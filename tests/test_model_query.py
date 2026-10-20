"""model_query_match comma fallback — pytest (`uv run pytest tests/test_model_query.py`).

The flexible model-query syntax uses ',' as an ordered set of alternatives (the same
fallback chain text routing splits on via _split_fallback). The image/edit/video/comfy
paths filter with model_query_match(), so the comma syntax must work there too:
`z*image,flux,qwen` matches a name ANY alternative matches, and _match_rank() ranks by
the FIRST alternative matched so the listed order is the preference order. ',' never
appears in a real model name, so comma-free queries are unchanged (pure superset).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

CANDS = ["z-image-turbo.safetensors", "flux1-dev.safetensors",
         "qwen-image.safetensors", "sd_xl_base.safetensors"]


def test_comma_matches_any_alternative():
    q = "z*image,flux,qwen"
    assert dyva.model_query_match("z-image-turbo.safetensors", q) is True
    assert dyva.model_query_match("flux1-dev.safetensors", q) is True
    assert dyva.model_query_match("qwen-image.safetensors", q) is True
    assert dyva.model_query_match("sd_xl_base.safetensors", q) is False


def test_rank_honors_listed_preference_order():
    q = "z*image,flux,qwen"
    matched = [c for c in CANDS if dyva.model_query_match(c, q)]
    ranked = sorted(matched, key=dyva._match_rank(q))
    # z-image first (listed first), then flux, then qwen — even though 'flux1-dev'
    # and 'qwen-image' sort before 'z-image-turbo' alphabetically.
    assert ranked == ["z-image-turbo.safetensors", "flux1-dev.safetensors",
                      "qwen-image.safetensors"]


def test_trailing_or_empty_comma_segments_ignored():
    assert dyva.model_query_match("flux1-dev.safetensors", "flux,") is True
    assert dyva.model_query_match("flux1-dev.safetensors", ",flux,") is True


def test_comma_free_queries_unchanged():
    # glob keeps exact meaning
    assert dyva.model_query_match("flux2-dev", "flux*2") is True
    # separator-insensitive literal still works
    assert dyva.model_query_match("flux2-dev", "flux.2") is True
    # end-anchor still excludes finetune variants
    assert dyva.model_query_match("qwen3.8:27b-abliterated", "qwen3.8:27b$") is False
    # empty = any
    assert dyva.model_query_match("anything", "") is True
    # a non-matching single pattern still doesn't match
    assert dyva.model_query_match("sd_xl_base.safetensors", "z*image") is False


def test_is_image_gen_model_keeps_generators_drops_parts():
    # Real generators: checkpoints / diffusion / UNET files.
    for m in ("checkpoints/flux1-dev.safetensors",
              "diffusion_models/z_image_turbo_bf16.safetensors",
              "checkpoints/qwen_image_2512_fp8_e4m3fn.safetensors",
              "sd_xl_base_1.0.safetensors",
              "unet_gguf/flux1-schnell-Q4_0.gguf"):
        assert dyva._is_image_gen_model(m) is True, m
    # Parts (VAE/CLIP/text-encoder/LoRA/controlnet) and non-image families — must
    # NOT make a host a match. The text-encoder case is the live bug: a flux2/z-image
    # host carries text_encoders/qwen_3_4b.safetensors, which must not match 'qwen'.
    for m in ("text_encoders/qwen_3_4b.safetensors",
              "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
              "vae/flux2-vae.safetensors",
              "loras/FLUX.1-Turbo-Alpha.safetensors",
              "controlnet/Z-Image-Turbo-Fun-Controlnet-Union.safetensors",
              "clip_gguf/Qwen3-4B-Instruct-Q6_K.gguf",
              "wan2.1_t2v_14B.safetensors"):
        assert dyva._is_image_gen_model(m) is False, m


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
