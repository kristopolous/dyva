# Size-aware host routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Learn each `(host, model)`'s proven prompt-size capacity and route large requests by it, so a prompt never re-cascades through hosts already shown to nope out at that size.

**Architecture:** One new persistent column `max_ok` in `host_status` (largest prompt successfully processed); the existing `ctx_limit` column additionally fed by 0-char-length drops (lowered toward the failed size, self-healing via the existing `clear_ctx_limit`); and a size-aware band ordering added to `_race_servers` after its existing `ctx_limit` fit-check. The banker is untouched (it stays pure/ephemeral); all size state lives in `host_status` and is consulted exactly like `ctx_limit`.

**Tech Stack:** Python, sqlite3, pytest. Single-module package `dyva/__init__.py`; data layer in the same module beside `mark_ctx_limit`/`_load_ctx_limits`.

**Spec:** `docs/superpowers/specs/2026-11-03-ctx-size-routing-design.md`

## Global Constraints

- Do NOT modify the num_ctx sizing/resize path (`_auto_num_ctx`, truncation-retry doubling, window-grow-on-length). This plan is routing + one column only.
- Size failures carry NO reputation penalty and NO new reputation state.
- All thresholds are routing-config settings: `CTX_CONFIDENT_FRACTION` default `0.6`.
- `ctx_limit` evidence only ever LOWERS the ceiling; `max_ok` evidence only ever RAISES it. Both self-heal (ctx_limit via `clear_ctx_limit` on success; max_ok by growing).
- The banker (`dyva/banker.py`) is not modified.

## Review Focus

- OpenAI-dialect hosts report no token usage → `max_ok` recording must reuse `account_tokens`'s existing `prompt_est` fallback, not silently skip. (Task 2)
- A host whose `ctx_limit` was lowered by an empty-length drop must RECOVER when it later succeeds at ≥ that size — the existing `clear_ctx_limit` must cover the empty-length-derived value. (Task 2)
- Small/unknown-size traffic must be unaffected: with a small `need` or unknown `max_ok`, the band order must be a no-op that preserves the incoming order. (Task 3)
- All-too-big pool: if every surviving host is band 2 (or none fits `ctx_limit`), the request must still be raced (last resort), never dropped. (Task 3)
- A bad/unreachable host (`prio >= TIER_BAD`) must never be promoted by a good `max_ok` band. (Task 3)

---

### Task 1: `max_ok` persistence

**Files:**
- Modify: `dyva/__init__.py` — add column at the `ALTER TABLE host_status ADD COLUMN` migration loop (~2838); add `mark_max_ok` and `_load_max_ok` beside `mark_ctx_limit`/`_load_ctx_limits` (~3636–3699).
- Test: `tests/test_ctx_size_store.py`

**Interfaces:**
- Produces: `mark_max_ok(host: str, model: str, tokens: int) -> None` (upsert; `max_ok` becomes `max(existing, tokens)`; no-op for falsy `tokens`); `_load_max_ok() -> dict[tuple[str,str], int]` (non-NULL rows only, gated by `_LIVE_REAL`, mirroring `_load_ctx_limits`).

- [ ] **Step 1: Write the failing tests**

```python
def test_mark_then_load_roundtrips():            # mark 70000 -> _load_max_ok()[(H,M)] == 70000
def test_max_ok_only_grows():                    # mark 70000 then 40000 -> still 70000
def test_falsy_tokens_is_noop():                 # mark 0 -> (H,M) absent from _load_max_ok()
```
Use the in-memory `host_status` setup idiom from `tests/test_reputation.py` (`_db`, `monkeypatch.setattr(dyva, "_status_db", conn)`), with `max_ok INTEGER` added to the CREATE and `_LIVE_REAL` stubbed true.

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_ctx_size_store.py -v`; Expected: FAIL (`mark_max_ok` undefined).

- [ ] **Step 3: Add `"max_ok INTEGER"` to the migration column tuple and implement `mark_max_ok`/`_load_max_ok`.** `mark_max_ok` SQL mirrors `mark_ctx_limit` but `ON CONFLICT(host,model) DO UPDATE SET max_ok=MAX(COALESCE(max_ok,0), excluded.max_ok)`. `_load_max_ok` mirrors `_load_ctx_limits` selecting `max_ok` where `max_ok IS NOT NULL AND {_LIVE_REAL}`.

- [ ] **Step 4: Run to verify they pass** — `pytest tests/test_ctx_size_store.py -v`; Expected: PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(routing): persist per-host max successful prompt size"`

---

### Task 2: record the two signals

**Files:**
- Modify: `dyva/__init__.py` — `account_tokens` (~1903, after `clear_ctx_limit`); the empty-length branch (~8157, beside `mark_empty_length`); add `mark_overflow` beside `mark_ctx_limit` (~3651).
- Test: `tests/test_ctx_size_record.py`

