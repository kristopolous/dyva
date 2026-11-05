# Dumb gateway + app tier (brainstorm — IDEA)

Status: idea 2026-11-04. Early thinking, **not committed, no code**. Entry point was
"keep a job running when the phone screen turns off"; it escalated into "what is dyva's
job, minimally." The split below is the thesis; detachable jobs are the forcing function,
not the whole of it.

## How we got here (the entry problem)

Want: start a generation on a phone, lock the screen, come back, it finished (or is still
going). The tempting answer is **web workers — and it's wrong**. A worker moves work off
the main thread but doesn't change the *tab's* lifecycle: screen-lock suspends the whole
page and the worker freezes with it. iOS Safari suspends almost immediately on lock (no
Background Fetch); Android Chrome throttles a hidden tab then *freezes* it after minutes.
There is no client-side trick that reliably survives screen-off on both.

The thing that survives screen-off is **not keeping the job on the client**: submit → get
a job id → server runs it detached → client reconnects by id and catches up. This is the
async-inference pattern everyone converged on (OpenAI batch/background, Replicate
prediction-id + webhook).

But the moment you detach the job from the client socket, you **throw away your automatic
reaper**. Today a dropped socket is the garbage collector: client leaves → upstream
aborts → slot freed. Detach it and you owe that reaping back explicitly, and the DIY
versions always leak here — jobs that hang, buffers never collected, and (the dyva sting)
**a held host connection** squatting a slot against the connection ceiling, which also
makes us look like a bad citizen to that host. A real job system needs three timers, not
one: a **lease/grace window** (no reader cancels — but only after long enough to survive a
lock; this number is the whole design), a **hard deadline** (the host itself can hang),
and a **result TTL + byte cap**. And the part everyone forgets: **cancellation must
propagate** — reaping a job has to actually close the upstream connection, or the host
side stays open and you've got the hang you were trying to avoid.

## The reframe (the actual idea)

The job system was a symptom. The disease: **dyva is two things wearing one coat** — a
host-racing *gateway* and a chat *application* (web UI, spreadsheet, screen-grab, stats
dashboard, activity feed, media orchestration). Those are different planes. Detachable
jobs are just the first feature whose seam between them hurts enough to notice.

So don't ask "where do jobs live." Ask "**what is dyva's job, stated as narrowly as
possible**," and move everything else up a tier.

### The dividing line

A clean cleave: **the gateway owns talking to hosts correctly and politely; the app tier
owns what a user is doing.**

| Stays in the gateway (the "dumb" data plane) | Moves to an app tier (the control plane) |
|---|---|
| racing / fanout / worker pool | chat web UI |
| tiers, reputation, banker, cooldowns | the spreadsheet feature |
| connection ceiling, soft-fail, hedge | screen grabbing |
| honeypot avoidance | stats dashboard / activity feed rendering |
| ollama↔openai dialect translation | media-gen orchestration (the user-facing flow) |
| num_ctx / num_batch resize (load-bearing) | session state |
| think-handling, scheme / num_predict fixes | **detachable jobs + the reaper** |

"Dumb" ≠ trivial. The gateway keeps the hard commodity logic; it's just *one concern* —
be a fast, correct, polite client of a pool of flaky hosts — and so becomes a thing you
can restart, scale horizontally, and replace without anyone noticing. The app tier is
stateful by nature, so job durability and the reaper live there **for free**, next to the
state they reap. The app server becomes just another client of the gateway, talking the
same OpenAI/Ollama contract an agentic coder would.

### Two things fall out

- **Good:** detach gets *easier*, not harder. Reconnect-by-id is natural in the app tier —
  it already holds the session. dyva staying stateless means it has no session to orphan,
  so it's trivially restartable.
- **Hold onto this:** the seam doesn't disappear, it **concentrates**. A dumb gateway's
  one remaining hard duty is to propagate a client disconnect into a host-side abort. That
  becomes dyva's *defining* behaviour instead of one of many — good for clarity, but now
  load-bearing for the whole architecture.

## Governing constraint — stay as light as dyva

