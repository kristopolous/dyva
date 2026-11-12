# Model query syntax

In dyva the **model name is a query**, not an exact id — the same syntax everywhere a
model is named: the `model` field of any endpoint (`/v1/chat/completions`, `/api/chat`,
`/api/generate`), the chat model picker, the dashboard's Server-Room filter, and the
map's compare boxes. A query selects a *slice of the pool*; dyva races the hosts that
match. A query with no `;` facet never touches the reputation DB.

## Quick reference

| Syntax | Means | Example |
|---|---|---|
| `name` | Containment match (case-insensitive) | `qwen` → every qwen variant |
| `*` `?` | Globs | `qwen*27b`, `flux*2`, `llama3?70b` |
| `*` alone | Any model | `*` |
| `name$` | End-anchor: exact name, skip quant/finetune variants | `qwen3.8:27b$` |
| `a,b` | Fallback chain — try `a`, then `b` (spaces optional) | `gemma3,qwen` |
| `>` `<` `>=` `<=` + size | Filter by recorded disk size (decimal, `gb`=1e9) | `>10gb`, `qwen <=4gb`, `<700mb` |
| `>` `<` `>=` `<=` + date | Filter by release date (year/month/day) | `qwen>2026`, `>2026-02-15` |
| `cap/model` | Capability + model pairing | `edit/wan`, `vision/qwen` |
| `;key op value` | Host-reputation facet (whitelisted `host_status` columns) | `;tps>40`, `;state=good`, `;ttft<0.5`, `;fail_smoke=0` |
| `;caps=…;model` | **Override** required capabilities (replaces inference) | `;caps=text;qwen3` |
| `;live` `;dead` | Liveness meta (map) | `qwen;live` |
| `;cloud` `;nocloud` | Cloud/non-cloud meta (map) | `;nocloud` |
| `;<service>` `;<provider>` | Service/provider meta (map) | `;ollama`, `;comfyui` |

Everything is **order-free and composable** — `qwen;tps>40`, `;tps>40;qwen` and
`tps>40;qwen` all mean the same thing.

## Details worth knowing

- **Containment vs. `$`.** A bare name matches as a substring, so `qwen3.8:27b` also
  drafts in `qwen3.8:27b-q4_K_M` and `-abliterated`. A trailing `$` (the only regex
  metacharacter honoured) anchors the end to the exact name.
- **Why comma for fallback.** It's shell-safe and absent from every model name in the
  survey — unlike `%` (URL encoding) or `/` (the capability/model pairing).
- **Size keeps unknowns; date drops them.** Narrowing a *name* query by size keeps
  candidates of unknown size (a backend that reports none isn't dropped), but a bare
  `>10gb` returns only models known to satisfy it. A date filter **excludes** anything
  it can't place in time (dates come from a bundled leaderboard,
  `dyva/model-release-dates.json`). Dates default unspecified units to `01`
  (`2026` → `2026-01-01`), so `>2026` is "2026 and newer."
- **Reputation facets want data.** `;key op value` filters by the *serving host's*
  measured reputation, not the model. The key is whitelisted against real `host_status`
  columns and the value is always bound (never interpolated). A thin or cold reputation
  DB simply returns little — it's only as good as the data behind it.
- **`;caps=` overrides inference.** dyva normally infers the caps a request needs from
  its messages (image → `vision`, `tool_calls` → `tools`, audio → `audio`). The
  `;caps=cap1,cap2;` directive states them explicitly and **replaces** that inference —
  the motivating case is attaching an image for a *tool* while routing to a smart
  **non-vision** model. Caps: `completion`/`text`, `vision`/`image`, `tools`, `audio`,
  `thinking` (aliases accepted); `completion` is always implied; a blank or all-unknown
  directive falls back to inference. Unlike reputation facets (which host), this sets
  what the *model* must do.

## Stacking

Facets, filters and the name compose freely:

```
qwen>2026>5gb              newer, large qwens
qwen >5gb ;tps>40 ;state=good   large qwen, fast host, good standing
gemma3,qwen3.6             try gemma3, fall back to qwen3.6
;caps=text;qwen3           any qwen3, no vision required even with an image attached
abliterated                for when you want to slip into something more comfortable
```
