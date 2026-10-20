"""Flow/DiT models own their sampling knobs — pytest (`uv run pytest tests/test_flow_model_knobs.py -q`).

The bug this pins: a request for z-image (a flow/DiT model whose recipe is ~8
steps / cfg 1.0 and whose sampler dyva chooses) was being rendered at a generic
20 steps / cfg 7 / "Euler" — the dashboard form's SD/SDXL defaults. cfg 7 fries a
cfg-1 flow model and 20 steps is 2.5x too slow. Root cause: the edit-path forced
the caller's (or a generic-defaulted) steps/cfg into _edit_workflow, overriding
the family recipe, and the recipe was looked up from the query, not the model.

Contract (confirmed with the user):
  * when the caller does NOT pin steps/cfg, the FAMILY recipe drives the render
    (z-image -> 8 / 1.0), never a generic 20 / 7;
  * when the caller DOES pin them, the explicit value is honored;
  * the sampler is dyva's regardless — the client's A1111 "Euler" never reaches
    the graph (a capital-E value ComfyUI rejects outright);
  * _sampling_recipe reports those same knobs so the dashboard can SHOW them.

Pure/deterministic — synthetic plan + families, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _zimage_plan():
    fam = next(f for f in dyva.load_edit_families() if f["name"] == "ZImage")
    return {"family": fam, "encode": "TextEncodeZImageOmni",
            "loader": {"kind": "unet", "unet": "z_image_turbo_bf16.safetensors",
                       "clip": "qwen_3_4b.safetensors", "clip_type": "qwen_image",
                       "vae": "ae.safetensors"},
            "model": "z_image_turbo_bf16.safetensors", "out": "SaveImage", "has_scale": False}


def _ksampler(g):
    return next(v["inputs"] for v in g.values() if v["class_type"] == "KSampler")


# ---- unpinned caller -> family recipe, not the generic 20/7 ----------------

def test_unpinned_steps_cfg_fall_back_to_the_family_recipe():
    # params carry None for steps/cfg (caller didn't pin them) — the exact shape the
    # fixed txt2img edit-path now passes.
    g = dyva._edit_workflow(_zimage_plan(), "a brown and white dairy cow", [],
                            {"width": 512, "height": 512, "steps": None,
                             "cfg_scale": None, "seed": 1, "sampler_name": "Euler",
                             "match_source": False})
    k = _ksampler(g)
    assert k["steps"] == 8       # ZImage family default, NOT 20
    assert k["cfg"] == 1.0       # NOT 7 (cfg 7 fries a cfg-1 flow model)


def test_clients_capital_e_euler_never_reaches_the_graph():
    g = dyva._edit_workflow(_zimage_plan(), "a cow", [],
                            {"width": 512, "height": 512, "steps": None,
                             "cfg_scale": None, "seed": 1, "sampler_name": "Euler",
                             "match_source": False})
    assert _ksampler(g)["sampler_name"] != "Euler"   # dyva's, lowercase/host-snapped


# ---- explicit caller pin wins ----------------------------------------------

def test_explicit_steps_and_cfg_are_honored():
    g = dyva._edit_workflow(_zimage_plan(), "a cow", [],
                            {"width": 512, "height": 512, "steps": 30,
                             "cfg_scale": 4.5, "seed": 1, "match_source": False})
    k = _ksampler(g)
    assert k["steps"] == 30
    assert k["cfg"] == 4.5


# ---- _sampling_recipe: what the dashboard shows ----------------------------

def test_recipe_z_image_is_eight_steps_cfg_one_no_sampler():
    r = dyva._sampling_recipe("z_image_turbo_bf16.safetensors")
    assert r["steps"] == 8 and r["cfg"] == 1.0
    assert r["takes_sampler"] is False   # sampler dropdown is inert for a flow model


def test_recipe_flux_cfg_is_one():
    r = dyva._sampling_recipe("flux1-dev.safetensors")
    assert r["cfg"] == 1.0
    assert r["takes_sampler"] is False


def test_recipe_classic_sdxl_keeps_the_generic_sliders():
    # a plain SD/SDXL checkpoint has no override — 20/CFG-7 on the slider IS right
    # for it, and the sampler dropdown stays live.
    r = dyva._sampling_recipe("realvisxl_v5.safetensors")
    assert r["takes_sampler"] is True
    assert "steps" not in r and "cfg" not in r


def test_recipe_turbo_checkpoint_is_few_step():
    # distilled checkpoints are read from the recipe table by FILENAME
    r = dyva._sampling_recipe("dreamshaper_turbo.safetensors")
    assert r["steps"] == 4 and r["cfg"] == 1.0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
