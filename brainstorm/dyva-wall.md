# dyva wall — advisory broadcast into live connections (brainstorm — IDEA)

Status: idea 2026-11-22. Early thinking, **not committed, no code**. Entry point was a
plain operational itch: people use the public demo (9ol.es) and are welcome to, but to
**test a change I have to drain or restart the server**, and right now that means yanking
their connections with no warning. What I want is a `/usr/bin/wall` for dyva — a way to
**speak into the streams that are already open** without killing them. Advisory, not a
disconnect.

## The headline

This is **not** Stop/Skip. Those abort a stream (`_workers[wid]["stop"]` /
`["skip"]`, consumed by the read loop in `_pump_stream`). A wall is the opposite: the
connection stays up, we just **say something into it** — "heads up, the server is
draining, you may get cut off; reconnect in a minute." The whole value is that it's
non-destructive, so a courteous drain replaces a rude one.

## The real axis: who's listening

The one distinction that actually shapes the design is the **target**, because "a human
watching tokens scroll" and "a model mid-agentic-loop" are different injection points:

- **human** — a visible content chunk spliced into the in-flight stream. Emit-only; it
  never enters the model's context, it just reaches the person's screen. This lives at the
  **pump** (`_pump_stream`), the one place every committed stream is written. The message
  rides out as an ordinary content delta (openai: an sse `content` chunk; native: an
  ndjson `message.content` line) at the next line boundary.
- **model** — a turn injected into the conversation an agentic caller is running ("run a
  sleep for 30s", "wrap up, we're draining"). That's a **context injection at a round
  boundary**, not a pump emit — the same shape as the session-tool loop just added to
  `_stream_chat_failover` (append a message, the next round sees it). The model reads it
  and can act (pause, summarize, stop asking for more).

So plausibly **one broadcast primitive with a `target: human | model` flag**, dispatched
to whichever injection point fits. The "don't worry if you get cut off" case is `human`;
the "sleep 30s" case is `model`.

## Where it hooks in (mechanism sketch)

The worker registry is already the roster of live streams: `_workers` maps `wid` →
a dict that today carries `stop`/`skip` handles. A wall is a **third per-worker signal**
of a different kind — not "abort" but "carry this payload out on your next line." The
dashboard already has a broadcast spine (`broadcast_activity`, `_broadcast_workers` over
SSE) but that talks to the **operator UI**, not to the **inference clients**; the wall is
the missing sibling that talks to the clients.

Rough shape (to be torn apart later, not a design):

- an operator endpoint / admin action sets a pending notice (text + target + maybe a TTL
  or a "once per stream" flag), fanned onto the live workers much like a handout.
- **human target:** each active `_pump_stream` checks the pending notice at its line
  boundary and, if unsent for this stream, emits one content chunk, then marks it sent so
  it isn't repeated. The watchdog/accounting path is untouched — it's an extra `cw(...)`,
  gated, inert when no notice is pending (same discipline as the session tap: text path
  pays nothing).
- **model target:** the notice is appended as a `system`/`user` turn at the next agentic
  round boundary (session-tool loop / `dyva_agent` loop), so the model sees it as context.

## Why it's worth having beyond "I'm testing"

A graceful drain is generally useful: migrations, host-pool churn, a known-slow moment,
or just "this box is going down in 2 minutes." It turns an abrupt truncation (which the
failover machinery then tries to paper over) into an **announced** one the client can
plan around. It also gives the **model** a cooperative off-ramp — "stop spawning
sub-agents, we're winding down" — which is cleaner than killing it mid-tool-call.

## Open

- **Trigger surface.** Operator-only admin action, obviously (never exposed to the pool —
  same rule as everything else). CLI? dashboard button? an HTTP endpoint the restart
  script calls *before* it bounces the server?
- **Scope.** All live streams, or filterable (by model, by host, by affinity/userhash once
  sessions land)? A drain wants "everyone"; a targeted nudge might want one conversation.
- **Human-target framing.** A bare content chunk injected mid-answer corrupts the visible
  transcript (it reads as if the model said it). Does it need a sentinel/marker, a
  separate channel, or do we accept that a wall is deliberately barging into the text?
  Maybe only emit it *between* turns, or as a trailing note, not mid-sentence.
- **Model-target trust.** It's an instruction to the model from the infrastructure, not
  from the user — the model might ignore it, or the user might not want infra speaking in
  their context at all. Advisory only; never load-bearing.
- **Relationship to drain.** Is "wall" just the *message* half, with the actual
  connection-draining (stop-after-this-turn, stop accepting new) a separate second half?
  Probably yes: announce, then optionally drain.
- **Dedup / TTL.** Once-per-stream vs repeat; expire the notice so a late-connecting client
  doesn't get a stale "restarting in 30s" ten minutes later.

Grounding: `_pump_stream` (the per-line client write seam), `_workers` + the `stop`/`skip`
handles (the live-stream roster), `broadcast_activity` / `_broadcast_workers` (the existing
operator-facing broadcast), and the session-tool round loop in `_stream_chat_failover`
(the model-target injection precedent). See also [[user-account]] (userhash → scoped walls).
