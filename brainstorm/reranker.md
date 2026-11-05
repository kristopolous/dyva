# Reranker — temporary, expiring reorder layer (brainstorm — BUILT)

Status: built 2026-11-05. Replaced the session prefer/skip sets + `favor_host`. Code in
`dyva/__init__.py` (`_get_reranker_db` / `add_rerank` / `rerank_scores` / `_rerank_prio`,
wired into `find_servers` + `_image_pool` + the two 503 sites + `/favor-worker` /
`/flag-worker`); tests in `tests/test_reranker.py`. Takes effect on next dyva restart.

## Thesis

The reputation tiers are the long-memory, self-healing truth. On top of them we want a
**short-lived, reversible reorder** for *transient* states — a host that's slammed right now,
a human who just voted — that **never touches reputation** and **expires on its own**. One
mechanism, sqlite-backed so it survives a restart, keyed by host, append-only.

This supersedes three things that already do this job worse (in-memory, lost on restart,
binary, and in one case a reputation write):

- `_session_prefer_hosts` → `TIER_PREFER` (👍 floats above all tiers) — session-scoped, binary.
- `_session_skip_hosts` → hard-drop in `find_servers` (👎 removes the host) — session-scoped.
- `favor_host(host, model)` — the one reputation write in the vote path (lifts a demoted host
  into 'good' for a re-measurement, and clears an UNREACHABLE mark). Dropped entirely.

## The table — its own `reranker.db` (never near `host-status.db`)

    CREATE TABLE IF NOT EXISTS reranker(host TEXT, direction INTEGER, expire_at REAL)

Append-only. **No DELETE, no GC** — at ~2 rows/day the table is a rounding error for years;
a human can prune by hand if they ever care. This keeps dyva 100% additive (no destructive
SQL anywhere — this was going to be the first, and we dodged it).

`host` is stored `_norm_host()`-normalized (matches how routing compares).

## Apply — after the tiered ranking, a final reorder

    SELECT host, SUM(direction) AS net FROM reranker WHERE expire_at > now GROUP BY host

(`> now` = still-active. Expired rows are simply ignored, never deleted.)

- `net > 0` → float toward the **front** of the whole eligible candidate list (`TIER_PREFER`),
  higher net first (`+2` ahead of `+1`).
- `net < 0` → sink toward the **back** (last-resort, still a candidate — busy-beats-none),
  lower net further back.
- `net == 0` → untouched, keeps its earned tier order.

Reorders only the **already-eligible** set (selection/unreachable filtering runs first), so a
👍 can't resurrect a dead host and a 👎 can't fully exclude a sole host. Net-sum gives a nice
freebie: a 👍'd host that's currently busy nets 0 → drops to its normal tier while slammed,
pops back to front when the busy row expires.

Fed into **both** text routing (`find_servers`) and image routing (same `_norm_host` lookup
routing already uses) via one shared helper — image == text.

## Writers (durations in settings.json, these are defaults)

| trigger | direction | ttl | default secs |
|---|---|---|---|
| host returns 503 busy (chat/text path) | −1 | ~30 min | 1800 |
| 👎 `/flag-worker` | −1 | ~12 h | 43200 |
| 👍 `/favor-worker` | +1 | ~12 h | 43200 |

## Two conscious behavior shifts (okayed)

- **A. 👎 softens from hard-drop → sink-to-back.** Was `_session_skip_hosts` (host gone till
  restart); now a −1 that sinks it for 12 h but keeps it as a last resort. A −1 is a −1,
  same as busy; matches the existing `down_voted → max(prio, TIER_BAD)` semantics.
- **B. 👍 no longer force-clears an UNREACHABLE cooldown.** `favor_host` used to `DELETE` the
  dead-socket mark so a cooling host re-raced immediately. The +1 floats any *reachable*
  candidate (good/maybe/unknown/bad all stay in the list) but won't bypass the 10-min
  circuit breaker — it floats once the host half-opens. Better: don't force traffic at a
  dead socket.

## Scope

- Chat/text + decision (`/v1/systemone`) 503 busy wired. Media `ComfyBusy` not penalized yet.
- `favor_host` def + call removed (tombstoned). `_session_skip_hosts`/`_session_prefer_hosts`
  removed (tombstoned).
- Legacy `votes`/`set_vote`/`down_voted` (find_servers) confirmed **dormant** — nothing has
  called `set_vote` since the handlers moved off it, so `_load_votes()` is always empty and
  the `down_voted` branch is a no-op. Left in place (out of scope; harmless), not ripped out.
