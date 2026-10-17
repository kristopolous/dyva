# stats-storage — making the Stats view scale

**Status: brainstorm, no decision, no code.** Captured to return to later.

## The problem

`handle_stats` reads the entire append-only `stats.jsonl` and parses every line as
JSON on *every* page load, then aggregates in Python into days / weeks / hours(168h) /
per-model / per-host / totals (+ an optional `q` substring-or-glob filter on
model-or-host). That whole-file serial scan now takes a few seconds and only gets
worse as the log grows.

It's about to get worse faster: the decision-model (`/v1/systemone`) token accounting
we just added writes a row **per request**, and decision calls are small and very fast
— so they multiply the row count quickly (running decision jobs overnight ~tripled
total requests). A night of that is what surfaced this.

**This is purely a presentation-layer problem. We are NOT deleting data in any design.**
The question is only how the read path is organized.

## Two candidate designs (both keep all data)

### A. Rollup + tail (a materialized summary)
`stats-rollup.json`: a byte `offset` into `stats.jsonl` + `rows` keyed by
`(day, model, host)` → `{up, down, reqs, tps_sum, tps_n, last}`. On load, `seek(offset)`,
fold records older than 168h into the rows and advance `offset`; keep the last 168h as
raw tail. Page = rollup rows + tail folded on top; hours come from the tail (always
⊇168h). Output byte-identical to today; the `q` model/host filter still works because
rows are keyed by model+host.

- **Win:** fast; re-scanned tail bounded to ~7 days, not all-time.
- **Cost (the catch):** it is **lossy for ad-hoc queries**. Once requests are folded
  into `(day, model, host)` sums, the aggregation dimensions are baked in forever — you
  can never later ask "requests where tps > 20", filter by `title`, or slice by any
  field that isn't already a rollup key. The rollup optimizes for *today's exact view*.

### B. SQLite (kristopolous' lean)
Move `stats.jsonl` → a SQLite table with an index on `date` (and maybe model/host).
Indexed `GROUP BY` in C is tens of ms over millions of rows vs. Python parsing the whole
file. Keeps every raw row (full fidelity) and preserves (even gains) arbitrary ad-hoc
queryability. One-time import of the existing `.jsonl` (same move already made for
graflex → `graflex.db`); `_write_stat` becomes an indexed insert; WAL + `busy_timeout`.

- **Schema restraint (the adage):** only make COLUMNS for fields you actually run SQL
  operations on — `date`, up, down, tps, maybe model/host — and store the rest of the
  record as a JSON blob column. Don't normalize into a pile of columns you'll never
  `WHERE`/`GROUP BY` on.
- **`q` filter:** `LIKE '%q%'` covers the common substring case; the fuller glob
  semantics either get approximated or kept as a Python match over the (small,
  pre-aggregated) model/host rows. A conscious minor change, not a silent regression.

## The real open question

**What utility do the ad-hoc queries (B preserves, A drops) actually give?** If the
answer is "not much," then A's permanent loss of them is fine and A is simpler. If we
expect to want "slice stats by any field" later, B is the one that doesn't paint us
into a corner. This is the crux and it's undecided.

## Why JSONL isn't obviously worse

JSONL has a genuine operational benefit SQLite doesn't: **archival is trivial.**
`wc -l`, take half, `head`/chop, zip it, done — line math and byte-offset surgery are
free on a flat append log. Rotating/archiving a SQLite DB is more ceremony. Both
formats have real benefits; this isn't a clean "SQLite strictly wins."

## Not deciding yet

Leaning SQLite (full fidelity + fast + the column/JSON-blob restraint keeps it cheap),
but the ad-hoc-utility question above is unresolved and the JSONL archival ergonomics
are a real counterweight. Revisit before building. No schema, no migration, no code
written.
