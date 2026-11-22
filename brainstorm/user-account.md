# User accounts + session tools (brainstorm — IDEA)

Status: idea 2026-11-22. Early thinking, **not committed, no code**. Entry point was
"share agent sessions across two laptops"; it unfolded into "what is the smallest thing
that gives a user an identity," and then hit a wall that is the real content of this doc:
doing it at all **changes what dyva is contractually**. The mechanism is basically
settled. Whether we want it is not.

## The headline (read this first)

Up to now dyva logs everything, but **only for diagnostics** — we are debugging an
under-development server, so capturing traffic is instrumentation, held loosely, nobody's
property. This feature flips that. The moment a session is kept *because the user wants it
back*, we are **logging as a service**: persisting user data as a product, with the
implied contract that comes with it — it's theirs, it's durable, it's private to them,
they can delete it, and we're now a thing that holds people's conversations on purpose.

That is a large architectural **and contractual** shift, and it's the actual decision.
What *bounds* the shift is the load-bearing premise below — **"you can't trust this network
at all"** — which strips most of the responsibility overhead (credentials, data-handling
guarantees) that logging-as-a-service would otherwise drag in. The shift is still real; the
premise just caps how much weight it carries.

**Decision: worth pursuing** (2026-11-22) — compelling feature, clean implementation, and
**100% additive**. Two independent reasons the blast radius is zero: (1) everyone's api-key
field is **blank today**, so parsing a `k=v;` bag is purely additive — present → new
behavior, absent (everyone, now) → exactly today; (2) the storage is a **separate table**
reusing existing scaffolding (see 1.0 below), so nothing in the running demo store is
touched or re-shaped. The contractual shift is real in *policy*, but in *code* it adds and
subtracts nothing from what runs today — which is what tips it from "theorize about" to
"pursue."

## How we got here (the entry problem)

Nobody was really asking for it. `old-agent` (curl a page, pipe through perl, you've got
an agent against the demo server's qwen in under 2 seconds — it's wild) is so
zero-friction that someone ran Hermes with it on two laptops and wanted the **sessions to
follow them**. Atuin could sync it — but you have to sign up, and the instant a signup
wall appears you've lost the exact thing that makes old-agent magic.

So the real target isn't "session sharing." It's: **give a user an identity with zero
enrollment, carried in something the client already sends.**

Note the drift: the ask started as *continue this conversation on the other laptop*
(cross-device resume) and slid toward *search/list my past sessions* (atuin is shell
**history** — searchable, many entries). **The product is not hashed out.** The mechanism
below works for either; the choice between them is an Open below, and it's downstream of
the retention appetite.

## The settled part: the api-key field as an env-var bag

For the public demo the API-key field is **dead weight** — every OpenAI-compatible client
exposes it, the user controls it, and we ignore it. Reuse it as a general
`key=value;`-delimited bag — **basically environment variables for your session:**

```
user=chris;pass=whatever
```

The `k=v;` format is the important part, and it's **deliberately open-ended**. `user` and
`pass` are just the first two keys; the whole point of the format is that later features
stuff more keys in here without a new channel. Don't design it as "the identity string" —
design it as an env bag that currently *carries* identity.