dyva has stayed **extremely painless and hassle-free** — clone it and run; no Postgres to
install, no docker, no containers, no compose eating 15GB and 4 cores, no service to babysit,
nothing to migrate. That is the whole point, and it's the invariant the split *serves*, not
a nicety to trade away in the name of it. The RAM/cores weight is only a symptom — the real
test any option must pass is **"does it add a setup step or an operational chore?"** If yes,
it's out, however good its feature list. Adding a stateful infra stack to solve a job-detach
problem is "simplification" in name and the opposite in fact. So the split is a **logical
boundary**, implemented as a light module
or at most a second small same-stack (aiohttp) process — possibly on another box purely for
blast radius — **not a deployment**. State is an in-process table plus a sqlite file (dyva
already speaks sqlite), never a database server. Anything that fails this test is rejected
on weight no matter how good its feature list.

## Prior art — lift the pattern, reject the stack

The generic "durable job, HTTP submit, poll-by-id, webhook, real reaper" is a mature
category — but the mature *products* are heavy because they solve multi-tenant / distributed
durability we don't have. For one operator, the whole job layer is a dict + a sqlite row +
an asyncio reaper: a few hundred lines on the stack dyva already runs. So the survey below
is for **what to lift (the recipe)**, not what to deploy. Surveyed 2026-11-04:

- **Hatchet** — Postgres + docker-compose, aimed at AI background tasks. The *shape*
  reference (one-dep self-host, submit/poll/webhook), but Postgres+docker is already heavier
  than all of dyva — **rejected as a dependency**; keep only the API shape.
- **Temporal** — durable workflows, Cassandra/MySQL/Postgres + optional Elasticsearch. The
  far heavy end. **Rejected on weight**; mentioned only as what we are *not* doing.
- **nTorque** — Python, Postgres-as-source-of-truth webhook queue. Right pattern, wrong
  weight (Postgres), and the listing looked dated (one entry said Py2.7). Pattern reference
  only.
- **Hoglah** (`github.com/gellsmore-svg/hoglah`) — the spirit-match: a *local*, light async
  job queue *for Ollama* — submit, get id, monitor, retrieve, completion callback. Obscure,
  unknown maturity, didn't confirm reconnect-by-id — but it's both domain- and weight-matched,
  so it's the one worth actually reading (crib or confirm-then-build).
- **OxiLLaMa** (Rust) — OpenAI-compatible `/v1/batches`: create/retrieve/cancel/list by id,
  disk-spooled, no infra tax. The *standardized light* shape of detach — clients may already
  speak it.

Default leaning: **build the light version** (crib Hoglah / the batch shape if useful),
because the pattern is small and the products' bulk is all stuff we don't need.

**Caveat that survives whatever we pick:** it doesn't remove the propagation seam. The
reaper frees a host slot only by closing its connection to dyva and trusting dyva to abort
upstream. And the job layer must go *through* dyva, never hold host connections directly
around it — doing so forks routing / reputation / banker into two places with different
manners.

## Why this fits the project

dyva wants to be an **ambient commodity**, not a frontier app. A dumb gateway *is* that
commodity — boring, stable, restartable. The chat features are the research surface; they
want to iterate fast and occasionally break. Splitting lets each be what it is instead of
dragging the other's constraints along.

## Costs / risks (why this is surgery, not an afternoon)

- dyva is **live, editable-installed, pinned to the running dir, ambient**. Re-drawing its
  boundaries is operating on a running system.
- Must be **incremental, not big-bang** — define the boundary, nail the gateway's public
  contract, peel *one* feature first as a proof (detachable jobs is the natural candidate
  since it's the forcing function and net-new).
- **Do not break num_ctx resize** — it's load-bearing correctness and it's a host-talking
  concern, so it stays in the gateway.

## Open questions

- **UNVERIFIED keystone:** does dyva *today* abort the upstream when its client disconnects
  mid-stream? Everything above pivots on this and it hasn't been checked in the code yet.
  First thing to confirm the moment this leaves the whiteboard.
- The **grace-window** number — long enough to survive a screen-lock (minutes), short enough
  not to squat a host slot. The delicate value.
- **Build vs adopt** — leaning build-light (dict + sqlite + asyncio reaper). The only adopt
  candidate that passes the hassle-free test is something as light as Hoglah, and only if a
  read shows it's real; the heavy brokers are out on principle.
- A few features sit near the dividing line — e.g. media-gen: the *routing* is a host
  concern (gateway), the *user-facing flow* is app. Where exactly does each half land?
- The gateway's **public contract** — presumably OpenAI + Ollama passthrough so the app
  tier and any other client talk to it identically. Pin it down.
- If it graduates: this is brainstorm-tier. Committing means a spec under
  `docs/superpowers/specs/` and a real migration plan.