**Interfaces:**
- Consumes: `mark_max_ok`, `_load_max_ok` (Task 1); existing `mark_ctx_limit`, `clear_ctx_limit`, `_load_ctx_limits`.
- Produces: `mark_overflow(host: str, model: str, prompt_tokens: int) -> None` — lowers `ctx_limit` toward a failed size: `ON CONFLICT DO UPDATE SET ctx_limit=MIN(COALESCE(ctx_limit, excluded.ctx_limit), excluded.ctx_limit)` with the stored value = `prompt_tokens` (no-op for falsy).

- [ ] **Step 1: Write the failing tests**

```python
def test_account_success_records_max_ok():       # account_tokens with p=70000 -> _load_max_ok()[(H,M)] == 70000
def test_openai_dialect_uses_prompt_est():        # obj has no usage, worker prompt_est=70000, speaks_openai -> max_ok 70000
def test_overflow_lowers_ctx_limit():             # mark_overflow(H,M,100000) -> ctx_limit == 100000
def test_overflow_never_raises_a_lower_ceiling(): # ctx_limit=32768 then mark_overflow 100000 -> still 32768
def test_success_clears_overflow_ceiling():       # ctx_limit=100000 (from overflow) then account p=100000 -> ctx_limit NULL (clear_ctx_limit)
```

- [ ] **Step 2: Run to verify they fail** — Expected: FAIL (`mark_overflow` undefined; `max_ok` not recorded).

- [ ] **Step 3: Implement.** Add `mark_overflow` (above). In `account_tokens`, after the existing `clear_ctx_limit(host, model, p)`, add `mark_max_ok(host, model, p)` (same `p`, which already carries the OpenAI `prompt_est` fallback). In the empty-length branch, after `mark_empty_length(host, full)`, add `mark_overflow(host, full, (_workers.get(job_wid) or {}).get("prompt_est"))`. `clear_ctx_limit` already NULLs any `ctx_limit <= ingested`, covering the overflow-derived value — no change there.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat(routing): record max_ok on success and lower ctx_limit on 0-char length drops"`

---

### Task 3: size-aware band ordering in the race

**Files:**
- Modify: `dyva/__init__.py` — add `CTX_CONFIDENT_FRACTION` constant (~near `NUM_CTX_*`, ~7320) and its settings load/save (`load_settings`/`save_settings`/`handle_settings_*`); add pure helpers `_size_rank` and `_order_by_size`; call `_order_by_size` in `_race_servers` right after the existing `ctx_limit` fit-check (~6880).
- Test: `tests/test_ctx_size_routing.py`

**Interfaces:**
- Consumes: `_load_max_ok` (Task 1); `TIER_BAD`.
- Produces: `_size_rank(need: int, max_ok: int | None, frac: float) -> int` — `0` if `max_ok >= need`, `1` if `max_ok >= frac*need`, else `2`; `max_ok None` → `2`. `_order_by_size(servers: list[tuple[int,str,list]], need: int, max_ok: dict[tuple[str,str],int], frac: float) -> list` — stable-sorts by `_size_rank` of the host's best model, but pins any `prio >= TIER_BAD` host to keep its position relative to other bad hosts (never promoted); `need <= 0` or empty `max_ok` returns `servers` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_size_rank_bands():                 # (100k, 100k)->0, (100k, 70k)->1, (100k, 40k)->2, (100k, None)->2
def test_order_band0_before_band1_before_band2():   # proven-100k host leads a proven-70k, which leads an unknown
def test_within_band_order_preserved():     # two band-0 hosts keep incoming order (warmth/reputation survives)
def test_bad_host_never_promoted():         # a TIER_BAD host with max_ok>=need stays behind non-bad hosts
def test_small_need_is_noop():              # need=0 -> servers returned unchanged
```

- [ ] **Step 2: Run to verify they fail** — Expected: FAIL (`_size_rank` undefined).

- [ ] **Step 3: Implement `CTX_CONFIDENT_FRACTION = 0.6`, its settings plumbing, `_size_rank`, and `_order_by_size`.** In `_race_servers`, after the `ctx_limit` block, add `servers = _order_by_size(servers, _need, _load_max_ok() if _pt else {}, CTX_CONFIDENT_FRACTION)`. `_order_by_size` uses each host's best (lowest) `_size_rank` over its models as a primary stable key, leaving `prio >= TIER_BAD` hosts unmoved relative to the sunk group — same discipline as the warm-first sort above it.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat(routing): band candidates by proven prompt-size capacity in the race"`

---

### Task 4: full suite + the message already reworded on main

- [ ] **Step 1:** Run `pytest -q --ignore=tests/test_banker.py` (worktree); Expected: PASS except the two pre-existing `test_stats_sqlite.py` `cost`-key failures (baselined, not ours).
- [ ] **Step 2:** Port the already-made activity-message reword (the empty-length line, `dyva/__init__.py:8159` on `main`) onto this branch so the branch is self-consistent, OR note it as a separate main-tree change to merge. Decide at review.
- [ ] **Step 3: Commit** any port — `git commit -m "chore: carry empty-length message reword onto the branch"`
