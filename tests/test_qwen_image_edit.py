"""Qwen-Image-2.1 edit graph. TextEncodeQwenImage21 is a UNIFIED node: one instance
emits positive[0] / negative[1] / latent[2] and REQUIRES prompt + negative_prompt
(STRING) + resolution (INT) + optional image_1.. reference slots. dyva's old two-encode
+ separate-latent `encode` path produced `required_input_missing: resolution` /
`negative_prompt` live. This proves _edit_workflow now builds the unified graph.

Node spec + wiring verified against a live host's /object_info and the official
image_qwen_image_2_1_image_edit.json template (cached in dyva/comfy-templates/).

Run: .venv/bin/python -m pytest tests/test_qwen_image_edit.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

LOADER = {"kind": "unet", "unet": "qwen_image_2.1_int8_convrot.safetensors",
          "clip": "qwen3vl_8b.safetensors", "clip_type": "qwen_image",
          "vae": "qwen_image_2.1_vae.safetensors"}
FAM = {"name": "QwenImage21", "style": "encode", "encode_unified": True,
       "resolution": 1024, "images": ["image_1", "image_2", "image_3"],
       "cfg": 1.0, "steps": 25}
PLAN = {"family": FAM, "encode": "TextEncodeQwenImage21", "loader": LOADER}


def _build(images, params=None):
    return dyva._edit_workflow(PLAN, "make it snowy", images, params or {})


def _node(g, cls):
    return next((v for v in g.values() if v.get("class_type") == cls), None)


def test_single_unified_encode_node_with_all_required_inputs():
    g = _build(["ref0.png"], {"negative_prompt": "blurry, low quality"})
    encs = [v for v in g.values() if v["class_type"] == "TextEncodeQwenImage21"]
    assert len(encs) == 1, "exactly ONE encode node (not a positive+negative pair)"
    inp = encs[0]["inputs"]
    # the two inputs whose absence caused the live failure:
    assert inp["resolution"] == 1024
    assert inp["negative_prompt"] == "blurry, low quality"
    # and the rest of its required/optional inputs:
    assert inp["prompt"] == "make it snowy"
    assert "clip" in inp and "vae" in inp


def test_reference_images_use_autogrow_list_not_flat_keys():
    g = _build(["ref0.png", "ref1.png"])
    enc = _node(g, "TextEncodeQwenImage21")
    # COMFY_AUTOGROW_V3 `images` wants a LIST of image links. Flat image_1/image_2 keys PASS
    # submit-validation but crash at execution ("unexpected keyword argument 'image_1'").
    imgs = enc["inputs"].get("images")
    assert isinstance(imgs, list) and len(imgs) == 2
    assert not any(k.startswith("image_") for k in enc["inputs"])   # no flat keys
    assert g[imgs[0][0]]["class_type"] == "LoadImage"


def test_ksampler_takes_pos_neg_and_latent_from_the_one_node():
    g = _build(["ref0.png"])
    enc_id = next(k for k, v in g.items() if v["class_type"] == "TextEncodeQwenImage21")
    ks = _node(g, "KSampler")
    assert ks["inputs"]["positive"] == [enc_id, 0]
    assert ks["inputs"]["negative"] == [enc_id, 1]
    assert ks["inputs"]["latent_image"] == [enc_id, 2]   # latent comes FROM the encode node
    assert ks["inputs"]["cfg"] == 1.0


def test_no_second_encode_no_separate_latent_nodes():
    g = _build(["ref0.png"])
    classes = [v["class_type"] for v in g.values()]
    assert classes.count("TextEncodeQwenImage21") == 1
    # the unified node is the latent source — none of the old latent-building nodes appear
    for dead in ("VAEEncode", "EmptySD3LatentImage", "EmptyLatentImage", "CLIPTextEncode"):
        assert dead not in classes, f"{dead} should not be in the unified graph"
    assert _node(g, "VAEDecode") and _node(g, "SaveImage")


def test_text_to_image_zero_references_still_valid():
    # unified weights serve t2i too: no refs -> no image_ slots, latent still from the node
    g = _build([])
    enc = _node(g, "TextEncodeQwenImage21")
    assert "images" not in enc["inputs"]   # no refs -> no images input (t2i)
    assert enc["inputs"]["resolution"] == 1024
    # t2i has no reference to follow, so it generates into a sized empty canvas
    assert _node(g, "EmptySD3LatentImage") is not None
    assert _node(g, "KSampler")["inputs"]["latent_image"] == [
        next(k for k, v in g.items() if v["class_type"] == "EmptySD3LatentImage"), 0]


def test_resolution_override_clamped_to_multiple_of_32():
    g = _build(["ref0.png"], {"resolution_px": 1500})
    assert _node(g, "TextEncodeQwenImage21")["inputs"]["resolution"] == 1472  # 1500 floored to a /32


def test_default_edit_follows_reference_via_encode_latent():
    # no explicit aspect -> the node's own latent[2] (keeps the reference's shape)
    g = _build(["ref0.png"])
    assert _node(g, "EmptySD3LatentImage") is None
    assert _node(g, "KSampler")["inputs"]["latent_image"] == [
        next(k for k, v in g.items() if v["class_type"] == "TextEncodeQwenImage21"), 2]


def test_explicit_aspect_forces_a_sized_empty_latent():
    # "make it a tall/portrait image" must change the ratio, not stay square -> a sized
    # EmptySD3LatentImage (16-channel, as base Qwen-Image t2i uses) feeds KSampler instead.
    g = _build(["ref0.png"], {"aspect": "tall"})
    el = _node(g, "EmptySD3LatentImage")
    assert el is not None
    w, h = el["inputs"]["width"], el["inputs"]["height"]
    assert h > w, "tall -> portrait canvas"
    assert _node(g, "KSampler")["inputs"]["latent_image"] == [
        next(k for k, v in g.items() if v["class_type"] == "EmptySD3LatentImage"), 0]
    # the reference still guides the result through the encode node's images input
    assert isinstance(_node(g, "TextEncodeQwenImage21")["inputs"].get("images"), list)


def test_clip_prefers_qwen3vl_8b_not_minimax_encoder():
    # A host can carry several qwen3vl CLIPs; TextEncodeQwenImage21 needs qwen3vl_8b, NOT
    # MiniMax's qwen3vl_32b_minimax (that produced a wrong-encoder render that "succeeded").
    fam = next(f for f in dyva.load_edit_families() if f["name"] == "QwenImage21")
    hints = dyva._hints(fam.get("clip"))
    both = ["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "qwen3vl_8b_int8_convrot.safetensors"]
    assert dyva._pick(both, *hints) == "qwen3vl_8b_int8_convrot.safetensors"
    assert dyva._pick(list(reversed(both)), *hints) == "qwen3vl_8b_int8_convrot.safetensors"
    # a host with ONLY the minimax encoder can't run Qwen-Image -> no match (host rejected,
    # not driven with the wrong encoder)
    assert dyva._pick(["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"], *hints) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
