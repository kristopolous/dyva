# Host-affinity cache — keep a large context resident on its host (BUILT)

Status: designed 2026-11-07, **built TDD same day**. Mirrors the `reranker` pattern (a soft,
expiring reorder ON TOP of the reputation tiers that never touches reputation). Tests:
`tests/test_host_affinity.py` (19, green). Takes effect on next dyva restart. See
`brainstorm/reranker.md` for the sibling it's modelled on.

## Code (as built), all in `dyva/__init__.py`

- Constants `AFFINITY_PREFIX_MSGS / _MIN_TOKENS / _TTL_SECS / _MAX_ENTRIES / _FLOOR_HOSTS`
  (beside the `RERANK_*` constants), wired into `load_settings` / `save_settings`.
- Store: `_affinity` (in-memory dict) + `_affinity_ctx` (contextvars). `_affinity_key`,
  `_affinity_size_ok`, `affinity_touch`, `affinity_view`, `_affinity_plan` (pure),
  `_affinity_candidates`, `_affinity_touch_ctx` — next to the reranker store.
- Apply: in `_find_servers_raw`, a post-loop pass folds affinity (match→+1 front, foreign
  reservation→−1 back, floor-bounded) THEN the reranker fold (voting last = wins), before the
  sort. One shared `_rerank_prio` primitive for both.
- Plumbing: `_proxy_chat` sets `_affinity_ctx` to `(key, size)` right before `find_servers`
  (size = cheap chars/4, NOT a tiktoken encode — that would block the loop on big prompts).
  `record_verdict` calls `_affinity_touch_ctx(host)` on `V_ACCEPTED`. No race/worker signature
  changed — the contextvar is request-scoped and inherited by the child race tasks.
- NOTE vs the original design: honoring is NOT size-gated (any chat request reorders around
  reservations — cheap insurance); only CREATION (`affinity_touch`) is size-gated. Image
  routing (`_image_pool`) is NOT wired yet — text/chat only for now (that's where the big
  contexts live). Full suite: 418 passed, 2 failed (the known `test_stats_sqlite` cost-key
  baseline), `test_banker.py` collection error ignored as always.

## The problem (why this exists)

Host KV / prefix cache is **host-local** — ollama/llama.cpp/vLLM keep the computed KV for a
prompt prefix in the memory of the one process that computed it; there is no shared cache
across hosts. The banker, by design, is host-agnostic: every request is raced / reputation-
routed / failed-over independently, with load spread across the pool. There is no thread→host
affinity anywhere, because per-request statelessness is the source of the routing wins.

Put those together and a **large context thrashes**. Observed in the wild: several live
threads at 20k / 54k / 74k / 120k tokens, all bouncing hosts. Every hop re-prefills the entire
KV from cold on the new host — tens of thousands of tokens of prefill, recomputed each time.
This is swap-thrashing in the textbook sense: an expensive resident working set that never
stays put, so it's perpetually evicted and recomputed. At 120k it dominates everything.

The fix is **soft affinity**: bias a thread back to the host that already has its prefix warm,
and protect that host from being cold-wiped by unrelated traffic. Soft, not a hard pin —
against a churning discovered pool you can never *guarantee* a host stays up for a thread's
life, so the ceiling is a preference, never a lock. Additive, like the reranker; if the whole
cache vanished, routing still works.

## The key — a crc over the first N user messages

    key = crc32( concat(first N user messages in the payload) )

- **N defaults to 4** (`AFFINITY_PREFIX_MSGS`, configurable — 3, 7, whatever; the value is
  noise). Use `first min(N, available)` so early turns degrade cleanly.
- **User messages only.** The human-authored spine is the stable part. Assistant turns and
  tool results churn every round; keying on them would change the key every hop. The first N
  user messages are identical across all of a thread's tool rounds AND across re-invocations of
  the same conversation — which is exactly the prefix that stays warm.
