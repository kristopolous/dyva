# The Gossiper

The gossiper is how independent dyva instances share **inference-time survey data** —
primarily the smoke-test verdict of "is this a real inference endpoint, or a
honeypot?" — without a central registry, without replication, and without widening
who can see a given host.

Graflex (the survey tool) already solves *discovery* distribution: its host lists are
public artifacts a dyva pulls on `/refresh`. But dyva also learns things at inference
time that graflex never captures — most importantly that a host answered with canned
garbage or grabbed a bait tool, i.e. it's a honeypot. That knowledge is expensive to
re-derive (you have to contact the host, and contacting a honeypot is the hazard) and
is worth sharing. The gossiper is that two-way channel.

This document describes the protocol and the implementation. The design rationale that
produced it lives in the git history of this file (it began as a brainstorm).

---

## The load-bearing idea: `channel = hash(source url)`

Every exchange happens on a **channel derived from a graflex source's URL**:

```
channel = sha1(source_url)
```

The source URL does three jobs at once:

- **Rendezvous** — two instances that share a source hash its URL to the same channel.
  No channel registry, no names to assign or collide.
- **Capability** — you cannot find, subscribe to, or enumerate a channel without the
  URL to hash. The public server can't be scraped for "what sources exist."
- **Correctness + privacy boundary** — you can only sync on sources you *have*, so you
  structurally only ever exchange status about the host population you and your peer
  *both* survey. Critically, this **dissolves the host-IP leak**: the capability that
  lets you join a channel (knowing the URL) is the same capability that already grants
  you those hosts (fetching them from the source). So

  ```
  channel audience  ⊆  holders of the source URL  ⊆  people who already have those hosts
  ```

  A host IP only reaches someone who could already pull it from the source directly.
  Sync exposure ≤ source exposure, per source, automatically — public sources leak
  nothing new, semi-private ones stay bounded to URL-holders. It's "top shelf," not
  cryptographic: the URL is a bearer capability, but it's the *same* boundary the
  source already has.

---

## Scope: what is shared

v1 shares the **smoke-test verdict only** — the durable *nature* of a host, not its
moment-to-moment liveness:

| verdict | meaning | grain | how it's used by a receiver |
|---|---|---|---|
| `pass` | answered the smoke probe straight — a real inference endpoint | per `(host, model)` | **fill-blank positive lead**: try it ahead of cold-unknown hosts, but only where you hold no verdict of your own |
| `honeypot` | canned/fake reply **or** grabbed a bait tool — an unsafe endpoint | host-wide (`model=''`) | **trusted skip, no contact** — inherited, never re-verified |

Deliberately **not** shared: `up`/liveness (transient — up now ≠ up in five minutes)
and `down`/`timeout`/unreachable (flaky, false-negative-prone). Connection state is
noise; the smoke verdict is signal.

A "canned" smoke failure (`fail_smoke=1`) **is** a honeypot — it's the older,
per-model way the "unsafe endpoint, don't go back" verdict was recorded before the
host-wide sentinel existed. It is folded into the `honeypot` emission, not treated as
a separate, milder class.

### Why honeypots are the one negative we inherit

The general rule is *establish negatives yourself* — a timeout or a 503 is flaky and
cheap to recheck, so you never skip a host on a peer's say-so. The honeypot is the
exception: re-verifying it means **sending it a request**, which is exactly walking
into the bait/RCE trap the flag exists to avoid. Contact *is* the hazard, and a
honeypot won't reform, so we trust the peer's flag and skip without contact. (See
[survey.md](survey.md) for how honeypots are detected in the first place.)

---

## The protocol

A plain **bidirectional timestamp-delta over one POST**, piggybacked on `/refresh`.
No streaming, no subscriptions, no server-side policy.

### Wire contract

```
GET | POST   <gossiper_url>?source=<sha1 hex>&since=<int cursor>

POST body (JSON):  {"since": <int>, "rows": [{"host","model","smoke","ts"}, ...]}
  - POST ingests `rows` then returns the delta; GET ingests nothing, just returns it.
  - `source` MUST be a 40-char sha1 hex (the channel / isolation key) or the server 400s.

Response (JSON):
  {"ok": true,
   "now": <cursor to persist and send back next time>,
   "rows": [ this source's rows with id > since,
             each {id, host, model, smoke, client_ts, src_ip, server_ts} ]}
```

- **The cursor is the server's autoincrement row `id`**, not a wall-clock time. It's
  monotonic, immune to clock skew, and avoids the "repeats in waves" a windowed
  `LIMIT` store would produce. `now` is the max id **actually returned** (never the
  table max), so a concurrent insert is picked up next sync instead of being skipped.
