# Self-update tab — agentic refresh of churning reference data + alias routes (brainstorm)

Status: brainstorm, NOT built (2026-11-08). Origin: tried to do a few image edits,
burned every frontend's free tier, turned to dyva chat — and it reached for **old,
lousy** image-edit models, because dyva has no current signal for *which* edit model
is good right now. That signal exists in the world (public leaderboards) but it
**churns daily**, and a new model drop takes ~7–10 days to even show up in our own
surveys. We don't want to be the updater (hand-feeding Claude), and we don't want a
brittle CSS/XPath scraper that dies the next time a leaderboard redeploys its front
end. So: **an LLM is the parser**, run over dyva's *own* pool.

## Thesis

A new dashboard tab of **packaged updater-agents**. Each updater is a shipped,
curated recipe that goes out over dyva's own infrastructure, reads a known public
source, and refreshes the user's local copy of a reference artifact — pricing,
release dates, model classifiers, and (the new one) **capability evals**. The whole
point is that these facts are *constantly changing*, and we can keep them current
**agentically and for free**, on the same pool we route inference to.

This is a *large class* of things done one way, not a one-off. Image-edit evals are
just tenant #1.

## The class boundary (the defining cut)

The agent's hands already exist: `WEB_SEARCH` (Keenable's keyless public endpoint,
`WEB_SEARCH_URL`) + the `lightpanda → headless-Chrome → curl` read chain + tool-calling
on a pool model. So "an agent over dyva infra" is **real today**, not new plumbing.
But it can only refresh facts that **live on a public page it can read**.

**IN — web-sourced reference facts (refreshable this way):**

| Artifact | What it is | Note |
|---|---|---|
| `dyva/model-prices.json` | the pricing sheet | the original motivator |
| `dyva/model-release-dates.json` | release dates | **already bends routing** — the `qwen>2026-02` date predicates read it |
| `dyva/model-labs.json` | model → lab/org | |
| `graflex/model-classifier.json` | model-string → image/video/audio/other | |
| `graflex/gradio-taxonomy.json`, `dyva/node-classifier.json`, `gguf-labels.json`, `nsfw-models.json` | "what kind of thing is this" buckets | slower churn |
| **`evals` (NEW)** | per-capability model rankings (image-edit, gen, video, chat…) from public leaderboards | **doesn't exist yet** — tenant #1 creates it |

**OUT — pool-measured facts (an LLM must NEVER touch these):**
`host-status.db` (reputation), `honeypot-responses.json`, `graflex/bogus-fingerprints.json`,
`survey.json`. These are **measured from our own pool**, not fetched. The whole
boundary in one line: **web-sourced facts in, pool-measured facts out.**

## The packaged updater (the design unit)

The guardrails *are* the artifact — hallucination has to survive all of them and the
version diff to ever reach the router, and it's revertible if it does. Same ethos as
graflex's atomic writer refusing a >40% pool drop: **trust by construction, not by
hoping.** An updater ships with:

- **source URL(s)** — fixed, curated, not "go find pricing."
- **extraction target** — the exact schema to emit, handed to the model.
- **validators** — generic (schema conformance, types/ranges, "every prior key still
  present", a graflex-style diff-size guard) + per-updater (price within x of last,
  date not in the future, a ranking is a permutation of known models).
- **versioned write + one-click rollback** — never an in-place overwrite; keep N
  versions, a current pointer, a visible diff.

One shared **agent-runner** executes any recipe: fetch → extract (pool model + web
tools) → validate → version → write. Adding a new member of the class = shipping one
more recipe.

## Alias routes (the payoff primitive)

The reason the eval refresh matters. Instead of making a human (or a model) write our
PCRE-ish routing query, name it:

    =image-edit

An **alias** is a *named, redefinable routing path*. Proposed mechanic: it expands,
in a **pre-pass before `find_servers`**, into an ordinary model-query in the existing
syntax — comma fallback chain, globs, `;`-facets and all:

    =image-edit   →   qwen-image-edit,flux-kontext;state=good

`=` is its own sigil, alongside `;` (facets) and `$` (exact-name anchor). No new
routing primitive — just a named indirection resolved before the race.

