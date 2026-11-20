"""dyva routes TTS to OpenAI-compatible hosts (LocalAI, Chatterbox) by passthrough
(`uv run pytest tests/test_openai_tts.py -q`).

TTS was ComfyUI-only (node-graph driving). But many discovered hosts speak the exact
OpenAI `/v1/audio/speech` shape dyva already uses — no graph, just POST-and-relay — and
produce clean voiced speech (STT-verified on live LocalAI + Chatterbox hosts). This is
far more "dyva" (a router) than driving ComfyUI graphs.

`_openai_tts_body` builds the per-service request. Chatterbox (devnen) is the fussy one:
its `voice` must be a predefined FILENAME ("Abigail.wav") — the display name or a foreign
voice name 404s — so we resolve the requested voice against the host's advertised voice
list (case-insensitively, tolerating a missing .wav) and fall back to the first voice.
LocalAI/generic just needs {model, input} with an optional voice passthrough.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

CB_VOICES = ["Abigail.wav", "Adrian.wav", "Dylan.wav"]


def test_chatterbox_body_uses_filename_voice():
    path, body = dyva._openai_tts_body("chatterbox", "ChatterboxTurboTTS",
                                       "hi there", "Dylan", CB_VOICES)
    assert path == "/v1/audio/speech"
    assert body["input"] == "hi there"
    assert body["voice"] == "Dylan.wav"        # resolved to the predefined filename
    assert body["model"] == "chatterbox"       # chatterbox ignores model; send the literal
    assert body["response_format"] == "wav"


def test_chatterbox_voice_resolution_is_case_and_extension_tolerant():
    _, b1 = dyva._openai_tts_body("chatterbox", "x", "t", "dylan", CB_VOICES)
    _, b2 = dyva._openai_tts_body("chatterbox", "x", "t", "Dylan.wav", CB_VOICES)
    assert b1["voice"] == "Dylan.wav" and b2["voice"] == "Dylan.wav"


def test_chatterbox_unknown_or_missing_voice_falls_back_to_first():
    # a voice from some OTHER engine (or none) must not 404 the host
    _, b1 = dyva._openai_tts_body("chatterbox", "x", "t", "Serena", CB_VOICES)
    _, b2 = dyva._openai_tts_body("chatterbox", "x", "t", None, CB_VOICES)
    assert b1["voice"] == "Abigail.wav" and b2["voice"] == "Abigail.wav"


def test_chatterbox_with_no_known_voices_omits_voice():
    # nothing to resolve against -> don't invent a filename
    _, b = dyva._openai_tts_body("chatterbox", "x", "t", "Dylan", [])
    assert "voice" not in b or not b["voice"]


def test_localai_body_is_generic_openai_shape():
    path, body = dyva._openai_tts_body("localai", "tts-1", "hello", "alloy", None)
    assert path == "/v1/audio/speech"
    assert body == {"model": "tts-1", "input": "hello", "voice": "alloy"}


def test_localai_without_voice_omits_voice():
    _, body = dyva._openai_tts_body("localai", "tts-1", "hello", None, None)
    assert body == {"model": "tts-1", "input": "hello"}


def test_openai_tts_service_detection():
    # chatterbox is always a TTS source; localai only when it has a tts model
    assert dyva._is_openai_tts_service("chatterbox") is True
    assert dyva._is_openai_tts_service("localai") is True
    assert dyva._is_openai_tts_service("fish") is True
    assert dyva._is_openai_tts_service("comfyui") is False
    assert dyva._is_openai_tts_service("ollama") is False


# --- fish-speech (fishaudio) ------------------------------------------------
# Discovered by graflex as service "fish"; every host speaks POST /v1/tts with a
# bare {text} body returning audio bytes (its own dialect, NOT /v1/audio/speech).
# It rides the same passthrough race; only path + body differ.

def test_fish_body_is_v1_tts_with_bare_text():
    path, body = dyva._openai_tts_body("fish", "fish", "hello there", None, None)
    assert path == "/v1/tts"
    assert body == {"text": "hello there"}


def test_fish_body_ignores_openai_voice():
    # Fish's bare /v1/tts doesn't take an OpenAI voice id (voice cloning is a
    # separate /v1/references capability only some hosts have) — don't smuggle
    # a foreign voice name into the body and confuse the host.
    _, body = dyva._openai_tts_body("fish", "fish", "hello", "alloy", None)
    assert body == {"text": "hello"}


def test_fish_is_always_a_tts_model():
    # Like chatterbox, a fish host is always a TTS source (models=["fish"]);
    # it qualifies even if the host advertised no model list.
    assert dyva._openai_tts_model({"service": "fish", "models": ["fish"]}) == "fish"
    assert dyva._openai_tts_model({"service": "fish"}) == "fish"


# --- models shape tolerance -------------------------------------------------
# load_servers() normalizes every record's `models` to canonical Form A (a dict
# {name: {caps:[...]}}). _openai_tts_model must read through that like the rest of
# dyva (model_names) — a raw models[0] on the dict raised KeyError: 0 and 500'd the
# WHOLE /v1/audio/speech request (the crash took out host discovery, so no host ran).

def test_chatterbox_model_from_form_a_dict():
    # the regression: post-normalization models is a dict, not a list.
    s = {"service": "chatterbox", "models": {"ChatterboxTTS": {"caps": []}}}
    assert dyva._openai_tts_model(s) == "ChatterboxTTS"


def test_chatterbox_model_from_legacy_list():
    # pre-normalization / graflex-fresh shape still works (back-compat).
    assert dyva._openai_tts_model(
        {"service": "chatterbox", "models": ["ChatterboxTTS"]}) == "ChatterboxTTS"


def test_chatterbox_with_empty_or_missing_models_defaults():
    # no advertised model -> the literal the request body sends anyway.
    assert dyva._openai_tts_model({"service": "chatterbox", "models": {}}) == "chatterbox"
    assert dyva._openai_tts_model({"service": "chatterbox"}) == "chatterbox"


def test_localai_tts_model_found_in_form_a_dict():
    # localai qualifies on a TTS-looking model name, dict shape included.
    s = {"service": "localai", "models": {"gpt-4": {}, "tts-1": {"caps": []}}}
    assert dyva._openai_tts_model(s) == "tts-1"


def test_localai_without_a_tts_model_is_none():
    assert dyva._openai_tts_model({"service": "localai", "models": {"gpt-4": {}}}) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
