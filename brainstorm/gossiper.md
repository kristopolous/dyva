ok i want to talk about a feature i've been thinking about for a while now. The graflex/dyva divide is nice because the dyva runtime uses the graflex survey time data and so it naturally solves the distribution problem. However, dyva keeps these databases of additional information, such as if a host passes a smoke test and if it's a real endpoint ... this goes back to an issue I had with using sqlite to begin with, syncronizations ... anyway let me lay it out:

  1. I agree that sqlite is a reasonable store for these inference time survey methods
  2. Inference time surveying is valuable data that should be able to be distributed among the hosts
  3. This is inherently two-way, unlike the producer/consumer graflex model
  4. The bad survey problem might be irrelevant. A dyva host can do a fire-and-forget sync mechanism and if they're network is doing some mass false-negative failure they will have a very low chance of
  successfully firing and forgeting their phony update.
  5. There's many ways of doing this - crdt, cr-sqlite, or maybe some really cheap mechanism of a few endpoints that support like glob-model querys GET /realtime?source=spider&model="qwen2.5*" POST /realtime {sqlite row} ...
  6. this leaks host data so we can't just have a global system instead it has to be a graflex source ... currently we have { name, url, mapping } this would now need a { name, url, sync, mapping }
  7. the request and api should be namespaced. I know this isn't real protection ... we can talk about it.

There's only one absolutely positive thing I'd like to do right now: support a -c, --config where you can point to something other than $HOME/.cache/free-ollama ... this is the one split that prevents multiple dyva's from running on a single box.

The other things we should discuss


================================================================================
CONVERGED DESIGN (discussion, not yet built)
================================================================================

STATUS (2026-09-12): PARKED, not building now. In practice hosts are secured fast
(~seconds), so the cold-start pain the gossiper solves isn't real today. Trigger to
revisit: run a cold start with the graflex SOURCE removed and measure — if securing
hosts then takes serious time, the gossiper is worth building; otherwise it stays
parked. The honeypots.json distribution (below/related) rides on this same parked
sync, so it waits too.

-c/--config : DONE. `dyva -c <DIR>` repoints every on-disk store (reputation DB,
jobs/chats DBs, caches, images/audio/video/music dirs, settings, survey, cleansed
log) via _apply_cache_dir(), run right after arg-parse. Package-relative files
(the classifiers) stay put. Lets a second instance run alongside the default.

--- The problem being solved -------------------------------------------------

Two concrete pains, both in the gap between SURVEY time and INFERENCE time (for
our sources that delta is up to ~100h; unknown for others):

  1. Liveness gap. Survey is stale; hosts vanish. At inference dyva pays to
     re-doorknock, find things stale/unreachable, stumble around.
  2. Honeypot / smoke cost. Finding a fake/honeypot endpoint, running the smoke
     test, and tossing it is expensive per instance.

If another dyva doorknocked the same host recently, we shouldn't repeat that.

--- The shape we converged on: pull-on-demand positive leads --------------------