**This is where the eval lands:** the eval updater's job is to **re-author the alias
body** — "best edit models right now, best-first" — as a new *version* of the alias.
The eval never touches `find_servers`; it just rewrites a string.

The host-vs-model tension dissolves cleanly into two layers:

- **the alias (eval-fed) picks which *models* to ask for** — a model-prior from the
  outside world.
- **the measured host-reputation race still picks the *host*** — pool-truth, unchanged.

Leaderboards carry their own facets (open-weight flag, size, task strengths), so the
updater **filters to self-hostable, size-sane, task-ranked at extraction time** and
writes the chain best-first. No fragile frontier-name → ollama-tag mapping to invent.

`aliases.json` thus becomes **another versioned member of the churn-class the tab
maintains** — shipped defaults, eval-updated, hand-editable, rolled back like the rest.

## The closed-family safety invariant (where this meets the honeypot work)

Nothing *real* in the pool self-hosts `claude-opus` — the only things answering to
that name are the LocalAI **costumes**. So an alias that expands to a closed-weight
family isn't just useless, it's a **honeypot magnet**: `=best-chat → claude-opus`
would resolve onto exactly the bait hosts the closed-catalogue detector exists to
sink.

So the open-weights filter is not a nicety, it's a **security boundary** — and we
already built the guard this session: `graflex._closed_family()`. The updater
framework reuses it as a shared validator: **any alias expansion — eval-written,
hallucinated, OR hand-edited — that names a closed family is refused before it can be
a routing target.** The predicate that low-ranks the costumes at survey time is the
same one that won't let an alias aim at them. One system, two threads.

## Hard invariants

1. **Additive + reversible + versioned.** Never an in-place overwrite; every write is
   a new version with a diff and one-click rollback. "We can change a version; we
   cannot undo data destruction."
2. **Web-sourced only.** Updaters touch the IN list; the OUT list (reputation,
   honeypot, bogus-fingerprints, survey) is off-limits to any agent, full stop.
3. **No alias may expand to a closed-weight family** (`_closed_family` guard) — it can
   only resolve onto honeypots.
4. **Guardrails ship with the recipe.** Fixed source URLs + schema + validators; a
   bad extraction must fail a validator or be caught in the version diff, never land
   silently.
5. **The eval touches routing only through the alias body** — never `find_servers`
   directly. Host-reputation stays the sole authority on *which host*.

## Open knobs (need your call before a plan)

- **Updater form:** a declarative **recipe** (data: URL + schema + validator set +
  target alias/artifact) run by one shared runner — or a hand-written **unit per
  source**? The recipe path is what makes "a large class" cheap; the unit path is more
  flexible per-source. (My lean: declarative recipe + shared runner, escape-hatch for
  the odd weird source.)
- **Promote model:** auto-apply once validators pass (rollback is the safety net) —
  or stage → show diff → you promote? (Routing-steering data argues for at least a
  visible diff.)
- **Closed-family guard:** **hard-reject** the whole alias, or **warn-and-strip** the
  closed entries and route on the open remainder? (Hard-reject is louder/safer; strip
  is forgiving of a half-open leaderboard row.)
- **Scheduling:** on-demand button only ("issue an agent") — or recurring, given the
  7–10 day churn (piggyback `/refresh` like gossiper, or a cron)?
- **Which pool model** runs extraction (reliability of structured output), and the
  fallback when the pool's best extractor is cold.

## Scope / first tenant

Prove the full chain on one real case: an **image-edit eval updater → `=image-edit`
alias**, end to end. That exercises every piece — recipe, runner, validators,
versioning, the alias resolver, the closed-family guard — on a single tenant before
generalizing the tab.

## What I will NOT do (until this is approved + the knobs are set)

- No overwrite of any reference JSON — versioned writes only, or nothing.
- No agent write to a pool-measured artifact (reputation / honeypot / survey).
- No alias that can expand to a closed-weight family.
- No change to the hard-won routing primitives in `find_servers` — the alias resolver
  is a pre-pass *in front of* it, not a change *to* it.
- No live run of any updater against real artifacts until the form + promote model +
  guard policy are chosen.
