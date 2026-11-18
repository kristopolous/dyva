# Oversized-Prompt Client-Compaction Nudge (Hail Mary)

**Status:** PARKED — brainstorm capture, 2026-10-19. Not scheduled, no code to be
written from this today. This is a design discussion recorded so the idea isn't
lost; several questions are deliberately left open.

**One-liner:** when a prompt is too large for any reachable host, make one
last-ditch attempt to get the *client* to shrink its own context — by letting a
model pick one of the client's own declared tools and forwarding that tool-call
back as the response.

---

## Problem

dyva routes to an unobservable fleet of discovered hosts. When a client (e.g.
opencode) lets its conversation balloon — the case that prompted this was a
~230k-token prompt with `max_tokens: 32000` against `qwen3.8` — almost nothing in
the pool can both *fit* the prompt and *prefill* it quickly:

- vllm / llama.cpp hosts reject instantly (`request (N tokens) exceeds the
  available context size`), which is fast but useless;
- ollama hosts (the majority, `:11434`) can't cleanly reject an oversized prompt,
  so they accept it and grind — "connected, no answer in 40–100s" — eating the
  full prompt-scaled budget (`_slow_timeout` → up to `TIMEOUT_MAX`);
- the search churns and eventually gives up with nothing to show.

dyva **cannot** shrink the client's context — that content belongs to the client.
But the request carries the client's declared `tools`. The idea is to use them.

Context-hacking the client's history is explicitly **out of scope** (owner call,
2026-10-19). This doc is only about the last-ditch *nudge*, not about dyva
managing anyone's context.

## The idea (mechanism)

Triggered only in the corner we'd otherwise lose (see gating):

1. **Snip the payload.** Drop the oversized conversation. Keep the client's
   declared `tools`.
2. **Ask a model, cheaply.** Send a tiny synthetic prompt to any fast host:
   roughly *"The user is trying to send a message that's too large for your
   context window. Here are the tools you're allowed to run. If any of them would
   reduce the context and let the request succeed, call it now."* plus the tool
   schemas.
3. **Model emits a tool-call** selecting one of the client's own tools.
4. **Forward it back to the client** as the assistant response to the original
   request.
5. **Stop there.** If the client happens to run that tool and self-compacts, it
   will resend a smaller request on its own. dyva does nothing further.

dyva is a *nudge*, not a fixer: it never executes anything, never edits the
client's history. It just hands back a well-formed tool-call and hopes the client
does something useful with it.

## Why an LLM picks the tool, not a regex

"Which of these tools, if any, would reduce the context window?" is a fuzzy
judgment over arbitrary third-party tool names + descriptions. That is exactly
what dyva already delegates to models everywhere (the node-classifier, the
`/v1/systemone` decision models) rather than to pattern matching.

Owner's framing (2026-10-19): **inference is plentiful — not top-shelf, but better
than PCRE.** A decision-model call costs dyva almost nothing and out-guesses any
hand-written pattern at reading intent from a tool catalogue it has never seen.
So selection is a decision-model call, sized and routed like any other small
probe.

## Trigger gating

All three, so this never fires on an ordinary request:

- **Large context only** — prompt over ~50k tokens (`_estimate_prompt_tokens`).
- **After a long fruitless search** — on the order of ~10 min, i.e. the request
  has genuinely failed to place anywhere, not merely "slow".
- **On a user flag** — a manual signal, not automatic. (Fits the existing
  thumbs-down / flag affordance; exact wiring TBD.)

Rarity is a feature: the whole value is in a corner, and the blast radius is
small because it almost never runs.

## The footgun (unresolved)

An LLM picks the tool-call and dyva blind-forwards it to a client that will
**execute** it. The model has no true notion of "reduce the context window," so it
could pick something destructive — "to reduce context I'll call `bash` with `rm`"
or `delete_files` — and the client runs it. That turns a stuck request into
arbitrary tool execution on the user's machine. This is the "shot the footgun at
foot" risk; it is the central open question, not a settled part of the design.

Candidate stances (pick later):

1. **Constrained selector + abstain.** Instruct the decision model to pick ONLY a
   tool that plainly compacts/reduces context, and to return nothing if unsure.
   On abstain, fall back to the honest overflow error (below). No human in the
   loop; leans on the model's judgment + a conservative prompt.
2. **Surface the pick, user confirms.** A user flag already triggered this, so
   show the chosen tool-call and forward only on their OK. Safest against
   destructive picks; needs a confirmation path and a human present.
3. **Accept the risk.** Gated, user-flagged Hail Mary; forward whatever the model
   picks. Simplest; worst tail.

## Load-bearing assumption (verify before ever building)

The trick only nets a *reduction* if the client exposes a tool whose effect is to
shrink its own context. Most agent tools (read, edit, bash, grep) *add* to the
conversation — the client appends the tool result and resends *bigger*. Many
clients do compaction as an internal step keyed on an overflow error, **not** as a
model-callable tool.

**Open fact to check:** does opencode (or whichever client) actually declare a
context-reducing tool in its `tools` array? If not, this trick cannot net-help for
that client no matter how clean the plumbing — and the simpler lever below is the
only thing that would.

## Simpler adjacent lever (not a Hail Mary)

When a prompt provably can't fit anywhere, **fail fast with the real
context-overflow error** (`request (N tokens) exceeds the available context size`)
instead of searching for ~10 min. Agentic clients commonly have native
"context too big → compact → retry" handling keyed on *that* error; today dyva
swallows it and keeps racing, so the client never gets the signal it already knows
how to act on. Surfacing it is safe, simple, and triggers the same self-compaction
through the front door. Worth considering independently of the Hail Mary.

Caveat: respect the standing rule — **no time/host caps bolted onto the race.**
"Fail fast" here means "return the overflow error once we can show nothing fits,"
decided by candidate/ctx-limit knowledge, not a stopwatch on the race.

## When to revisit

- A client we care about is confirmed to expose a model-callable compaction tool.
- The oversized-prompt corner stops being a once-in-a-while opencode hiccup and
  becomes common enough to be worth the footgun-mitigation work.

Until then: parked. The honest-overflow-error lever is the cheaper thing to reach
for first if this corner starts to hurt.