- **Hash choice is a rounding error.** Four short messages, hashed once per request, dwarfed by
  the LLM call. `zlib.crc32` is C-backed and instant — use it. (xxhash is faster and pointless
  here.) crc32's ~4B space makes a collision at ~40 entries negligible, and a collision is
  harmless anyway: an optimistic "match" sends the thread to a host warm only up to the
  divergence point, the host's prefix cache reuses the common head, the rest re-prefills. No
  worse than a cold host.
- **Ramp caveat (moot in practice):** the key only stabilises once ≥N user messages exist, so a
  thread's first N turns don't get affinity. Doesn't matter — creation is size-gated (below),
  and anything big enough to care is long past its first 4 messages.

## The store — in-memory L1, bounded, sliding TTL

    affinity: { crc -> { host, expire_at } }     # ~30-40 entries max (AFFINITY_MAX_ENTRIES), LRU

- **In-memory dict, NOT sqlite** (the one deliberate divergence from the reranker). Host KV is
  ephemeral — it does not survive a dyva restart or a host re-probe — so a persisted pairing
  would just hand out stale affinity on boot. Ephemeral state for an ephemeral fact is *more*
  correct here, not less.
- `host` = the last host to serve this crc (the one presumed warm).
- `expire_at` = `now + AFFINITY_TTL_SECS` (**default 300s**), a proxy for how long the host's KV
  stays hot. **Sliding:** refreshed every time the thread hits the host — a recent use just
  re-warmed the KV, so the reservation should extend, not expire on a fixed clock.
- LRU-evict when over `AFFINITY_MAX_ENTRIES`.

## Engage gate — only when a cold hop is expensive

Affinity is worthless for small prompts (cold-prefilling 2k costs nothing) and the whole point
is protecting big working sets. So **gate entry *creation* on prompt size**:

- Create / refresh an entry only when the prompt size estimate ≥ `AFFINITY_MIN_TOKENS`
  (**default ~8000**, a preference). Small threads never create entries.
