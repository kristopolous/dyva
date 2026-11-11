# brainstorm/ — design notes index

Working notes for features that are **designed or half-built but not finished**. Each
file is a thinking-out-loud document, not a spec or a commitment; several record
decisions that were later revised in place, so trust the newest line in a file over
an older one. This directory holds only what's still **outstanding** — things that
shipped have graduated out of here (into the code and `docs/`).

## Status at a glance

| doc | status | one line |
|---|---|---|
| [honeypot-detection.txt](honeypot-detection.txt) | Signal A **built**, Signal B parked | detect cloned-honeypot hosts (fingerprint + behavioural) |
| [honeypot-score.md](honeypot-score.md) | flag **built**, scoring **designed** | escalate honeypot evidence to a whole-machine flag |
| [canary-detection.md](canary-detection.md) | idea (data-collection only) | find hosts whose connect trips a Cloudflare soft-ban |
| [graflex-sample.md](graflex-sample.md) | planned, **lower priority** | a publishable rotating "sample platter" of hosts |
| [dumb-gateway-split.md](dumb-gateway-split.md) | idea | split dyva into a dumb host-racing gateway + a light app tier that owns chat features & detachable jobs |
| [skip-to-next-host.md](skip-to-next-host.md) | designed | restore the per-worker "skip" control — abandon a stalled host for the next, connection never dropped, partial toolcalls truncated to invalid |
| [auth-required-hosts.md](auth-required-hosts.md) | idea | recognize 401 "API token required" (LM Studio/Bearer) and quarantine the host instead of re-racing it |
| [self-update-tab.md](self-update-tab.md) | designed, knobs open | a tab of packaged updater-agents that refresh web-sourced reference data (pricing, evals…) over dyva's own pool, feeding **alias routes** (`=image-edit`) |

Status legend: **designed** = written down, ready to build, not built · **parked** =
deliberately on hold with a named trigger to revisit · **idea** = early thinking,
explicitly not being built. (Anything that reached **built** has left this directory.)

### Shipped since this index last tracked them
- **Gossiper** — dyva↔dyva sync of smoke-test verdicts — is built (server + client +
  batched sync). It lives in the code and in [docs/gossiper.md](../docs/gossiper.md).
- **Query syntax** — the model-name grammar and the `;`-facet filter language
  (metric + `key=value` on `host_status`: `;tps>10`, `;state=good`, `;ttft<0.5`) are
  in `find_servers` and documented in `dyva/README.md`. One remnant is still unbuilt:
  **enrichment facets in the routing query** — `;country=JP`, `;provider=aws`,
  `;service=ollama`. Those fields live on the pool record, not `host_status`, so the
  facet compiler doesn't know them; today they only filter in the map/compare view and
  via the cloudskipper. Also open from the original north-star: negation
  (`;country!=JP`), relative durations (`;fresh<7d`), and a `;has=vision` caps facet.
- **Host cross-reference** — hunting new survey sources by reverse-looking-up our own
  hosts / honeypot-signature model names on GitHub — is built as `tools/crossref.py`.

---

## Theme 1 — keeping the tool in the clear (safety / not touching bad hosts)

The survey touches machines other people left open. Two hazards: **honeypots**
(fake endpoints that bait or fingerprint us) and **canaries** (hosts whose mere TCP
connect gets our egress IP soft-banned by Cloudflare/OVH). These notes are about
*detecting and avoiding* both — and about the governing stance that the project stays
"in the clear" by not being loud and not distributing hosts.

### [honeypot-detection.txt](honeypot-detection.txt) — Signal A built, Signal B parked
Detecting cloned-honeypot hosts. The core insight is **constructive symmetry**: any
behavioural question you build to catch a fake, the adversary builds a machine to
answer — so behavioural probes are a losing arms race. The escape is *information
destruction* and *negative testing* (assert bogus, never assert good).
- **Signal A — timestamp fingerprint (BUILT as graflex `score-bogus`)**: a cloned
  disk image reuses the same `(model, nanosecond-timestamp)` across many hosts; a real
  host pulls independently. Validated — 6 fingerprints each shared by 209–243 of 688
  hosts. Consumed by routing as a *deprioritize*, never a ban. This is the only
  structural signal with teeth.
- **Signal B — nonsense→canned negative test (PARKED)**: send dictionary-word
  nonsense; flag only *canned/invariant* replies as bogus. A tripwire for lazy
  fixed-canned fakes, easily beaten by any varied-text generator — so it stays parked;
  build only if a fingerprint-0 host keeps returning canned soup (not observed yet).
- Open thread: per-host vs per-`(host,model)` — a host can serve a fake `codellama`
  *and* a real `llama3`, so the fingerprint flags per-`(host,model)` and behaviour
  arbitrates; host-level is at most a soft tiebreaker.

### [honeypot-score.md](honeypot-score.md) — flag built, scoring designed
Judge the *machine*, not the model: enough honeypot evidence → flag the whole host so
`find_servers` drops it for every model. The **host-wide flag mechanism exists**
(the `HONEYPOT_KEY` sentinel row + `mark_honeypot`, same pattern as `UNREACHABLE_KEY`,
additive and reversible). What's still **design** is the *accumulated score*:
`honeypot_score = bogus_score + Σ(runtime signal weights)`, derived from an
append-only signal log, crossing a threshold to set the flag. Hard invariants:
additive-only, reversible, provenance kept, schema changes additive, validated on a
copy before any live run. Open: the weights and threshold.

