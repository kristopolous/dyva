"""Video reference-graph path — pytest (`uv run pytest tests/test_video_reference_graph.py -q`).

dyva prefers a VERIFIED ComfyUI graph (exported from a real host, under
dyva/workflows/) over a hand-built one: validate it fits THIS host, remap its
file inputs to the host's own installed variants, bypass a missing LoRA, patch
prompt/seed/(start image), submit; fall back to the family builders only if no
reference graph fits. This pins the pure pieces of that path:

  * remap matches a file to a host variant by its leading family token, then
    prefers the candidate sharing the MOST descriptive tokens — so a t2v model is
    never swapped for the host's i2v one, nor high_noise for low_noise;
  * a missing checkpoint/encoder/VAE is UNRESOLVED (→ fall back), but a missing
    LoRA is BYPASSED (optional style adapter, not structural);
  * patch sets prompt/seed/start-image without disturbing wired inputs;
  * mode is i2v iff the graph loads an image.

Pure/deterministic — synthetic graphs + /object_info, no network. Also guards the
shipped dyva/workflows/*.json assets stay well-formed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _info(classes):
    """classes: {class_type: {field: [options]}} -> a minimal /object_info."""
    return {ct: {"input": {"required": {f: [opts] for f, opts in fields.items()}}}
            for ct, fields in classes.items()}


# ---- _wf_remap_files: variant-aware file remapping -------------------------

def test_remap_prefers_the_variant_sharing_the_most_tokens():
    # a t2v/high_noise request must not be swapped for i2v or low_noise just because
    # the coarse "wan2" family token matches all three.
    graph = {"1": {"class_type": "UNETLoader",
                   "inputs": {"unet_name": "wan2.2_t2v_high_noise_14b_fp16.safetensors"}}}
    host = _info({"UNETLoader": {"unet_name": [
        "wan2.2_i2v_high_noise_14b.safetensors",
        "wan2.2_t2v_high_noise_14b_bf16.safetensors",   # the right one
        "wan2.2_t2v_low_noise_14b.safetensors"]}})
    g, unresolved = dyva._wf_remap_files(graph, host)
    assert g["1"]["inputs"]["unet_name"] == "wan2.2_t2v_high_noise_14b_bf16.safetensors"
    assert unresolved == []


def test_remap_leaves_a_file_already_present_on_the_host():
    graph = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8.safetensors"}}}
    host = _info({"CLIPLoader": {"clip_name": ["umt5_xxl_fp8.safetensors", "t5xxl.safetensors"]}})
    g, unresolved = dyva._wf_remap_files(graph, host)
    assert g["1"]["inputs"]["clip_name"] == "umt5_xxl_fp8.safetensors"
    assert unresolved == []


def test_remap_missing_family_is_unresolved_so_caller_falls_back():
    # an LTX graph on a WAN-only host: the family token doesn't match anything ->
    # unresolved, which is the signal to fall back to the builders.
    graph = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "ltxv_13b_dev.safetensors"}}}
    host = _info({"UNETLoader": {"unet_name": ["wan2.2_t2v_14b.safetensors"]}})
    _, unresolved = dyva._wf_remap_files(graph, host)
    assert unresolved and unresolved[0][0] == "UNETLoader"


# ---- LoRA bypass (optional) vs required-file (structural) ------------------

def test_missing_lora_is_bypassed_and_consumers_rewire_to_its_source():
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_t2v_14b.safetensors"}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": "speedup_lora.safetensors"}},
        "3": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "seed": 0}},
    }
    host = _info({"UNETLoader": {"unet_name": ["wan2.2_t2v_14b.safetensors"]},
                  "LoraLoaderModelOnly": {"lora_name": ["some_other_lora.safetensors"]},
                  "KSampler": {}})
    g, unresolved = dyva._wf_remap_files(graph, host)
    assert "2" not in g                               # the LoRA node is gone
    assert g["3"]["inputs"]["model"] == ["1", 0]      # KSampler now reads the UNET directly
    assert unresolved == []


def test_bypass_rewires_both_model_and_clip_for_a_full_loraloader():
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "m.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "c.safetensors"}},
        "3": {"class_type": "LoraLoader",
              "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": "x.safetensors"}},
        "4": {"class_type": "KSampler", "inputs": {"model": ["3", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 1]}},
    }
    assert dyva._wf_bypass(g, "3") is True
    assert "3" not in g
    assert g["4"]["inputs"]["model"] == ["1", 0]      # output slot 0 -> model source
    assert g["5"]["inputs"]["clip"] == ["2", 0]       # output slot 1 -> clip source


def test_missing_vae_is_structural_not_bypassed():
    graph = {"1": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}}}
    host = _info({"VAELoader": {"vae_name": ["totally_different_ae.safetensors"]}})
    _, unresolved = dyva._wf_remap_files(graph, host)
    assert unresolved and unresolved[0][1] == "vae_name"   # fall back, never silently bypass


# ---- _wf_patch -------------------------------------------------------------

def test_patch_sets_prompt_seed_and_start_image():
    g = {
        "1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "template prompt"}},
        "2": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": 0}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 0}},
        "4": {"class_type": "LoadImage", "inputs": {"image": "template.png"}},
    }
    out = dyva._wf_patch(g, "a dancing cat", 12345, start_image="start.png")
    assert out["1"]["inputs"]["value"] == "a dancing cat"
    assert out["2"]["inputs"]["noise_seed"] == 12345
    assert out["3"]["inputs"]["seed"] == 12345
    assert out["4"]["inputs"]["image"] == "start.png"


def test_patch_does_not_clobber_a_wired_seed_input():
    # a seed fed by a link ([node, slot]) is graph wiring, not a fillable value.
    g = {"1": {"class_type": "KSamplerAdvanced", "inputs": {"noise_seed": ["9", 0]}}}
    out = dyva._wf_patch(g, "p", 7)
    assert out["1"]["inputs"]["noise_seed"] == ["9", 0]


def test_patch_leaves_the_original_graph_untouched():
    g = {"1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "orig"}}}
    dyva._wf_patch(g, "new", 1)
    assert g["1"]["inputs"]["value"] == "orig"   # operates on a copy


# ---- _ref_ckpt + mode detection + shipped assets ---------------------------

def test_ref_ckpt_finds_the_checkpoint_or_unet():
    assert dyva._ref_ckpt({"1": {"class_type": "UNETLoader",
                                 "inputs": {"unet_name": "wan2.2_t2v_14b.safetensors"}}}) \
        == "wan2.2_t2v_14b.safetensors"
    assert dyva._ref_ckpt({"1": {"class_type": "Note", "inputs": {"text": "hi"}}}) is None


def test_shipped_reference_workflows_load_and_classify_mode():
    refs = dyva._load_ref_workflows()
    assert refs, "expected bundled dyva/workflows/*.json reference graphs"
    for r in refs:
        assert r["mode"] in ("i2v", "t2v")
        assert any(isinstance(v, dict) and "class_type" in v for v in r["graph"].values())
        # mode is i2v iff the graph loads an image
        has_load_image = any(isinstance(v, dict) and v.get("class_type") == "LoadImage"
                             for v in r["graph"].values())
        assert (r["mode"] == "i2v") == has_load_image


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