- The size estimate is **free**: the banker already has to hold the messages to hash the prefix,
  so a cheap token estimate (chars/4 is fine for a gate) comes off the same data. No new
  plumbing beyond what the crc already needs. (A simpler message-count floor — "engage past M+3
  messages" — is supported by the same setting if ever preferred; size is the true cost axis.)
- **Honoring is universal, creation is gated.** Every request, big or small, honors existing
  reservations and takes a match if it has one. The asymmetry is entirely in our favor:
  deflecting a *small* request off a reserved warm host costs ~nothing and protects a 120k
  investment — nearly-free insurance.

## Apply — the banker's ordering, on top of tiers

Order of application (last applied = highest precedence):

1. **Tier rank** (reputation) — the base order, unchanged.
2. **Affinity reorder:**
   - the **matching** host (this crc's warm host), if it's an eligible candidate → **front**.
   - **reserved** hosts (holding a *foreign*, unexpired crc) → **back** (still candidates — soft
     demote, not exclusion), subject to the floor below.
3. **Voting / reranker** (`rerank_scores` + `_rerank_prio`) — applied last, so a 👍/👎 and a
   live busy −1 **override** affinity. Deliberate: explicit human intent and current busyness
   both beat a cache guess. (A busy warm host gets fronted by affinity then sunk by its busy −1
   → net not forced onto a slammed host. Correct.)
4. Hand the ordered list to the worker, which races / hedges / fails over down it as today. If
   the warm host is first and answers, it refreshes the TTL; if it's down, the worker falls
   through — soft affinity degrades gracefully.

### The reservation is the load-bearing half

"Match → front" is the obvious part. **"Foreign unexpired → back" is what actually preserves
the cache.** Without it, a warm host serving thread A gets grabbed for thread B, B's prefill
evicts A's KV, and A returns inside its own TTL to a cold host — the cache you were counting on
is gone. Holding a host out for foreign crcs is what makes the TTL *mean* anything.

### The floor — protect big cold-starts

Reservation can lock up the pool. The degenerate case is graceful (if *all* hosts are reserved,
"to the back" is a no-op and the request still lands on a good one — soft, not starvation). The
genuinely bad case is a **fresh large** context (no match yet) shoved onto a weak host because
every good host is reserved — cold-starting 120k on the worst host is the exact pain we're
fixing, just relocated.

So: **never reserve below `AFFINITY_FLOOR_HOSTS` good hosts** (**default ~5**, a preference).
Affinity may demote reserved hosts only down to that floor; big new threads always have decent
landing pads. When the cap binds, keep the **freshest-TTL** reservations (most recently warm =
most valuable to protect) and release the stalest.

## Settings (all configurable, defaults are guesses)

| setting | default | what |
|---|---|---|
| `AFFINITY_PREFIX_MSGS` | 4 | how many leading user messages form the key |
| `AFFINITY_MIN_TOKENS` | 8000 | engage gate — only create entries for prompts this big |
| `AFFINITY_TTL_SECS` | 300 | sliding KV-warmth TTL per entry |
| `AFFINITY_MAX_ENTRIES` | 40 | L1 cache cap (LRU) |
| `AFFINITY_FLOOR_HOSTS` | 5 | good hosts affinity may never reserve away |

Wire into `load_settings` / `save_settings` the same way the `rerank_*_secs` keys were.

Exposed in the dashboard: a **"Host matching"** settings card (after "Routing configuration"),
which also surfaces the `rerank_*` vote lifetimes (previously JSON-only). `handle_settings_get`
echoes the keys, `handle_settings_post` applies them live (read live → no restart to tune),
`populateSettings` / `saveSettings` in `dashboard.html` bind the fields.

## Scope / non-goals

- **Additive. Touches no routing primitive.** Reputation tiers, hedge, failover, the connection
  ceiling, the race — all unchanged. This is a reorder + a demote on top, exactly like the
  reranker. "We don't change the hard-win routing primitives."
- **Soft, never a hard pin.** A down/slow preferred host falls through to the rest.
- **Cross-agent shared-`context` co-location is explicitly NOT a goal.** You cannot both spread
  N parallel agents for throughput AND co-locate them on one host to share the context KV — the
  connection ceiling and fanout *want* them apart. The achievable win is per-thread continuity
  across rounds and re-invocations, which is what this delivers. The shared-context prefill
  amortization is only reachable if agents run sequentially on one host, which defeats the
  parallelism; out of scope.
- **Not persisted** (see store). Dies with the process, as it should.

## Plumbing seam (the one integration point)

The banker keys on query + caps today; it does **not** see message content. To compute the crc
(and the size estimate) it needs the request's messages. Decide at build time: either compute
`(crc, size)` in the request handler and pass them into `find_servers` / `_image_pool`, or pass
the messages down. Prefer the former — keep the banker's signature lean and hand it two scalars.

## Observability (activity feed)

A handout is visible in the activity log so you can tell affinity is firing and whether it
helps. `_affinity_handout` emits a `"affinity"` line at routing ("leading with the warm host")
with `aid="aff:<wid>"`; `_affinity_resolve` re-emits the SAME aid at the winning host's
`connected` point so the line RESOLVES IN PLACE to either "stuck - warm host served" or "warm
host fell through, served elsewhere". Both read `_affinity_match_host()` (the pre-touch match).
Feed colour: `.status-affinity` (violet) in `dashboard.html`. A low stick rate = affinity is
handing out hosts that then lose the race (dead/busy/slow) — worth watching.

## Build notes (for the TDD pass)

- Backend/Python → testable, unlike the front-end. Follow the reranker's test shape:
  `tests/test_host_affinity.py`.
  - store: create/refresh, sliding TTL, LRU eviction, expiry ignored-not-counted, size gate
    blocks creation under threshold.
  - pure reorder helper (mirror `_rerank_prio`): match→front, foreign-unexpired→back, floor
    respected, freshest-TTL kept when the cap binds, voting applied after wins.
  - `find_servers` integration: a matching crc fronts its host; a foreign reservation sinks a
    host but never below the floor; a 👎/busy on the warm host still overrides.
- Applied BEFORE the existing reranker fold in `find_servers` (tiers → affinity → reranker).
- Confirm the 2 pre-existing `test_stats_sqlite` cost-key failures are the only reds (documented
  baseline), as with the reranker build.
