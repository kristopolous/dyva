# Manual host-skip (brainstorm — DESIGNED)

Status: designed 2026-11-04. Contract below is complete; **not built**. The feature existed
once — a per-worker "skip" control in the dashboard — and the UI trigger was **cut on
purpose** (see archaeology). It has died and come back before (commits `a483b59` "restore …
skip", `47b9242` "making sure the skip work"), so this records the full contract once, to
stop it falling away a third time for lack of a record.

## What it is

A human-triggered "this host has stalled — move on" control over a live worker. Not a new
routing idea: it's a manual trigger for the same "abandon this host, try the next" the race
already does on stalls/soft-fail. The distinguishing properties:

- **Skip = go to the next host.** That's the whole promise.
- **The resulting content is undefined — always.** Fresh start, incoherent splice, whatever
  the next host does. There is no promise of coherent continuation; skip is *not* a resume.
- **The one invariant: the client connection is never dropped.** That's the entire
  difference from **stop**. Stop ends the socket (visible termination); skip keeps it open
  and swaps what's feeding it.

## Why human-triggered (and why it can't be automatic)

The machine cannot tell a stalled worker from a legitimately slow one: a worker emitting no
tokens for ten minutes might be hung, or might be mid-toolcall on something like
`sleep 100000;`. Indistinguishable from the outside — so the system must **not** try to
adjudicate it and auto-kill. But a human watching often *does* know ("there's no sleep in
here, what gives?"). So skip is exposed as a primitive and the operator pulls it. Same stance
as the capability discussion ([[dumb-gateway-split]]): don't make the system adjudicate what
it can't; expose the control; let the human with the context decide; undefined-but-honest
outcome. Here "honest" means the connection is preserved, never silently dropped.

## The safety rule — a partial toolcall must become INVALID, not best-effort

This is the load-bearing part and the easy half to get wrong. A half-streamed toolcall is
not incomplete *text*, it's a live weapon: `DELETE FROM users` with the `WHERE uid=19131`
still in flight. You must never let a partially-streamed toolcall pass **as valid**. On a
mid-toolcall skip you truncate it to *un-executable*, and the invariant holds in **both
directions**:

1. **Toward the client:** close the stream so nothing runs the partial — specifically,
   **never emit `finish_reason: tool_calls`** for it (that token means "go execute").
   Terminate it as aborted/incomplete so even a naive client discards it.
2. **Toward the next host:** drop the partial call from the continuation context entirely.
   Hand the next host the dangling assistant message and *it* may cheerfully finish the
   arguments — so the host you skipped **to** completes your `DELETE FROM users`. Start it
   from the last clean message boundary.

Feasibility: dyva already tracks toolcall args while streaming (`partial[1]` in
`_pump_stream`), so "are we mid-call?" is a state it can already see — a call opened, no
finish yet.

## Safe-tiers — not gates, just how much cleanup skip owes

Skip is always offered. The tier only decides the cleanup:

- **no tokens emitted** — nothing to clean, swap freely
- **prose, no toolcall** — harmless splice, just swap
- **mid-toolcall** — the truncate-to-invalid-both-ways dance above

## What's live vs gone today

- **Endpoint: LIVE.** An admin route taking `?wid=`, summary verbatim "Abandon the in-flight
  host and let the race try a different server" (dyva/__init__.py ~11150). Looks up
  `w.get("skip")`, fires it threadsafe, returns `{"skipped": model, wid}`. Tells the client
  nothing — already the right semantics.
- **Racing-phase skip: WORKS.** `_skip()` (~6066) cancels the in-flight `_attempt_tasks` and
  leaves `done` clear, so the race advances to the next host. Armed on the racing worker
  (`_workers[wid]["skip"]`, ~6074/6079).
- **Committed-stream skip: NO-OP.** Once a worker commits and streams, and for non-chat kinds
  (image, speech, `/api/generate`, edit), the handle falls back to the default
  `"skip": lambda: None` (~1522). So clicking it mid-stream did nothing.
- **UI button: REMOVED on purpose** (dashboard.html ~3063): "it was wired to a skip handle
  that most worker kinds never arm, so it silently did nothing … Stop is the honest control."
  A button that no-ops on most rows was judged worse than no button.

## The one open pivot (resolve before building)

Does `_stream_chat_failover` already keep the client stream open across a host switch that
happens **after** tokens have flowed, or only **before** the first token?

- If it already swaps post-token → restore is small: arm a mid-stream skip handle that
  triggers that existing path, add the toolcall truncation, re-add the button.
- If failover only switches pre-first-token → then "hold the client stream open while
  swapping upstream mid-flight" is the actual new capability, and that's the real work.

Note: "mirror how `stop` was re-armed mid-stream" is the wrong analog — stop *terminates*,
skip *preserves*. The right analog is failover's own host switch. Read `_stream_chat_failover`
to settle this; it sizes the whole feature.