NOT replication, NOT a CRDT, NOT a background sync, NOT a mine.db/shared.db split.
It is a query-time fallback, pulled only when a dyva is about to do expensive
discovery:

  * Endpoint (per the #5 sketch):
      GET /realtime?source=<name>&model=<glob>
        -> recently-good (host, model, last_good) rows for that source, freshest
           first. "yo, hey server, have any news on qwen2.5* ?" -> "try a,b,c,d".
      POST /realtime {row}   (the producer side — see call sites)

  * Two call sites, same endpoint:
      - Inference fanout: when dyva would otherwise begin cold-probing UNKNOWN
        hosts for a model, it first asks the configured peer(s) and splices their
        answers in as high-priority candidates.
      - __dyva_info__:test / :test-all: before probing a batch, ask "know the
        status of any of these?" and skip the ones a peer freshly characterized.

  * :test-all is the natural PRODUCER. It is literally what establishes
    smoke_ok / good, so its verdicts are the freshest status worth handing back
    out (POST). The loop closes: one instance's sweep feeds the next instance's
    sweep and fanout.

--- PUSH trigger: debounced-on-success (NOT the unknown->good edge) -------------

  Why not the edge: "unknown" is the ABSENCE of a verdict row (tier rank is
  recent -> good -> maybe_good -> unknown(absent) -> bad). A good row persists;
  failures demote good->maybe_good->bad (still a row), nothing resets good back to
  unknown on a timer. So `unknown -> good` fires ~ONCE per (host,model) ever — a
  cold-start BURST as a fresh instance warms up over the survey, then near-total
  silence. That silence is the trap: a warm instance never re-crosses the edge, so
  it never re-pushes, so the gossiper is refreshed ONLY by new/cold instances — it
  goes stale for a steady or single operator. Push-rarity and server-freshness are
  the same coin; the edge is too rare to keep the gossiper warm.

  * The trigger is a SUCCESSFUL result, DEBOUNCED to at most once per (host,model)
    per window (e.g. once / 6h). That keeps warm instances re-attesting from actual
    use (so the stack stays fresh), while the debounce is the hard traffic cap —
    not per-request, not per-edge. Usage-driven re-attestation, bounded.
  * Positive-only: we push successes, never failures. A failed attempt produces no
    push (the poisonable, non-self-correcting negative stays off the wire).

--- The store: most-recent-N, no TTL, eviction by displacement ------------------

  * The gossiper is a pure recency stack: most-recent ~N (e.g. 50) per source/model,
    each row carrying its DISCOVERY TIME. A push goes to the top. NO time-based
    expiry, no server-side "too stale" policy — the server is that dumb.
  * Dead nodes drop off NATURALLY BUT SLOWLY, positive-only: nothing tells the
    gossiper a host died (no negative push). A stale entry just sinks as fresh
    successes pile on top and eventually falls past N. Edge case, working as
    intended: client pulls list, tries #1 -> fails, tries #2 -> succeeds -> #2
    re-pushed to the top, #1 drifts down and is eventually buried.
  * Freshness is IMPLICIT, no 36h constant: effective window = N / push-rate. A
    busy source cycles the 50 slots fast (tight freshness); a quiet source cycles
    slow (looser) — the right behavior. The RECEIVER still gets each row's
    timestamp and applies its own "too stale to bother" preference, so displacement
    (store side) + timestamp skip (receiver side) = belt and suspenders, still zero
    server-side time logic.
  * Cost of "slowly": a stale entry near the top costs a few clients one failed
    attempt each (fanout just moves to #2) before successes bury it — and crucially
    NO negative push, so it stays positive-only and unpoisonable.

--- The RECEIVER owns the judgment --------------------------------------------

  * For each returned (host, time) it checks its OWN db: "3.4.5.6 isn't in my
    database — probably offline / not my copy of this source — skip. 2.3.4.5 — I
    have that — fresh-good lead, try it." Skip-if-unknown keeps it in its lane: it
    syncs fresh STATUS on shared-survey hosts, NOT host discovery (importing a
    never-surveyed host would be a separate, bigger feature, out of scope).
  * No echo-back (the sender already knows the host is good), no dedup, no
    coordination. A stale server entry for a host YOU already know costs nothing —
    you read your own db; the gossiper only ever fills blanks.

  * STATELESS / NO PROVENANCE — the keystone that makes it trivial to build.
    Neither side remembers anything beyond its own plain table:
      - The client does NOT tag a host "came from the server." A pulled lead just
        becomes an ordinary candidate and flows through the SAME try-it path as a
        cold-discovered one; a success runs the SAME debounced-on-success push. No
        "is this a sync host?" special case, no "did the server suggest it?" flag.
      - The server does NOT track "I sent X to that client." No per-client state,
        no "already told you" dedup. On a report it writes the row to the top; on
        a query it reads the top-N. That's all.
    Consequence: NO new code paths for "synced" hosts, no bookkeeping tables. The
    feature is just (a) a pull that seeds the normal unknown-candidate list and
    (b) the normal edge push — both reusing the EXISTING host-status machinery.
    (Same principle as dyva's routing: no parallel mechanism, just existing rules
    firing.)

--- Why this is safe (the whole point) -----------------------------------------

We feared the graflex mass-flagging failure (a bad pass gutting a populated pool).
This design sidesteps it by sharing POSITIVE LEADS ONLY:

  * Skip / seed on a peer's POSITIVE ("recently good / smoke_ok"). Re-verifying a
    fresh positive is pure waste, and if the positive is wrong your own attempt
    re-grades it normally (good -> maybe_good -> ...). Self-correcting.
  * ALWAYS establish NEGATIVES yourself. Never skip a host because a peer called
    it bad/fake — that is the sticky, non-self-correcting inheritance (you'd skip
    it forever and never re-test). The hot inference path avoids junk IMPLICITLY
    by only chasing positive leads (the peer simply doesn't list what isn't
    working), so it never stumbles into honeypots in the first place. The
    maintenance sweep re-verifies negatives locally — cheap enough, not hot path.

  Net: the `null -> smoke_fail` sync we agonized over isn't needed. Everything
  rides on sharing POSITIVE "good" status only (pushed debounced-on-success) plus
  the fill-blanks application rule below — both safe and self-correcting.

  Only-fill-blanks rule: a received row is applied IFF local state is the "I don't
  know" state (unknown / null). A peer can never overwrite something you measured,
  so it can't gut a pool of things you actually know.

--- Scope / threat model (#6, #7): "cookies on the top shelf" -------------------

  The goal is DETERRENCE OF LAZY ABUSE, not confidentiality — explicitly a non-goal.
  The harvesting tools are distributed anyway; the private source just raises the
  bar above `uvx dyva` so that grabbing a big host list takes reading payloads and
  patching code (and may trip a HuggingFace block). A public demo server already
  runs with the "secret" source; you just have to do meaningfully more than the
  one-liner to reach it. That friction IS the feature; don't over-engineer it into
  crypto.

  * Per source, enforced at the PRODUCER/PUSH side: an instance only pushes hosts
    from a sync-enabled SHARED source; private / federation-source hosts aren't
    pushed. This is the sensible default (don't casually spray private hosts), NOT
    a confidentiality guarantee — see next point.
  * Skip-if-unknown at the receiver is correctness, not privacy: by the time a
    client says "2.3.4.5, never heard of it, must be stale" the IP already crossed
    the wire. That's fine under THIS threat model (top-shelf, not a vault) — noted
    only so nobody later mistakes it for a security boundary.
  * Source schema grows: { name, url, mapping } -> { name, url, sync, mapping },
    where `sync` = where to ask for that source (its gossiper endpoint).
  * Namespacing by source name partitions the data; not real protection, and not
    meant to be. No malicious-abuse hardening (effectively a single operator). A
    per-source shared secret in .env could gate POST later if ever wanted (never in
    code, no signpost) — out of scope.

--- THE GOSSIPER (the reference sync server) -----------------------------------

  Name, in the dyva/graflex/woahllama spirit: "the gossiper" — the endpoint dyva
  gossips fresh good-host status to and pulls it from.

  * It is a USER-CONFIGURABLE, USER-REMOVABLE setting (sources are a setting, not
    an assumption): point it at the public demo gossiper, run your own, or drop it
    entirely. Per-source `sync` holds the gossiper endpoint for that source.
  * The public demo gossiper is the default reference instance; nothing forces an
    operator to use it or to keep it.

--- Open knobs (still undecided) -----------------------------------------------

  * The fanout THRESHOLD that triggers the ask (exhausted local good hosts for the
    model? first K attempts failed? about to cold-probe unknowns?).
  * The receiver-side freshness preference (how old a discovery time is still worth
    a try — 36h is the working example; a per-instance knob, not a server TTL).
  * The server's response bound N (most-recent ~50 good discoveries per source/model).

RESOLVED: push is edge-triggered on unknown->good (see the PUSH section). That was
the "every time is too much" worry — it's once per rediscovery, not per request.

MVP = the pull (GET) alone, consumed by fanout + :test-all, positive-leads-only,
per source. The edge-triggered POST/producer + server TTL cache layer on next;
thresholds and the cutoff value are the remaining tuning.


================================================================================
REFRESH (2026-10-13): THE SPINE IS THE NAMESPACE, NOT THE TRANSPORT
================================================================================

The converged design above assumed a custom HTTP endpoint (GET/POST /realtime). The
refresh keeps ALL of its semantics (positive-leads-only, fill-blanks-only, debounced-
on-success push, receiver-owns-judgment, stateless/no-provenance, per-source) and
changes only two things: the load-bearing idea, and the transport.

--- The load-bearing idea: channel = hash(source URL) --------------------------

  The real insight is NOT the transport. It is that the sync channel for a source is
  DERIVED from the source URL:  channel = hash(source url)  (e.g. sha1). The transport
  (NATS, HTTP, a file on a CDN, Redis) sits behind that and is interchangeable.

  The source URL then does THREE jobs at once:
    * Rendezvous key — same URL -> same channel, deterministically, with no registry
      and no coordination. Channel names aren't assigned; they fall out of the URL
      every dyva already has. No `sync`-name to curate or collide.
    * Capability ("keeps the sources clean") — you cannot find, subscribe to, or
      enumerate a source's channel without knowing its URL to hash. The URL IS the
      key. The public server can't be scraped for "what sources exist." Top-shelf
      friction, for free, with no auth layer.
    * Correctness boundary — you can only push/pull on channels for sources you HAVE,
      so you structurally only ever exchange status about the host population you and
      your peer BOTH survey. "skip-if-unknown-host / stay in your lane" stops being a
      receiver-side rule you must remember and becomes a property of the namespace.

--- This DISSOLVES the leakage problem (it isn't mitigated, it's gone) ----------

  The old, acknowledged leak: "skip-if-unknown at the receiver is correctness, not
  privacy — by the time a client says '2.3.4.5, never heard of it' the IP already
  crossed the wire." A global gossiper sprays private host IPs to whoever listens.

  Namespacing by hash(source URL) closes it BY CONSTRUCTION: the capability that lets
  you join a channel IS the capability that already grants you the hosts — to read a
  source's channel you must know its URL, and knowing the URL is exactly what lets you
  fetch those hosts from the source directly. So:

      channel audience  ⊆  people who have the source URL  ⊆  people who already have those hosts

  The IP still crosses the wire, but only to someone who could already pull it from
  the source itself. The sync never widens the audience beyond the source's existing
  one; a stranger (no URL) can't derive the channel, so there is no stranger on it to
  leak to. It degrades gracefully across the whole source mix:
    * PUBLIC sources (forrany/spider/happyshua — lists already on GitHub): URL public
      -> channel derivable -> but the hosts were already public. Nothing new leaks.
    * SEMI-PRIVATE source (the 9ol.es feed): URL not casually known -> channel not
      derivable -> audience bounded to those who already have the feed.
  Either way: sync exposure ≤ source exposure, per source, automatically. Still
  "top shelf," not crypto — the URL is a bearer capability (whoever gets the URL gets
  the channel, same as whoever gets the URL gets the source) — but that is the SAME
  boundary the source already has, so the sync adds no new exposure. This REPLACES the
  "noted leak" caveat in the scope/threat-model section above: privacy = the source
  URL is the one capability for both source access and sync access.

--- Transport: NATS is the reference backend, not the design --------------------

  Core NATS pub/sub can't serve the query-time PULL (a late subscriber misses
  history), so the reference backend is NATS JetStream — and its per-subject
  retention (MaxMsgsPerSubject = N) IS exactly the "most-recent-N recency stack, no
  TTL, eviction by displacement" specified above, off-the-shelf. The server you
  maintain is `nats-server` with JetStream + that cap; no custom service to host.
    * subject = sha1(source url). Pull-on-demand stays stateless via a JetStream
      direct-get / pull consumer of the last-N at fanout time — no live subscription,
      no background sync (preserving the deliberate "NOT replication, NOT background
      sync" choice). Push-subscribe is available if warm caches are ever wanted, at
      the cost of statelessness.
    * Model granularity wrinkle: model names (`qwen3.8:27b`) aren't NATS-subject-safe
      (dots/colons are token separators). MVP: one subject per source, model in the
      payload, filter client-side after fetching last-N (fine for N~50). Option B:
      subject = sha1(url).sha1(model) for native per-model MaxMsgsPerSubject.
    * Cost: adds a `nats-py` dependency (the custom /realtime was dependency-free), in
      exchange for persistence + retention + pub/sub for free instead of hand-rolling
      the recency store. Weigh against keep-client-features-light.
    * Source schema: { name, url, mapping } -> { name, url, sync, mapping } where
      `sync` = the transport/server (the NATS URL); the CHANNEL is derived from `url`,
      not stored. Opt-in / overridable / removable, same as any source.

--- WHEN to pull: on /refresh, with everything else ----------------------------

  This RESOLVES the "fanout THRESHOLD that triggers the ask" open knob by ELIMINATING
  it. The pull is NOT a hot-path, fanout-time query with a threshold to tune. It rides
  the EXISTING /refresh cycle — the same pass that already re-pulls the configured
  sources and lands new hosts ("do it with everything else"). The gossiper is just
  another input to /refresh; its positive leads enter the pool as ORDINARY candidates,
  so fanout needs ZERO gossiper-specific code — no "about to cold-probe?" trigger, no
  synced-host special case. This is the stateless/no-provenance keystone paying off: a
  pulled lead is indistinguishable from a source host once it's in the pool, so it
  flows through the SAME try-it path with no new branches.
    * Producer (push) is unchanged and stays event-driven: debounced-on-success,
      positive-only. Only the CONSUMER/pull timing is resolved here.
    * Consequence to accept: lead freshness is now bounded by the /refresh cadence,
      not request time — the trade for the simplicity. Fine as long as /refresh runs
      often enough to beat the survey delta it papers over (and /refresh can pull the
      gossiper even when a full source re-pull is skipped, if finer freshness is ever
      wanted — but that's a knob, not a requirement).

--- SCOPE v1: smoke-test verdicts (observed output), not liveness ---------------

  Start by sharing ONLY the smoke-test result — the observed-output verdict of "real
  inference endpoint that passed the smoke" vs "honeypot / fake (took the bait / canned
  reply)". That is the HIGH-CONFIDENCE, DURABLE datum: it describes the host's NATURE,
  established by actually sending something and reading what came back ("honeypot is we
  tried something, we got some results"). Explicitly OUT of scope for v1:
    * "up" — liveness is transient (up now != up in 5 minutes); a stale "up" is worthless.
    * "down" / unreachable / timeout — low confidence (flaky network, false negatives).
  Connection state is noise; the smoke verdict is signal. This also sharpens the
  converged design's "recently-good" lead: the part worth syncing is the REAL-ENDPOINT
  determination, not the moment-in-time liveness bundled with it.

  How this sits with positive-leads-only: the PASS (real endpoint) is a positive, fully
  consistent with the rule. The HONEYPOT verdict is a NEGATIVE, and it is the ONE negative
  we deliberately TRUST and inherit — skip the host WITHOUT contact. The reason is the whole
  point of the flag: re-verifying a honeypot means sending it a request, i.e. walking into
  the bait/RCE trap the flag exists to keep you away from. "Re-verify, don't inherit" is the
  right rule for FLAKY negatives (down/timeout — cheap and safe to recheck yourself), NOT for
  a conclusive malicious one where contact IS the hazard and the host won't reform. So the
  original "always establish negatives yourself" splits cleanly: flaky liveness negatives =
  never inherit; honeypot = trust and skip, no contact.
  The residual risk is poisoning — a bad peer flagging good hosts as honeypots to get them
  skipped. It's bounded, and NOT by re-verifying (that defeats the purpose): (a) the namespace
  means only holders of the source URL can post to its channel, so an attacker is already a
  source participant, not the open internet; (b) src_ip is stored per row for retro-
  invalidation of a bad contributor; (c) the blast radius of a false flag is one skipped host.
  If permanence ever bites, AGE honeypot flags out (re-admit to discovery after a long cooldown)
  rather than re-verify-by-contact.

--- SIMPLEST PROTOCOL (the MVP): timestamp-delta sync on /refresh ---------------

  This is simpler than NATS and does the whole job, so it's the MVP. The sync is a
  plain BIDIRECTIONAL DELTA over ONE POST, piggybacked on /refresh. (NATS/JetStream
  above is no longer needed for v1; the namespace-by-hash insight still holds if a
  shared transport is ever wanted, but a per-source POST URL carries the same
  "sync access ≡ source access" property, because the URL lives in the source def.)

  Source schema: { name, url, mapping } -> add a sync/post URL (the server side is the
  thing "we will have to build"). Opt-in / overridable / removable like any source.

  CLIENT state, persisted to disk (survives restart), per source — TWO timestamps:
    * last_refresh_at : the client's OWN clock at its previous refresh. Bounds the
      OUTGOING window (which of my smoke results to send this time).
    * server_hwm      : the timestamp the SERVER handed back last time. The cursor for
      the INCOMING delta ("send me everything newer than this").

  EACH /refresh (with everything else):
    1. outgoing = my local SMOKE-TEST results with local-time in (last_refresh_at, now].
    2. POST <sync_url>  { rows: outgoing, since: server_hwm }
    3. SERVER:
         - ingests `rows`, stored with the client's src_ip (so a bad contributor can be
           invalidated later) and the timestamps (so it "can do the math" later);
         - SELECTs its own records with server-time > `since`; returns { rows, now: <its clock> }.
    4. CLIENT: apply returned rows (SMOKE verdicts only, fill-blanks; honeypot = hint,
       re-verify) then persist  last_refresh_at := now,  server_hwm := returned `now`.

  SERVER = a dumb append log: "some stupid timeseries data sitting in sqlite or whatever"
  — rows of (smoke record, src_ip, client_ts, server_ts). Two operations: INSERT the
  client's payload; SELECT where server_ts > since. No recency stack, no eviction, no
  pub/sub, no per-client dedup state beyond the src_ip tag. Every timestamp is kept, so
  invalidation / windowing / analysis are just queries later.
  UNBOUNDED on purpose — the "most-recent-N (~50) recency stack, eviction by displacement"
  from the converged section above is CANCELED. A LIMIT/windowed store surfaces repeats in
  waves (periodic duplicates as the window slides), which is worse than just keeping
  everything. Leave it unbounded for now; if the table gets huge, deal with it then
  (archival/compaction), not with a row cap.

  Why it's right:
    * Reuses /refresh — no new trigger, no hot path ("do it with everything else").
    * Delta by timestamp both ways — bandwidth ~ the deltas, not the whole set.
    * Clocks never need to agree: the client windows by ITS clock, the server by ITS
      clock, each just echoes the other's last token verbatim. No shared time, no skew.
    * src_ip on the server is the abuse lever (retro-invalidate a bad client) with no
      up-front auth ceremony — fits "cookies on the top shelf".

--- STORAGE: quarantine measured vs attested; the router reads ONE view ---------

  Our own probes (MEASURED) and peers' (ATTESTED) must NOT share a table — commingling erases
  provenance and lets a peer overwrite measured truth. So: host_measured (= dyva's host_status
  in prod) stays untouched, peer rows land in a quarantined `hosts_attested`, and the router
  reads a unified VIEW `host_resolved`. Canonical schema + view: dyva/gossiper/resolution.sql;
  its proof (all cases pass): dyva/gossiper/test_resolution.py. Mirrors graflex's host/bad_hosts
  two-table last-wins split.

  Resolution per (host, model):
    * a HONEYPOT (measured OR attested) is ONE-WAY sticky and WINS -> trusted skip, no contact
      (re-probing a claimed honeypot IS the hazard). Measured honeypot outranks attested only
      for provenance; both mean "skip".
    * else the MOST-RECENT row wins. With re-measure-on-use this reproduces fill-blanks for
      free (a host you use => your measured row is freshest => yours wins; one you don't =>
      attested fills the blank; a fresh peer row beats your stale own row).
  Verified cases: attested fills a blank; your newer measurement wins; a fresh attested row
  updates your stale one; attested honeypot beats even your NEWER pass; your own honeypot is
  sticky against an attested pass (peer can't un-bad your burn); honeypot wins among mixed
  attesters.

  Quarantine's payoff = INVALIDATION. Each attested row carries the `attester` (the reporter's
  IP/id — pushed down; no masking needed, the host set is already 10k+). A poisoned/stale report
  drops with one `DELETE FROM hosts_attested WHERE attester=?` that can't scratch host_measured,
  and the host recovers in the view (verified: E -> your measured pass; a mixed host -> the
  remaining attested pass). That is the escape hatch for a trusted-but-wrong honeypot, NOT
  re-contact; aging attested rows out is the other.

--- Status: SERVER + CLIENT BUILT (2026-10-13) ---------------------------------

  The SERVER is built and live-verified on real Apache: dyva/gossiper/gossiper.php (deploy
  as the subdir ~/www/dyva/gossiper/ -> 9ol.es/dyva/gossiper) + gossiper.py (portable twin).
  Schema `gossip(id PK AUTOINC, source, host, model, smoke, client_ts, src_ip, server_ts)`,
  cursor = `id`. The delta SELECT NOW RETURNS `src_ip` so the client can store the attester
  (invalidation hatch); live round-trip confirmed. Deploy notes: a `gossiper/` SUBDIR with
  `.htaccess` = `DirectoryIndex gossiper.php` (clean URL, no mod_rewrite needed) + FilesMatch
  denying *.sqlite*/.db/.py (else the store is one GET away). GOTCHA: POST must use the
  TRAILING-SLASH url (/dyva/gossiper/) — the no-slash form 301-redirects and the redirect
  drops the POST body.

  The CLIENT is built (dyva/__init__.py, "GOSSIPER CLIENT" section; tests in
  tests/test_gossiper_client.py, 16 green). It rides /refresh (handle_refresh ->
  gossiper_sync, off the event loop, best-effort, gated by GOSSIPER_ENABLED):
    * TABLES in host_status.db: `hosts_attested` (QUARANTINED peer rows: host, model,
      verdict in pass|honeypot, attested_at, attester=src_ip, source=sha1(url)) and
      `gossiper_state` (per-channel cursor: last_refresh_at + server_hwm).
    * channel = sha1(source url). Hosts are grouped by their pool `source` tag, so a host
      is only ever POSTed to ITS source's channel (the leak boundary, structural).
    * OUTGOING = our smoke verdicts since last_refresh_at. HONEYPOT (host-wide, model='') =
      the HONEYPOT_KEY sentinel OR ANY `fail_smoke=1` ("smoked:canned") row — a canned/fake
      reply IS a form of honeypot (the older, per-model way the same "unsafe, don't go back"
      verdict was recorded before the sentinel existed; kristopolous 2026-10-14), so it's
      shared + inherited identically, NOT re-verified (contact is the hazard). PASS (per-model)
      = smoke_date set & fail_smoke=0 AND the host is not unsafe (a canned host is never
      advertised good even for a model that looked fine). First wiring shared only the 4
      sentinels; folding in fail_smoke=1 took that to ~1500 honeypot hosts. INCOMING ->
      hosts_attested.
    * VISIBILITY: the host-card per-model breakdown (handle ...host_model...) surfaces a peer's
      honeypot verdict — a host we never measured shows as failed-smoke "synced" (state bad +
      attested flag) instead of "untested", so a synced verdict is actually visible. host_status
      is still never written (quarantine); the count carries an `attested` provenance field.
    * RESOLUTION into the router is via the EXISTING loaders, NOT a literal host_resolved
      view (dyva's find_servers reads good/maybe/bad/unreachable sets, richer than the
      prototype's pass/honeypot grain): attested honeypot UNIONs into find_servers' host-wide
      skip set (load_attested_honeypots -> treated exactly like a measured honeypot); attested
      pass fills a blank (load_attested_pass -> lifts an UNKNOWN host to maybe_good, never
      overriding our own good/maybe/bad). honeypot still wins host-wide above any pass.
      resolution.sql + test_resolution.py remain the canonical proof of the resolution rules
      and the bridge if find_servers' reputation model is ever unified onto one view.
    * INVALIDATION = `DELETE FROM hosts_attested WHERE attester=?` (src_ip now carried end to
      end). Aging attested rows out is the other escape hatch; neither re-contacts the host.

  Everything in the CONVERGED DESIGN section still holds; this only reframed the spine
  (namespace) and demoted the transport (NATS = reference backend, unused for v1).
