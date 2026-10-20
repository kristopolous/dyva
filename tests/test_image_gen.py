"""Image-generation workflow + routing suite — pytest (`uv run pytest tests/test_image_gen.py`).

Covers the comfy image path end to end at the unit level, no live host:

  * The sampler-strip contract: the comfy flow/DiT/encode workflow builders
    (_t2i_unet_workflow, _edit_workflow) run z-image / Flux / Qwen-Image etc.,
    for which a CLIENT-supplied A1111 sampler is ALWAYS wrong — a capital-E
    'Euler' is rejected by ComfyUI outright ("value_not_in_list"). dyva must
    IGNORE params['sampler_name'] and pick the family default, snapped onto the
    host's KSampler enum. These tests pin that: no KSampler node may ever carry
    the client's 'Euler'.
  * Host selection: _is_image_gen_model keeps real generators and drops parts
    (VAE/CLIP/text-encoder/LoRA/controlnet) so a host isn't chosen for a query
    it can only satisfy with an auxiliary file.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _ksamplers(graph):
    return [n for n in graph.values()
            if str(n.get("class_type", "")).startswith("KSampler")]


def _info_with_samplers(sampler_opts, sched_opts=("simple", "normal")):
    """A minimal /object_info whose KSampler advertises these combos."""
    return {"KSampler": {"input": {"required": {
        "sampler_name": [list(sampler_opts), {}],
        "scheduler": [list(sched_opts), {}],
    }}}}


# ---- _t2i_unet_workflow (flux / z-image / qwen-image UNET t2i) -------------

def _t2i_plan(sampler=None):
    spec = {"res": 1024, "steps": 20, "cfg": 3.5}
    if sampler:
        spec["sampler"] = sampler
    return {"loader": "UNETLoader", "model": "z_image_turbo_bf16.safetensors",
            "clip": "qwen_3_4b.safetensors", "clip_type": "qwen_image",
            "vae": "flux2-vae.safetensors", "latent": "EmptySD3LatentImage",
            "out": "SaveImage", "spec": spec}


def test_t2i_strips_client_euler_no_info():
    g = dyva._t2i_unet_workflow(_t2i_plan(), "a cat", "", {"sampler_name": "Euler"})
    ks = _ksamplers(g)
    assert ks, "no KSampler node built"
    for n in ks:
        assert n["inputs"]["sampler_name"] == "euler"   # family default, not client 'Euler'


def test_t2i_snaps_family_sampler_onto_host_enum():
    # Family asks for 'Euler' (wrong case); host advertises 'euler' -> snapped.
    info = _info_with_samplers(["euler", "dpmpp_2m", "res_multistep"])
    g = dyva._t2i_unet_workflow(_t2i_plan(sampler="Euler"), "a cat", "",
                                {"sampler_name": "Euler"}, info=info)
    for n in _ksamplers(g):
        assert n["inputs"]["sampler_name"] == "euler"


def test_t2i_falls_back_to_first_host_option_when_euler_absent():
    info = _info_with_samplers(["res_multistep", "dpmpp_2m"])   # no euler at all
    g = dyva._t2i_unet_workflow(_t2i_plan(), "a cat", "", {"sampler_name": "Euler"},
                                info=info)
    for n in _ksamplers(g):
        assert n["inputs"]["sampler_name"] in ("res_multistep", "dpmpp_2m")
        assert n["inputs"]["sampler_name"] != "Euler"


# ---- _edit_workflow (z-image / Flux.2 / Qwen-Image encode families) --------

def _edit_plan(style="encode", sampler=None):
    fam = {"style": style, "images": [], "steps": 8, "cfg": 1.0}
    if sampler:
        fam["sampler"] = sampler
    return {"family": fam,
            "loader": {"kind": "unet", "unet": "z_image_turbo_bf16.safetensors",
                       "clip": "qwen_3_4b.safetensors", "clip_type": "qwen_image",
                       "vae": "flux2-vae.safetensors"},
            "encode": "TextEncodeZImageOmni", "out": "SaveImage"}


def test_edit_strips_client_euler_no_info():
    g = dyva._edit_workflow(_edit_plan(), "a cat", [], {"sampler_name": "Euler"})
    ks = _ksamplers(g)
    assert ks, "no KSampler node built"
    for n in ks:
        assert n["inputs"]["sampler_name"] == "euler"


def test_edit_snaps_onto_host_enum():
    info = _info_with_samplers(["euler", "dpmpp_2m"])
    g = dyva._edit_workflow(_edit_plan(sampler="Euler"), "a cat", [],
                            {"sampler_name": "Euler"}, info=info)
    for n in _ksamplers(g):
        assert n["inputs"]["sampler_name"] == "euler"


def test_edit_reference_style_also_strips_euler():
    g = dyva._edit_workflow(_edit_plan(style="reference"), "a cat", [],
                            {"sampler_name": "Euler"})
    for n in _ksamplers(g):
        assert n["inputs"]["sampler_name"] != "Euler"


def test_no_graph_node_ever_carries_capital_euler():
    # Defensive: a client 'Euler' must not survive anywhere in either graph.
    for g in (dyva._t2i_unet_workflow(_t2i_plan(), "x", "", {"sampler_name": "Euler"}),
              dyva._edit_workflow(_edit_plan(), "x", [], {"sampler_name": "Euler"})):
        assert "Euler" not in str(g)


# ---- host selection: _is_image_gen_model ----------------------------------

def test_is_image_gen_model_keeps_generators_drops_parts():
    for m in ("checkpoints/flux1-dev.safetensors",
              "diffusion_models/z_image_turbo_bf16.safetensors",
              "checkpoints/qwen_image_2512_fp8_e4m3fn.safetensors",
              "unet_gguf/flux1-schnell-Q4_0.gguf"):
        assert dyva._is_image_gen_model(m) is True, m
    for m in ("text_encoders/qwen_3_4b.safetensors",
              "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
              "vae/flux2-vae.safetensors",
              "loras/FLUX.1-Turbo-Alpha.safetensors",
              "controlnet/Z-Image-Turbo-Fun-Controlnet-Union.safetensors",
              "wan2.1_t2v_14B.safetensors"):
        assert dyva._is_image_gen_model(m) is False, m


# ---- _image_pool: image routing uses the SAME rules as text ----------------
# Pins that image candidate selection honours reputation tiers, 👎/👍 session
# votes, the fallback chain, and structural hard-exclusion — across a1111 AND
# comfyui in ONE ordered pool, so image generation is not a separate scheme.

import pytest   # noqa: E402

K = dyva.IMG_KEY


def _srv(host, models, service="comfyui"):
    return {"server": host, "service": service, "models": models}


Z = _srv("http://z:1", ["diffusion_models/z_image_turbo_bf16.safetensors"])
F = _srv("http://f:1", ["diffusion_models/flux1-dev.safetensors"])


@pytest.fixture
def clean(monkeypatch):
    """Neutral routing state: nothing incompatible, no load, no votes."""
    monkeypatch.setattr(dyva, "load_incompatible", lambda: set())
    monkeypatch.setattr(dyva, "is_active", lambda h: False)
    monkeypatch.setattr(dyva, "_inflight", {})
    monkeypatch.setattr(dyva, "_session_skip_hosts", set())
    monkeypatch.setattr(dyva, "_session_prefer_hosts", set())


def _marks(good=(), maybe=(), bad=(), unreachable=()):
    return (set(good), set(maybe), set(bad), set(unreachable))


def test_pool_honours_fallback_chain_order(clean):
    # z*image hosts must ALL come before flux*2 hosts, same as text routing —
    # even though flux is passed first and sorts first alphabetically.
    pool = dyva._image_pool([], [F, Z], "z*image,flux*2", _marks(), None)
    assert pool == ["http://z:1", "http://f:1"]


def test_pool_thumbsdown_host_is_dropped(clean, monkeypatch):
    monkeypatch.setattr(dyva, "_session_skip_hosts", {dyva._norm_host("http://z:1")})
    pool = dyva._image_pool([], [Z, F], "z*image,flux*2", _marks(), None)
    assert "http://z:1" not in pool and "http://f:1" in pool


def test_pool_thumbsup_host_floats_to_front(clean, monkeypatch):
    # f is 'good', z is unknown — but a 👍 on z floats it above the better tier.
    monkeypatch.setattr(dyva, "_session_prefer_hosts", {dyva._norm_host("http://z:1")})
    pool = dyva._image_pool([], [Z, F], None, _marks(good=[f"http://f:1 {K}"]), None)
    assert pool[0] == "http://z:1"


def test_pool_structural_incompatible_is_dropped(clean, monkeypatch):
    monkeypatch.setattr(dyva, "load_incompatible", lambda: {f"http://z:1 {K}"})
    pool = dyva._image_pool([], [Z, F], "z*image,flux*2", _marks(), None)
    assert "http://z:1" not in pool


def test_pool_reputation_tier_orders_good_before_unknown(clean):
    pool = dyva._image_pool([], [Z, F], None, _marks(good=[f"http://f:1 {K}"]), None)
    assert pool == ["http://f:1", "http://z:1"]   # f good, z unknown


def test_pool_tiers_across_a1111_and_comfy_together(clean):
    # A GOOD comfy host must beat a BAD a1111 host — one pool, not a1111-then-comfy.
    a = _srv("http://a:1", ["sd_xl_base_1.0.safetensors"], service="a1111")
    pool = dyva._image_pool([a], [Z], None,
                            _marks(good=[f"http://z:1 {K}"], bad=[f"http://a:1 {K}"]), None)
    assert pool == ["http://z:1", "http://a:1"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
