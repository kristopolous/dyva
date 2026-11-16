"""TTS family recipes own their quality params — LongCat gets 3.5B/apg, not 1B noise
(`uv run pytest tests/test_tts_param_hints.py -q`).

The user's bug: short prompts ("hi how are you") came back as noise. Root cause, proven
with speech-to-text over real generations on a live host: `_tts_fill` takes the node's
raw schema default for every field, and LongCatTTS defaults to `LongCat-AudioDiT-1B`,
which produces empty/garbled audio on short text. Whisper transcripts on the same host:

    1B  / cfg / 16  -> ''                 (noise — our default)
    3.5B-bf16 / apg / 32 -> 'Hi, how are you?'   (verbatim)

So a TTS family may carry a `param_hints` recipe that overrides the raw defaults, exactly
as the flow/DiT image families own their steps+cfg. `_apply_param_hints` applies a recipe
to the filled inputs, honoring each field's schema: enum hints are a preference list (first
option that the node actually offers wins), INT/FLOAT hints are clamped to the field's
range, and a field the node doesn't have is skipped. LongCat's recipe picks 3.5B-bf16 and
apg; a host that only offers 1B still gets a valid graph (falls through the preference list).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

# LongCatTTS's real required schema (from a live host's /object_info)
LONGCAT_REQ = {
    "text": ["STRING", {"multiline": True, "default": "Hello!"}],
    "model_path": [["LongCat-AudioDiT-1B", "LongCat-AudioDiT-3.5B",
                    "LongCat-AudioDiT-3.5B-bf16", "LongCat-AudioDiT-3.5B-fp8"], {}],
    "steps": ["INT", {"default": 16, "min": 4, "max": 64}],
    "guidance_method": [["cfg", "apg"], {"default": "cfg"}],
}


def test_enum_hint_picks_first_offered_preference():
    inputs = {"model_path": "LongCat-AudioDiT-1B", "guidance_method": "cfg"}
    dyva._apply_param_hints(inputs, LONGCAT_REQ,
                            {"model_path": ["LongCat-AudioDiT-3.5B-bf16", "LongCat-AudioDiT-3.5B"],
                             "guidance_method": "apg"})
    assert inputs["model_path"] == "LongCat-AudioDiT-3.5B-bf16"
    assert inputs["guidance_method"] == "apg"


def test_enum_hint_falls_through_to_available_option():
    # host offers only the 1B model -> the hint can't force a model it doesn't have
    req = {"model_path": [["LongCat-AudioDiT-1B"], {}]}
    inputs = {"model_path": "LongCat-AudioDiT-1B"}
    dyva._apply_param_hints(inputs, req, {"model_path": ["LongCat-AudioDiT-3.5B-bf16", "LongCat-AudioDiT-3.5B"]})
    assert inputs["model_path"] == "LongCat-AudioDiT-1B"   # unchanged, still valid


def test_int_hint_is_clamped_to_range():
    inputs = {"steps": 16}
    dyva._apply_param_hints(inputs, LONGCAT_REQ, {"steps": 999})
    assert inputs["steps"] == 64            # clamped to max
    dyva._apply_param_hints(inputs, LONGCAT_REQ, {"steps": 32})
    assert inputs["steps"] == 32


def test_missing_field_is_skipped():
    inputs = {"text": "hi"}
    dyva._apply_param_hints(inputs, LONGCAT_REQ, {"speaker": "Dylan"})  # node has no speaker
    assert "speaker" not in inputs


def test_longcat_family_carries_the_recipe():
    fam = dyva._tts_node_family("LongCatTTS")
    assert fam is not None and fam["name"] == "LongCat"
    hints = fam.get("param_hints") or {}
    assert "LongCat-AudioDiT-3.5B-bf16" in (hints.get("model_path") or [])
    assert hints.get("guidance_method") == "apg"


def test_longcat_workflow_uses_the_better_model_not_1b():
    # end-to-end through the real workflow builder: the graph must NOT ship 1B/cfg
    schema = {"LongCatTTS": {"input": {"required": LONGCAT_REQ}, "output": ["AUDIO"]}}
    spec = {"class": "LongCatTTS", "text": "text", "voice": None, "lang": None, "style": None}
    host_info = dict(schema); host_info["PreviewAudio"] = {"input": {"required": {}}}
    graph = dyva._tts_workflow(spec, schema, "hi how are you", None, host_info=host_info)
    g1 = graph["1"]["inputs"]
    assert g1["model_path"] == "LongCat-AudioDiT-3.5B-bf16"
    assert g1["guidance_method"] == "apg"
    assert g1["steps"] >= 32


def test_non_hinted_family_is_unaffected():
    # a plain family with no recipe leaves filled defaults alone
    req = {"text": ["STRING", {"default": ""}], "steps": ["INT", {"default": 20, "min": 1, "max": 50}]}
    inputs = {"steps": 20}
    dyva._apply_param_hints(inputs, req, None)
    assert inputs["steps"] == 20


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
