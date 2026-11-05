# Size-aware host routing — design

## The problem

Large (agentic) requests "fall off the saddle." A host wins the race, spends
~30s chewing the big prefill, then returns `done_reason: length` with **0 chars
of output** (it can't hold a prompt this large). dyva drops it from *this*
request and re-races — onto the next host that also can't hold it. Live example:
a single request cascaded `218.75.72.234 → 115.159.71.16 → 157.180.109.161`,
and the *next* request repeated the identical doomed cascade, because those are
the reputation-top hosts for the model and **dyva learns nothing** between
requests. Each doomed host costs a full ~30s of wasted prefill; cascading
through several is where the minutes go. It is worse than sequential because the
race keeps re-selecting the same fast-winning hosts that then nope out.

The 0-char-length signal is already detected and already recorded — but
`empty_length_count` is explicitly **observational only** ("does NOT affect
routing"). The fix is to turn what we observe into what we route by.

## What we measure (two columns, two different measurements)

Per `(host, resolved-model)` in `host_status` (the persistent reputation DB —
NOT the banker, which is pure/ephemeral by design):

1. **`max_ok`** (new column) — the largest prompt (in tokens) this pair has
   **successfully** processed. Hard, positive evidence: we watched it work.
   Grows monotonically on success.
2. **`ctx_limit`** (exists) — the largest prompt the pair will **not** exceed.
   A soft "maybe," because it is load-dependent and reversible. Today it is fed
   only by explicit `400 exceed-context` rejections (which state an exact
   `n_ctx`). It will **also** be fed by a 0-char-length drop, which gives us
   "failed at size P" → lower the ceiling toward P. `ctx_limit` always takes the
   **more restrictive (lower)** evidence and is **self-healing**: `clear_ctx_limit`
   already NULLs it the moment the host later ingests ≥ the recorded size.

No new reputation state and **no reputation penalty**: a size failure is our
query overreaching, never the host's fault (same stance as truncation today).

## How we route (the banker's selection, size-aware)

For an incoming request whose needed window is `need` (the existing padded
estimate: `prompt_est × NUM_CTX_PAD + reply_reserve`), applied in the
`find_servers` fit-check that already orders candidates before the banker leases
them:

1. **Exclude** hosts with `ctx_limit < need` — unless none fit, in which case
   keep them as a last resort (existing `ctx_limit` behavior, unchanged).
2. Among survivors, **order by proven capacity**, highest-confidence first,
   never promoting a bad/unreachable host (`prio >= TIER_BAD`), existing order
   preserved within a band:
   - **Band 0 (certainly):** `max_ok >= need`.
   - **Band 1 (probably):** `max_ok >= CTX_CONFIDENT_FRACTION × need` (default
     **0.6**).
   - **Band 2 (unknown):** everything else → fall back to existing reputation
     order; the size metric is ignored here, not penalizing.

A 100k request thus never revisits the host that noped at 32k, tries the hosts
proven at ≥100k first, then those proven at ≥60k, then the rest by reputation.

## Constraints (carried from the num_ctx history)

- **Do not touch the num_ctx sizing/resize path.** This is routing/ordering and
  a new column only; `_auto_num_ctx`, the truncation-retry doubling, and the
  window-grow-on-length logic are unchanged.
- **Soft, reversible, self-healing** — a briefly-overloaded good host must not be
  benched permanently. `ctx_limit` self-heals on success; `max_ok` only grows.
- **The banker stays pure** (no I/O, no persistence). Size data lives in
  `host_status` and is consulted in `find_servers`, exactly like `ctx_limit`.
- **Tunables are settings**, not magic numbers: `CTX_CONFIDENT_FRACTION` is a
  routing-config key with a default of 0.6.
- **No reputation ding** for a size failure.

## Scope

**In scope (this plan):** the `max_ok` column + its record/load, feeding
0-char-length drops into `ctx_limit`, and the size-aware banding in
`find_servers`.

**Out of scope (phase 2):** surveying a host's *declared* context limit at
graflex probe time (e.g. ollama `/api/show` model params) and storing it in the
model dictionary as a cold-start prior, so we don't have to learn `need` the
hard way on first contact. Separable; noted for later.