- **Clocks never need to agree.** The client windows its *outgoing* rows by its own
  clock; the server returns *incoming* rows by its own `id`; each side just echoes the
  other's last token verbatim.
- **`src_ip`** is recorded per row and returned in the delta so a receiver can store
  *who* attested each row — the hook for retro-invalidating a bad contributor.

### The two client-side cursors (per channel)

- `last_refresh_at` — the client's own clock at its previous sync. Bounds the
  **outgoing** window (which of my smoke results to send this time).
- `server_hwm` — the `now` the server last handed back. The cursor for the
  **incoming** delta.

Each `/refresh`, per source: POST `{rows: my smoke verdicts written since
last_refresh_at, since: server_hwm}`; apply the returned rows; persist
`last_refresh_at := now`, `server_hwm := returned now`.

---

## The server

A dumb, append-only SQLite log. Two interchangeable twins with an **identical
contract** live in `dyva/gossiper/`:

- **`gossiper.php`** — PHP/PDO, for Apache. The reference deployment is
  `~/www/dyva/gossiper/` → `https://9ol.es/dyva/gossiper/`.
- **`gossiper.py`** — stdlib `http.server` + `sqlite3`, zero dependencies. For running
  your own, and for testing the client against locally (`python3 gossiper.py`).

Schema (one table, no eviction — unbounded on purpose; a windowed store surfaces
periodic duplicates, which is worse than size):

```sql
gossip(id INTEGER PRIMARY KEY AUTOINCREMENT,
       source, host, model, smoke, client_ts, src_ip, server_ts)
```

Two operations: INSERT the POSTed rows (tagged with `src_ip` and `server_ts`), and
`SELECT ... WHERE source=? AND id>? ORDER BY id`. That's the whole server.

### Deploy notes

- Deploy as a **subdirectory** `gossiper/` with an `.htaccess` doing
  `DirectoryIndex gossiper.php` (clean URL via `mod_dir`, no `mod_rewrite` needed) plus
  a `FilesMatch` denying `*.sqlite*`, `.db`, and `.py` — the DB sits in the docroot, so
  without the deny it's one GET away.
- **Gotcha: POST must use the trailing-slash URL** (`/dyva/gossiper/`). The no-slash
  form 301-redirects (`DirectorySlash`) and a 301 turns POST into GET, silently
  dropping the body.

---

## The client (dyva)

All of the policy lives on the client, in `dyva/__init__.py` under the
`GOSSIPER CLIENT` banner. It runs **on `/refresh`** (`handle_refresh` →
`gossiper_sync`, off the event loop since it does blocking network I/O), "with
everything else" — the same pass that re-pulls the configured sources and lands new
hosts. There is no hot-path, fanout-time query: a pulled lead just enters the pool as
an ordinary candidate, so fanout needs zero gossiper-specific code. The trade is that
lead freshness is bounded by the `/refresh` cadence.

The whole sync is **best-effort** — a dead or slow gossiper never fails a refresh — and
gated by `GOSSIPER_ENABLED`.

### Storage (two tables in `host_status.db`)

Peer data is **quarantined** from our own measurements. Our probes (MEASURED) live in
`host_status`; peer rows (ATTESTED) live in a separate table, so a peer can never
overwrite what we measured and a bad contributor's whole output drops with one DELETE.

```sql
hosts_attested(host, model, verdict, attested_at, attester, source)
  -- verdict ∈ {'pass','honeypot'}; honeypot is host-wide (model='')
  -- attested_at = the original reporter's client_ts
  -- attester    = the src_ip the server tagged -> invalidate by it
  -- source      = the sha1(url) channel it arrived on

gossiper_state(source PRIMARY KEY, last_refresh_at, server_hwm)
  -- the per-channel cursors, so they survive restart
```

### Per-sync flow (`gossiper_sync`)

1. Group the freshly-reloaded pool by each host's `source` tag. This is the **leak
   boundary in code**: a host is only ever POSTed to *its* source's channel, so a
   private-source host never reaches a public channel.
2. For each enabled source (`_source_urls()` → name→URL), `channel =
   _gossiper_channel(url)`.
3. `_gossiper_outgoing(host_set, last_refresh_at)` builds the outgoing delta:
   - **honeypot** (host-wide) for any host carrying the `HONEYPOT_KEY` sentinel **or**
     any `fail_smoke=1` row;
   - **pass** (per-model) for `smoke_date` set & `fail_smoke=0`, **excluding** any
     unsafe host (a canned host is never advertised good, even for a model that looked
     fine).