### [canary-detection.md](canary-detection.md) — idea, data-collection only
Some hosts are canaries: *connecting* to them (the soft-ban fires on the TCP connect,
not a completed request) drops our egress IP into a temporary Cloudflare/OVH ban pool.
Plan: log every touch with a timestamp, periodically probe a CF-fronted site for our
block state, and later correlate block events back to the hosts touched just before.
Per-egress-IP (graflex and dyva are separate machines). **Scope is deliberately data
collection only** — no attribution/scoring/remediation until there's data. Once
canaries are known, the sink is the existing cloudskipper (drop them before connect).

---

## Theme 2 — distribution & sync (the hard part: sharing without exposing)

> **Priority note (from the operator):** the sample platter is currently **lower
> priority**. The project stays in the clear precisely *because it does not distribute
> hosts* today — that restraint is load-bearing, not an oversight. So anything that
> publishes hosts (the sample platter) is a deliberate, not-yet-needed step, weighed
> against that posture. (The gossiper, which shares *verdicts* not host lists, shipped —
> see [docs/gossiper.md](../docs/gossiper.md).)

### [graflex-sample.md](graflex-sample.md) — planned, lower priority
A `graflex sample` subcommand that draws a small **stratified, rotating** subset
(defaults: 8 image / 8 video / 4 edit / 4 music / 3 speech / 30 text, from
proven-responsive hosts) and writes a new `graflex-sample.json` — a "sample platter"
so someone can clone the demos and get generation working **without** shipping tens of
thousands of hosts and **without** a curated winners list (which would concentrate load
and accelerate discovery of those machines). Destroys nothing (read-only inputs, one
new output, not committed to git); re-running rotates exposure. Consumed by dyva via a
pluggable `--pool <path|url>` — the same slot the gossiper URL drops into.

---

## Theme 3 — the shape of the system (architecture)

How the responsibilities are drawn, not what any one feature does. The governing posture
is that dyva has stayed **painless and hassle-free** (clone-and-run, no infra, no service
to babysit) — any architectural move is judged first against *keeping* that.

### [dumb-gateway-split.md](dumb-gateway-split.md) — idea, not committed
dyva is quietly two things: a host-racing **gateway** and a chat **application** (web UI,
spreadsheet, screen-grab, stats, media orchestration). The idea is to name that seam and
pull the app features up into a separate light tier, leaving dyva a dumb, stateless,
restartable gateway whose one hard job is talking to hosts correctly and politely. The
forcing function is **detachable jobs** (survive a phone screen-off — web workers can't,
the page freezes; the real answer is a server-side job with reconnect-by-id). The catch
that survives any version: detaching the job from the client socket throws away the
automatic reaper, so you owe back an explicit lease/deadline/TTL **and** cancel-propagation
to the host, or you leak held host connections. Hard constraint: **no heavy brokers**
(Postgres/docker/Temporal) — lift the pattern, not the stack; for one operator the job
layer is a dict + sqlite + an asyncio reaper. Open keystone, still **unverified**: does
dyva abort the upstream today when a client disconnects mid-stream?

---

## Theme 4 — operator control over live routing

Manual levers a human can pull on an in-flight request, for the cases the automatic
machinery can't adjudicate from the outside.

### [skip-to-next-host.md](skip-to-next-host.md) — designed, not built
Restore the per-worker **skip** control that fell away (UI button cut on purpose; the
`?wid=` endpoint and the racing-phase `_skip` are still live, but the handle no-ops once a
worker commits to streaming). Skip = abandon this host, go to the next; **content is
undefined, always**, but the **client connection is never dropped** (the whole difference
from stop). Human-triggered because the machine can't tell a stall from a legit slow
toolcall (`sleep 100000`). The load-bearing rule: a mid-toolcall skip must **truncate the
partial call to *invalid*** in both directions — never emit `finish_reason: tool_calls` to
the client, and drop the partial from the next host's context — or you ship a half-streamed
`DELETE FROM users`. Open pivot that sizes the build: does `_stream_chat_failover` already
keep the client stream open across a *post-token* host switch, or only pre-first-token?

---

## Theme 5 — hosts we can't use (classification)

Honest servers that aren't bad or fake but that we can't route to, and shouldn't keep
re-racing as if a retry might work.

### [auth-required-hosts.md](auth-required-hosts.md) — idea, not built
An LM Studio host with auth on 401s every request ("API token required", Bearer scheme) —
and was seen being **re-raced twice in 5s across two workers** instead of quarantined. It's
not a bad host or a honeypot, just gated. Proposed: recognize the 401 token-required signal
and quarantine the host (per-host — the token gates the whole server), same additive/reversible
shape as `UNREACHABLE`, so routing stops offering it. Open: how dyva classes a 401 today
(it's clearly retryable now), and whether to reuse `UNREACHABLE_KEY` or a dedicated reversible
`AUTH_WALL` marker.
