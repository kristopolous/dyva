# Auth-walled hosts (brainstorm — IDEA)

Status: idea 2026-11-05. Observation + proposed handling, **not built**.

## Observation (live, 2026-11-05)

`http://124.222.186.23:8899@muse-glimmer` is an **LM Studio** server with auth turned on.
Every request 401s with:

> `An LM Studio API token is required to make requests to this server, but none was
> provided using the Authorization header using the 'Bearer' scheme … Learn more at:
> https://lmstudio.ai/docs/developer/core/authentication.`

And it was quick-tested **twice in 5 seconds across two different workers** (`5cadd` at
08:08:10, `99e87` at 08:08:15) — i.e. it's being **re-raced, not quarantined**. An
auth-walled host will 401 forever (we have no token and won't get one), so every re-race
burns a race slot and a worker, and spams the feed with the same 401.

## The point

This is **not a bad host and not a honeypot** — it's an honest server we simply *can't use*.
The right move is the same shape as `UNREACHABLE`: recognize the 401 "token required" signal
and **quarantine the host** so routing stops offering it and stops re-racing it. It's a clean
signal the host volunteers (not a probe we send), so there's no arms race — cheap and safe to
trust.

Almost certainly **per-host, not per-(host,model)**: the LM Studio token gates the whole
server, so one 401 condemns every model on it, like unreachability.

## Open

- **Verify current classification.** How does dyva class a 401 today? The log proves it's at
  least *retryable* right now (two races in 5s), so whatever bucket it lands in, it isn't
  quarantining. Find where the race records the quick-test outcome and what a 401 maps to.
- **Marker.** Reuse the `UNREACHABLE_KEY` sentinel pattern (additive, reversible, provenance
  kept) or a dedicated `AUTH_WALL` sentinel? Leaning: a distinct marker, because the *reason*
  differs from unreachable (reachable, answering, just gated) and a future token could unlock
  it — so it should be reversible and self-describing, not conflated with "dead."
- **Scope of the signal.** Match on the 401 + the LM Studio phrasing, or any 401 with a
  `Bearer`/`Authorization` hint? Generalize cautiously — a 401 from a *honeypot* is a
  different animal; this marker is for honest auth walls only.
