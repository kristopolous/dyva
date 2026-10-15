# Cached ComfyUI workflow templates (authoritative references)

Official Comfy-Org templates for models whose graph shape dyva builds programmatically
in `_edit_workflow`/`_t2i_*`. Cached here so the canonical node wiring is available
offline and so new-model recipes can be checked against the real thing rather than guessed.

- `image_qwen_image_2_1_image_edit.json` — Qwen-Image-2.1 (unified t2i+edit). The real
  graph lives in its `definitions.subgraphs[0]`: one `TextEncodeQwenImage21` node emits
  positive[0] / negative[1] / latent[2]; KSampler(cfg 1, 25 steps, euler/simple) -> VAEDecode.
  Source: Comfy-Org/workflow_templates.
