# Unserviceable responses — cutting off runaway generations (brainstorm — IDEA)

Status: idea 2026-11-23. Early thinking, **not committed, no code**. Entry point was a
live worker that would not die: `873a263f-…` on `140.114.129.25:8888@qwen3.8-flash-next`,
**~9.6 MB** of body pulled over **~46 minutes**, still streaming, holding a connection.
Not a hang, not a zombie, not a bug in the cap — a host stuck in a never-ending
generation, being consumed correctly, on a leash too long to matter. This is about
giving dyva a *shorter, smarter* reason to let go.

## The concept: serviceability is a property of the consumer

A response is **unserviceable** when nobody downstream can use it — regardless of whether
the host ever finishes it. A multi-megabyte chat answer is unserviceable even if it
arrives with a clean terminal `done`: the chat UI chokes rendering it, an agentic
tool-call gets garbage, and it blows the context window for the very next turn. So there
is no point waiting for completion. The response was dead on arrival the moment it crossed
into "no human or program can consume this."

That reframes the problem. We are not trying to detect "is the model broken?" (hard,
philosophical). We are asserting "this output can't be used" (concrete, and a stronger
thing to say). The model's internal state is none of our business; the usability of what
it's emitting is entirely our business.

## Why no single signal works

**Size alone is wrong.** Qwen3.8 + YaRN on an RTX 6000 at a 1M-token window can
legitimately emit ~5 MB. A bare size ceiling false-positives on the real long-context
dumps that are part of the point of a big-window host.

**Loop/repetition alone is wrong.** "Do this highly repetitive task for me" is a real
request. We don't want to kill a model that's dutifully producing structured, repetitive —
but *varying* — content.

**But their intersection is almost empty, and we don't even care about the exception.**
A response that is *both* very large *and* genuinely looping is a runaway. The one
legitimate case in that corner — "repeat this sentence 5 million times" — is itself
unserviceable, so killing it (or the model refusing it) is a fine outcome. **We
explicitly accept false positives in the large-and-looping quadrant.** That decision is
what lets the rest of this stay simple.

## Current backstops, and the gap

- **Per-read stall watchdog** (`TIMEOUT`, live = 33 s): resets on *every* read. A host
  that trickles one line every <33 s defeats it forever. The live case did exactly this.
- **Wall-clock cap** (`WORKER_MAX`, live = **3600 s** from `settings.json`, not the `1800`
  literal in source; `WORKER_MAX_FAILOVER = False`): the only thing that actually stops a
  slow runaway today — after a full hour.

The gap: a response can be *obviously* unserviceable at minute 3 and we sit on it for 57
more. The byte rate on the live case was ~2.9 KB/s on the wire — steady, unremarkable,
invisible to the silence watchdog. We need a signal that fires on *what* is being emitted,
not just *whether* something is.

## Two kinds of runaway

They are different failure modes and need different detectors:

- **Type A — repetition.** The model says the same thing over and over. Common now
  (sampler collapse, reasoning models re-entering the same `<think>` cycle). The output is
  **periodic**. This was the live case.
- **Type B — breakdown.** The math falls apart and it emits gibberish word-fragments.
  **High-entropy, not periodic.** A cycle detector will never catch this.

## The ensemble

Committed to ensemble detection; **not** committed to the exact methods or their balance.
The shape:

**Gate — size.** A size threshold is the *trigger only*, not a verdict. Below it, do
nothing (zero cost). Above it, the detectors turn on. "Big enough to be worth inspecting,"
nothing more.

| detector | catches | cost | have it? |
|---|---|---|---|
| **size** | *trigger gate* | ~0 | easy |
| **period / tandem-repeat** over a trailing window (classic cycle detection on output; check a few period scales — token, phrase, block) | Type A | cheap (rolling hash, bounded window) | no |
| **coherence collapse** — mean word-length drops, broken-fragment / non-alpha ratio climbs | Type B | cheap | no |
| **tok/s spike vs. the host's own early-generation baseline** | **both** A and B | ~0 | **yes** (tps already tracked) |

**On tps — the cross-cutting backbone.** When a model enters either degenerate state the
sampler collapses to short, high-confidence tokens with no reasoning pacing, and tok/s
tends to climb. Two properties make it valuable: it catches both types, and measured
*against the host's own baseline* it is **self-normalizing** — a 3090 and an RTX 6000 have
wildly different absolute tps, but each accelerating past *its own* early-generation rate
is the same signal across a heterogeneous pool. It's free; we already record it.

**Caveat, straight from the live case:** that runaway was *slow* (~2.9 KB/s wire), so a
tps-spike would **not** have flagged it. tps cannot be a required vote. The structural
loop detector has to be able to fire on its own.

### Proposed combination (a starting point to tune, not a commitment)

Past the size gate, cut if **either**:

- **(a)** a clear repeat cycle is detected — the trailing window is mostly explained by a
  repeating period, sustained over enough new output; **or**
