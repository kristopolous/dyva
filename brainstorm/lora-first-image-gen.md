# LoRA-first image generation (brainstorm — PARKED)

Status: parked 2026-10-22. Direction leaning **peer-filter**, not yet committed. No code written.

## The problem

LoRAs on the fleet are a **per-host attribute with near-zero cross-host overlap**, not a
portable capability. Read-only survey (2026-10-22):

| | a1111 | comfy |
|---|---|---|
| hosts in pool | 40 | 465 |
| reachable | 27 | 203 |
| have >=1 lora | 17 | 130 |
| lora files on them | 1,908 | 1,610 |

~147 live hosts carry loras; **2,384 distinct files**; long tail — only `krx.pth` (79 hosts,
probably a shared upscaler not an art lora) and the *accelerator* loras
(`Qwen-Image-Lightning-4steps`, minimax turbo) span many hosts. Every style/character lora
lives on 1–7 hosts, mostly 1.

Consequence: a **global "pick any lora" dropdown is a lie** — pick it, the request races
(media routes one-host-per-request like text), lands on a host that lacks it, nothing happens.

## The inversion (the good idea)

Flip the selection axis. Instead of "pick a model/host, then its loras," make the **lora the
primary selector**: name a lora -> route to whatever host has it -> use whatever compatible
model is there. For this fleet, "I want this lora" is already an implicit host selector, so the
per-host stickiness stops being a liability and becomes the routing key.

This is the **same pattern dyva already runs for models**: today you name a model and
`_image_pool` narrows the race to hosts that have it. Lora-first is that machinery pointed at a
different attribute. If several hosts have the lora (the accelerators), you get redundancy/failover
for free.

## Two framings of "it's a new mode"

- **Separate mode** — an explicit "LoRA mode" UI pane + flow beside the normal one. Clearer to a
  user but a parallel pipeline: more surface, duplication. Edges toward architectural (spec).
- **Peer filter (preferred)** — lora becomes a first-class narrowing constraint *alongside* model;
  the "mode" is emergent from which field you fill:
  - model only -> narrow by model (today)
  - lora only -> narrow by lora, infer a compatible checkpoint
  - both -> intersect
  - neither -> today's auto-pick
  One codepath, bounded, no parallel pipeline. Recommended.

## What's actually new (either framing)

1. **A lora->host index.** Pool records carry `models: [...]` today but not loras. Fold the probe
   (a1111 `GET /sdapi/v1/loras`; comfy `GET /object_info/LoraLoader` lora_name enum, newer combo
   format = options in attrs) into the existing discovery/host-probe pass and store `loras: [...]`
   on each a1111/comfy record. Then pool-narrowing is trivial. Staleness behaves like model
   staleness: route, and if the lora's gone, fail over.
2. **Checkpoint inference from lora family.** This is the one genuinely new *behavior* — the
   current pipeline always picks a checkpoint first; lora-first needs "no model specified, infer
   one." Loras are base-family-specific (an SDXL lora over a Flux checkpoint silently no-ops), so
   "any model on the host" is really "any *compatible* model":
   - **Best-effort:** apply over whatever checkpoint; a family mismatch is a dud. Simplest.
   - **Family-preferring:** when the host has several checkpoints, pick one whose family matches
     the lora (filename heuristics `XL`/`pony`/`flux`/`qwen`/`sd15`; a1111 lora metadata sometimes
     carries `ss_base_model_version`). Degrades to best-effort when family unknown.

## Mechanical wiring (cheap, already mostly there)

- **Apply:** `<lora:name:weight>` prompt convention works on BOTH backends from one codepath —
  a1111 parses it natively (passthrough), comfy parses it out and splices a `LoraLoader`.
- **Comfy splice** is the exact inverse of the existing `_LORA_BYPASS`/`_wf_bypass`
  (`dyva/__init__.py` ~15862) which today *removes* a LoraLoader and rewires model/clip consumers.
- **Graph builders** to splice into: hardcoded checkpoint graph (~13484), `_t2i_unet_workflow`
  (~17070), `_edit_workflow` (~17395).
- **Routing:** mirror the `model_filter` narrow in `_image_pool` (~12611) with a lora filter.
- **Enum/family resolution:** parallel to existing clip/vae resolution in `_t2i_unet_plan` using
  `_enum_options(info, "LoraLoader", "lora_name")`.

## Open questions for when we resume

1. Peer-filter vs separate mode (leaning peer-filter).
2. Best-effort vs family-preferring checkpoint inference (family-preferring is the better UX;
   confirm the heuristics are worth it).
3. Weights / multiple loras — the `<lora:name:weight>` convention supports both; how much to expose
   in the tool param vs UI.
4. Special-case the accelerator loras (4-step speedups) since they're the one broadly-portable
   subset?
5. Discoverability surface for the long tail — how does anyone browse 2,384 mostly-singleton loras?
   (search_models already has `type` plumbing incl. a lora kind.)

See memory `lora-is-a-per-host-attribute`.
