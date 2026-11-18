# Strip Tool Output on Search Failure

**Status:** PARKED — brainstorm capture, 2026-11-17. Not scheduled, no code to be
written from this today. This records a design discussion so the shape isn't lost;
defaults and one load-bearing assumption are left open on purpose.

**One-liner:** when a request has searched hard and still can't place, dyva strips
the tool-call *output* out of the payload (keeping structure) and lets the in-flight
search carry the lighter version onward — a configurable clamp on runaway agentic
context, driven by search failure, not by context-window size.

---

## Problem

An agentic client (opencode, etc.) resends its whole conversation every turn, and
each turn appends tool-call results — read/edit/bash/grep output — so the payload
balloons. The case that first prompted the context-length thread was a ~230k-token
prompt against `qwen3.8`: almost nothing in the pool can both *fit* it and *prefill*
it quickly, so the race churns host after host and either grinds or gives up with
nothing to show.

The signal that matters is **progress, not size.** Owner's standing framing: "if
something is being serviced reliably then who cares." A big prompt that places and
streams fine is not a problem. A prompt that has genuinely failed to find a home is.
So the trigger is search *failure*, measured in hosts tried or time spent — never
context-window size (which is the wrong trigger and was rejected explicitly).

### What this does NOT address

The long-pure-*output* runaway (the 36-min `qwen3.8-heretic` stream that started the
original conversation) is a different animal: a single generation with no tool calls,
so there is nothing to strip. This design is about **input balloon** from accumulated
tool output, not output monopolization. The heretic is consciously set aside here.

## The mechanism

1. **Trip on failure.** A request that hasn't placed after a threshold — N hosts
   tried *or* T seconds of search, whichever first — is treated as a runaway.
2. **Strip, don't delete.** Walk the payload's tool results and replace each result
   *body* with a stub (`[output stripped]`) **in place**. Both the `tool_call` and
   its matching `tool_result` message stay, with their ids intact — so nothing
   desyncs and no upstream 400s on a dangling call-id. Keep the last `strip_keep_last`
   exchanges whole.
3. **Keep racing the lighter payload.** The ongoing fanout just carries the stripped
   version to the hosts it's *already about to try* — no restart, no new round-trip.
   By the time we strip we're deep into exploring fresh hosts anyway; the smaller
   prefill fits more of them and prefills faster, so the tail of the search is where
   it pays off.

## Why this is not a race cap

The standing rule (from the parked nudge spec, 2026-10-19) is **no time/host caps
bolted onto the race.** This respects it: the thresholds do not *abort* the search —
they *transform the payload and continue*. The clock and the host counter are signals
to strip, not a stopwatch that gives up. If stripping is disabled, the race behaves
exactly as today. "Fail" is never an outcome of the threshold; "lighter and still
racing" is.

## This revises an earlier owner call

The 2026-10-19 nudge spec drew a hard line: *"context-hacking the client's history is
explicitly out of scope (owner call)."* The whole nudge design bent around that line.
**Stripping tool output crosses it** — it is dyva editing the client's history.
Deliberate reversal (owner, 2026-11-17): stripping in place is the better lever and
the nudge's blind tool-call-forward is a footgun, so dyva takes on the payload
surgery. Stub-in-place keeps the edit honest — the model still sees *a tool ran here →
`[stripped]`*, it just doesn't re-read the volume.

## Affinity: discover once, strip upfront thereafter

dyva already fingerprints a conversation: `affinity_crc` = crc32 of the first N user
messages (the stable warm prefix, `dyva/__init__.py:441`; thread-audit at `:1894`),
used today to pin a big conversation to the host whose KV is warm. That crc *is* a
per-thread identity across turns — "the payload comes again" is just the next turn of
the same crc, bigger.

So the first turn eats the full N-hosts/T-seconds failure before it strips; once that
crc is flagged as a stripper, the *next* turn strips upfront and skips the whole
dance. Reactive to discover, proactive thereafter. The flag rides the existing
affinity cache (already keyed on the right crc, already TTL + LRU bounded), so it's a
small additive field, not new machinery.

Interaction note: stripping trims the **tail** (tool output); the affinity key is the
**head** (first N user msgs), so a strip never moves the crc and the host pin holds.
The host's warm KV only covers a contiguous prefix, so the first strip diverges the
prefix at the cut point and re-prefills from there once; after that the stripped shape
is the new warm prefix. A one-time cost, not ongoing.

## Settings surface

Same convention as the `affinity_*` knobs (settings dict → module globals,
`dyva/__init__.py:841`). Operator tunes or kills it; the behavior is a lever, not
something baked into the router.

| setting | meaning | default (proposed) |
|---|---|---|
| `strip_enabled` | master on/off | **on** (see below) |
| `strip_after_hosts` | hosts tried before a strip trips | `100` |
| `strip_after_secs` | seconds searched before a strip trips | `1200` (20 min) |
| `strip_keep_last` | recent exchanges left un-stripped | `3` |
| `strip_affinity_memory` | a tripped crc strips upfront on later turns | `true` |

Default stance (owner's call to flip): **on, conservative** — 100 hosts / 20 min only
ever trips on a genuine runaway, and the public demo is the thing being protected. A
single `strip_enabled: false` restores today's behavior exactly.

## Load-bearing assumption (verify before building)

`strip_keep_last` must leave a payload that can actually *place*. If the single most
recent exchange itself carries a giant tool result, keeping the last 3 whole may still
exceed every reachable host's context — and then stripping bought nothing. The strip
target probably needs a **token-budget floor**, not only an exchange count: strip
until the payload fits the candidate hosts' known ctx limits, stripping into the kept
exchanges too if forced. Confirm the real balloon shape (is it many small results or a
few huge ones?) before deciding whether exchange-count is enough.

## Open questions

- Exact defaults (above are proposals).
- Whether `strip_keep_last` is enough or a token-budget fit floor is required (the
  load-bearing assumption).
- Where the strip runs in the race/proxy path, and how it reads "hosts tried / seconds
  searched" from the in-flight fanout state.
- `strip_affinity_memory` TTL — reuse the affinity entry's window, or its own?

## Relation to the nudge spec

`2026-11-17` (this) and `2026-10-19` (nudge) attack the same oversized-prompt corner
from opposite directions. The nudge keeps dyva's hands off the payload and begs the
client to self-compact (footgun: blind tool-call forward). This strips the payload
directly (no client cooperation, no footgun) and is the owner's preferred lever as of
2026-11-17. The nudge's "simpler adjacent lever" (surface the honest overflow error so
the client self-compacts) remains a cheap, orthogonal thing worth doing regardless.