- **(b)** coherence has collapsed **and** tps has spiked above the host's baseline.

Rationale: a clear tandem repeat past the size gate is unambiguous on its own (and the one
legit exception we've already agreed to sacrifice), so (a) stands alone — which is what
catches the slow live case. Gibberish (b) is riskier to call on coherence alone (code,
tables, non-English can look fragmented), so it's paired with the tps spike. The operator's
one-liner is the whole thing in plain terms: **over a megabyte, looping, emitting fast →
cutting it off is not unreasonable.**

## Hard invariants

- **The streaming text path (`_pump_stream`) is not to be modified.** The detector is a
  pure **observer** there: it watches the content + timing dyva already assembles, and when
  it decides, it pulls the **existing** Stop/Skip/cap lever rather than reaching into the
  stream loop. Read-only in, existing machinery out. Same detector serves the buffered
  path (`_drain_stream`), where acting is easier.
- **Cheap on the hot path.** Checks run periodically (every N KB of *new* content, not per
  token) over a bounded trailing window. Nothing per-token, nothing unbounded.
- **False positives in the large-and-looping quadrant are accepted**, by decision above.
  The detector is not required to spare "repeat 5M times."

## Rollout: mark → mark-and-wait → mark-and-kill

Never ship straight to kill. The false-positive rate is unknown and there are zero Type-B
samples. Promote only on evidence:

1. **Mark only (shadow).** Detectors run, flag "would-have-killed," nothing dies. The mark
   is **not a boolean** — it records the evidence that would justify the kill: which
   detector(s) fired, the trailing-window sample, size, period found (Type A), tps baseline
   vs. current, timestamp. And it is **updated at job end with the final disposition** —
   clean `done` at a reasonable size, or ran to the cap / grew unbounded. That disposition
   is *approximate ground truth*: marked + hit-cap-or-huge ≈ true positive; marked +
   finished-normally ≈ false positive. The audit is then mostly mechanical — hand-label only
   the disagreements, not every sample. (Decision record → `worker-jobs.jsonl` fields;
   trailing-window bodies → a separate, size-capped sample file, not the jsonl.)
2. **Audit.** Over a large marked sample, confirm we only marked true runaways.
3. **Mark and wait.** Mark, wait a few minutes, kill unless it resolved during the wait —
   keep sampling. The wait is also a **permanent safety valve**, not only a transitional
   phase: a slow-but-finishing legitimate response self-terminates inside the grace window
   and is spared, which lowers FP for free. Mark-and-wait may be the end state; killing
   immediately only reclaims a few minutes of held connection and gives up that valve.
4. **Mark and kill.** Full enforcement — only once the marks look clean.

**Promote per detector, not globally.** We have Type-A evidence and no Type-B, so the cycle
detector (a) can graduate to wait/kill while coherence+tps (b) stays in shadow until a
gibberish sample exists to validate it. Each detector earns enforcement on its own evidence.

## Open questions

- **Size floor.** 1 MB as the trigger? Higher? Detection is cheap, so the floor is about
  "when is it worth caring," not cost. Must sit clear of legit long-context output.
- **Cycle detection shape.** Window length; which period scales (token / phrase / block);
  what fraction-explained-by-period over how much sustained output counts as "clear."
- **Coherence metric.** Mean token/word length? Dictionary hit-rate? Non-alpha / broken-
  UTF-8 ratio? Needs a real Type-B sample to calibrate — **we don't have one captured yet**
  (today's live case is Type A). Catch a gibberish breakdown and keep it.
- **tps baseline.** Which early window is "healthy"; what spike factor; interaction with
  cold-start and with reasoning models that are legitimately slow then fast.
- **Action on trip.** Governed by the rollout above (mark → wait → kill). When it does
  kill, reuse the cap path (a distinct outcome, e.g. `runaway`). Does a runaway ding host
  reputation? Argument for: the host's model broke on this request — soft-demote so it
  isn't tried first. Argument against: may be prompt-specific. Leaning toward a *distinct,
  soft* verdict (not a hard ban), tagged as model-behavior-on-this-request. Decide with
  data — and note the mark/disposition record gives us exactly that data.
- **Think vs. content.** Measure the assembled visible content, the thinking channel, or
  both together? A runaway can live in either.
- **Scope confirm.** Text only. Confirm no legitimate large payload (inline vision/audio
  base64, deliberate large-file generation) rides `_drain_stream`/`_pump_stream`; if one
  does, it needs an exemption.

## Calibration data on hand

- **Type A sample:** the live runaway (`873a263f-…`, qwen3.8-flash-next) — ~9.6 MB, slow
  trickle, periodic. Capture its body before it caps if we want a clean sample; otherwise
  `worker-jobs.jsonl` / `traffic.jsonl` for token counts and timing.
- **tps histories:** `worker-jobs.jsonl`, `~/.cache/free-ollama/stats.db`.
- **Type B sample:** none yet — needed before (b) can be tuned.