4. `_gossiper_exchange(channel, server_hwm, out)` does the one POST and returns the
   delta (or `None` on any failure → cursor left untouched, retried next refresh).
5. `_gossiper_apply(rows, channel)` inserts the returned rows into `hosts_attested`
   (`attester` = `src_ip`).
6. `_gossiper_set_cursor(channel, now, returned_now)` advances both cursors.

### Routing integration (how attested data reaches `find_servers`)

dyva's router reads reputation as several sets (`good` / `maybe` / `bad` /
`unreachable`), richer than the prototype's pass/honeypot grain, so the attested data
is folded into those existing loaders rather than swapping in a monolithic view:

- `load_attested_honeypots()` is **unioned into the host-wide skip set** — an attested
  honeypot is treated exactly like a locally discovered one.
- `load_attested_pass()` is a **fill-blank positive lead** — it lifts an *unknown* host
  to `maybe_good`, and only when we hold no `good`/`maybe`/`bad` of our own. Our
  measurement always wins; a honeypot still wins host-wide above any pass.

**Visibility.** Because attested rows are quarantined, the host-card per-model
breakdown surfaces a peer's honeypot explicitly — a host we never measured shows as
failed-smoke **"synced"** (with an `attested` provenance flag) instead of "untested."
`host_status` is still never written.

### The canonical resolution model

`dyva/gossiper/resolution.sql` + `test_resolution.py` are the proven, isolated model of
the conflict resolution the loaders implement: a honeypot (measured or attested) is
one-way sticky and wins (trusted skip); otherwise most-recent wins (which, with
re-measure-on-use, reproduces fill-blanks). They stay the spec and the bridge if the
router's reputation is ever unified onto a single `host_resolved` view.

---

## Security properties

- **No new leak** — the namespace guarantees sync exposure ≤ source exposure per source
  (above).
- **Poisoning is bounded, not by re-verification** (which would defeat the point of a
  trusted honeypot skip) but by three structural limits: (a) only holders of the source
  URL can post to its channel, so an attacker is already a source participant, not the
  open internet; (b) `attester` (src_ip) is stored per row, so
  `DELETE FROM hosts_attested WHERE attester=?` retro-invalidates a bad contributor and
  the host recovers in the resolved view, scratching nothing we measured; (c) the blast
  radius of a single false flag is one host. If permanence ever bites, **age** attested
  rows out (re-admit after a long cooldown) rather than re-contacting.
- **Positive-leads-only on the positive side** — a `pass` can only fill a blank, so a
  peer can never gut a pool of things you actually know.

---

## Settings

Two runtime settings (module globals, persisted in `settings.json`, editable in the
dashboard Settings tab):

- `GOSSIPER_URL` — default `https://9ol.es/dyva/gossiper/` (trailing slash — see the
  POST gotcha). Point it at your own server, or clear it to disable.
- `GOSSIPER_ENABLED` — default **on**; the opt-out tick.

---

## Files & tests

| path | what |
|---|---|
| `dyva/__init__.py` (`GOSSIPER CLIENT` section) | the client: sync, extraction, apply, loaders, cursors |
| `dyva/gossiper/gossiper.php` | reference server (Apache/PHP) |
| `dyva/gossiper/gossiper.py` | portable zero-dep server twin |
| `dyva/gossiper/.htaccess` | `DirectoryIndex` + deny `*.sqlite*`/`.db`/`.py` |
| `dyva/gossiper/resolution.sql` | canonical quarantine schema + `host_resolved` view |
| `dyva/gossiper/test_resolution.py` | proof of the resolution rules (stdlib) |
| `tests/test_gossiper_client.py` | client logic: extraction, apply, cursors, routing, sync round-trip |

---

## Status & open items

Server and client are **built and in use**. The remaining known decision:

- **Unsafe hosts are currently `TIER_BAD`** (raced dead-last, so effectively skipped in
  any populated pool) rather than hard-dropped. Strict "skip a honeypot *without
  contact*" argues for a hard drop (never a candidate) for both local and attested
  unsafe hosts — a small, deliberate routing change not yet made.
- Transport is pluggable; the timestamp-delta-over-HTTP above is v1. NATS JetStream
  (per-subject retention = the recency store for free) remains a reference backend
  option if a shared transport is ever wanted; the namespace-by-URL property holds
  regardless, because the sync URL lives in the source definition.