**On the wire** (confirmed with `llcat --curlify` — the reference tool for how keys are
passed; don't assume, curlify it): the api-key field rides a **different header per dialect**.
openai / ollama / llama.cpp → `Authorization: Bearer <bag>`; anthropic → `x-api-key: <bag>`
(no scheme). Some clients also send a bare `Authorization: <bag>`. The reader
(`_apikey_from_request`) normalises all three. **Deploy caveat:** verify the 9ol.es front
proxy actually forwards `Authorization`/`x-api-key` to dyva rather than stripping it — if it's
stripped, the whole opt-in never arrives. (Curlify proves what the *client* sends, not what
dyva *receives*.)

- The token is `hash(user + pass)`. Presence of a `user` is the **opt-in** — no bag,
  nothing is kept, exactly as today.
- **`pass` is optional.** `user=chris` alone is a valid identity (token = `hash(user)`).
- `pass` is **not a credential** — it's optional entropy. Add it when you want
  collision-safety: namespacing so two people who both pick `user=bob` don't land in the same
  bucket, and a nudge away from naming yourself `bob` in the first place. Skip it and you take
  your chances on the bare name.
- **Privacy is elective, not owed.** Plenty of users genuinely don't care who reads their
  sessions — someone doing strictly open-source dev in the open (Chris, for one) can run a
  bare `user=chris`, no pass, and *snoop all day* is a fine answer. A guessable bare name
  isn't a failure for that person, it's the point. `pass` exists for those who want obscurity;
  nobody's forced into it either way.
- This is a **bearer token, not auth** — whoever knows your string *is* you. Fine for a
  pseudonymous "my stuff follows me," but it means the token is a capability; treat the
  value accordingly.
- **Accepted risk, not a hole to plug.** Yes, someone could guess another user's
  `user=x;pass=y` and read their stored sessions — sniff them for secrets. But if you're
  sending secrets through a public experimental proxy in the first place, you have bigger
  security problems than this feature. The store isn't where secrets belong, so credential
  guessing gets you someone's agent chatter, not their keys. Not worth real auth for 1.0.
- **The load-bearing premise: "you can't trust this network at all."** This isn't a gap we're
  tolerating — it's the frame that makes the whole lightweight design coherent. Because dyva
  never promised a trusted channel, it doesn't *owe* the responsibility overhead that a trust
  promise drags in: no real auth, no credential custody, no data-handling guarantees around
  the stored sessions. The zero-trust premise is doing real work here — it's what lets
  identity be a bearer bag, retention be uncapped, and deletion be a soft nicety instead of a
  compliance obligation. State it out loud so nobody later mistakes the lightness for an
  oversight.

### The settings "Sessions" section (front-end)

The demo dashboard's settings gets a **"Sessions"** section for unauthenticated users that
does two things:

- **"Register" a user** — meaningless on the server, it just sets a **localStorage** value
  (your `user`/`pass` bag) so the client remembers it and injects it. Convenience, not an
  account.
- **Delete** — this one is *meaningful*: a **soft delete** of the stored sessions (additive
  flag, data preserved, consistent with the project's never-hard-delete rule). Easy to do,
  so it's in.

This section is also **where the `k=v;` syntax is exposed/documented**, so a user knows how
to set it in whatever client they're using, not just the dashboard.

## The surface: prefixed server-side tools, not magic strings

First instinct was to extend the existing in-band directive family —
`__dyva_info__` / `:next` / `:test` / `:hosts` (dispatched before routing at
`dyva/__init__.py:13730`, current-turn-only detection via `_content_contains`,
multi-client response shaping, pipes-to-file). Session commands *would* drop into that.
**Rejected: too hacky** — a magic string in the message content is the wrong contract for
a durable feature.

Cleaner (the pet-sitter pattern, a separate project): make them **real function-calling
tools the model invokes**, namespaced by a random prefix.

- **Engine already exists.** `_run_agent_tools` (`dyva/__init__.py:18325`) is a server-side
  tool loop: completion → intercept `tool_calls` by name → run in-process → append the
  assistant turn + tool results → next round (`AGENT_MAX_ROUNDS`). It already dispatches
  `web`, the scratchpad, and a per-agent file sandbox as `elif name == …` branches.
  `list_sessions` / `search_sessions` are **new branches in that switch**. Execution is done.
- **The random base62 prefix** (~64 bits) is what pet-sitter adds and we don't have: it
  namespaces injected tools so ours **cannot collide** with the caller's own tool names.
  Interception is then unambiguous — prefix matches → ours, handle it; no prefix → relay
  straight to the model untouched. **Chosen prefix: `m4x1aaz40I9z`** (12 base62 chars ≈ 71
  bits — enough entropy). So the injected tools are `m4x1aaz40I9z_list_sessions`,
  `m4x1aaz40I9z_search_sessions`, etc.
- **Trigger = already tool-calling.** If the api-key carries the kv pairs **and** the
  request already has `tools` (it's agentic), dyva appends its prefixed session-tools to
  the array before sending, then runs the intercept loop on the user's behalf.

### Why this shape is nice

The **double gate** — opted-in *and* already tool-calling — keeps it **off the sacred text
path**. A plain chat with no tools, or an anonymous caller, sees nothing different. And
there's a clean symmetry: **identity rides the api-key field, control rides the tools,
both in-band** — `old-agent` stays curl-and-perl and needs to know nothing about either.

## The prerequisite (the honest dependency)

That loop today only runs for dyva's **own** `dyva_agent` sub-agent jobs, not for a user's
inference call. So the real prerequisite is not the session tools — it's the thing already
flagged as pending: **extending server-side tool-calling to user requests.** Once dyva can
inject-and-intercept on a user's own agent loop, session tools are a small addition on top.

## The 1.0 version

Don't overthink the product. We **already RAG the front-end conversations with
sqlite-vec** (the vec extension is wired in at `dyva/__init__.py:38`, and the embedding
path — `_embed_one`, `/v1/embeddings`, cosine — is all there ~`13095–13316`). This is the
**same pattern**: embed, store, semantic-search. Reuse it.

Two decisions make it bounded instead of a swamp:

- **Key the context by its affinity id.** We already compute `_affinity_key(messages)`
  (crc32 of the first-N user messages) and carry it in `_affinity_ctx` per request. Key the
  stored session by that. Every turn resends a *growing* array, so without a key you'd store
  N near-duplicate copies of one conversation; keying by affinity id means **one row per
  context**, updated in place. The dedup key is already in hand — nearly free.
  - **Granularity = the affinity setting, not a fixed number.** N is `AFFINITY_PREFIX_MSGS`,
    a per-host setting (**4 on Benjamin**). The prefix only *stabilizes* once that many user
    messages exist, so a conversation's first few turns re-key (bounded by N) before settling
    onto one row. That's the intended "no per-turn flood," not strictly one-row-per-convo —
    and because capture calls `_affinity_key` directly, the behavior tracks the setting if you
    retune it.
- **Retention: uncapped for 1.0.** Endless store, no TTL, no cap — just keep them. The
  affinity-id dedup already stops the per-turn duplicate flood, so growth is one row per
  *context*, not per message, which is slow enough to leave unbounded at first. Get a feel
  for real volume once it works, *then* set a reasonable cutoff. (Design it as a policy knob
  so capping later is a config change, not a rewrite.)

**Forks collapse, and that's fine for 1.0.** Two branches that share the same prefix map to
the same affinity id, so a tool that forks a conversation can't be told apart from its
parent. *Too bad* — accepted limitation, not a bug to chase now.

So the retention/logbook/memory fork lands at: **basic embeddings RAG over deduped,
policy-bounded contexts.** Searchable (the "search my sessions" the ask drifted toward),
without being an unbounded raw transcript log. `search_sessions` is a vec query;
`list_sessions` is a scan of the keyed rows. **That's 1.0.**

**Embedding is lazy, at query time** (the sem index's build-on-search), so **capture is a pure
write with no embed cost** — the embedding is a one-time, on-demand cost paid when a user
actually searches, not on every insertion. Ingress stays cheap; the expensive part only
happens for users who use search, and only for what's new since they last searched.

### Schema: a separate table, reusing the scaffolding — 100% additive

**Decision (2026-11-22): user sessions are a separate table. The existing demo chats stay
global and untouched — "global until further notice." That stays.** We do *not* bolt a
`userhash` onto the live `chats`/`items` store or touch its eviction; we stand up a parallel
store beside it and **reuse most of the existing scaffolding** rather than fork it.

What's there to reuse (looked at 2026-11-22):

- **`SEMINDEX_DB`** already has the whole machinery: open-with-vec0-loaded (`_get_sem_db`,
  `dyva/__init__.py:14424`), an `items` mirror table rowid-aligned with
  `vec_items USING vec0(embedding float[dim])` (`:14442`, `:14464`), a corpus diff that only
  (re)embeds new/changed rows (`_sem_plan`, `:14524`), and a lazy drop-and-rebuild when the
  pinned embed model changes (`_sem_check_model`, `:14472`). All of that is store-agnostic —
  point it at a sessions table and it just works.
- **The existing demo path is left exactly as-is:** `chats(id,title,created,data)` (`:14311`),
  its `_chat_upsert` (`:14335`), its global `CHATS_KEEP` eviction (`:14348`), and the
  docs+chats corpus (`:14510`). No column added, no behavior changed. (This also voids the
  eviction-cap collision from the earlier draft — we're simply not in that table.)

The parallel store:

- A **sessions table** keyed by `(userhash, affinity_id)` — the affinity-id dedup gives one
  row per context, updated in place (so no per-turn duplicate flood), and it's **uncapped**
  (the 1.0 retention call).
- Its **own vec0 index** via the reused sem-index helpers. Scope KNN with a **userhash
  partition column** — `vec0(embedding float[dim], userhash text)`, filter `WHERE userhash=?`
  *inside* the search — so a user's `search_sessions` only ever sees their own rows and can't
  be starved by global neighbors.
- The **soft-delete flag** lives on this table too.

**Why this is the version worth building:** it's **100% additive** — a new table plus reused
helpers, nothing existing touched or re-shaped. That's the whole case for pursuing it now:
compelling feature, clean implementation, zero disturbance to the global demo store. The
"contractual shift" at the top is real in *policy*, but in *code* it adds and subtracts
nothing from what runs today.

## Open

- **The contract (blocking).** Are we willing to be a service that holds users'
  conversations on purpose? This is the whole decision; the rest is downstream. Chris: "I'll
  have to think it through." Everything in "The 1.0 version" is contingent on a yes here.
- **Retention cutoff (deferred by design).** 1.0 is uncapped on purpose — set a TTL/cap
  only after real volume shows where "reasonable" is.
- **How arms-length does dyva stay in the user's loop?** The prefix guarantees it only ever
  executes its own tools and relays the rest — but it does mean the proxy is a **live
  participant** in a real user's tool loop, consuming a round when the model calls
  `search_sessions`. Comfortable with that, or keep it more detached?
- **Storage + TTL + delete.** Wherever sessions live, the "I don't hold data" instinct wants
  a hard TTL, a cap, and a user-facing delete (`forget_me` as another prefixed tool?).
- **Bearer-token stakes.** The string travels in the Authorization header and will land in
  client logs / proxies. `pass` being fake lowers the stakes, but a resumable identity in a
  header is still secret-shaped.
