"""Text-to-speech graph building + node viability — pytest (`uv run pytest tests/test_tts.py -q`).

TTS is a *node* feature, not a model file: dyva classifies a host's installed
node-class names against the node-classifier, proves the whole 3-node graph can
be driven, then builds engine / direct-audio graphs. Each assertion below pins a
contract with a named real-world failure the code comments record:

  * an ENGINE node (Qwen3TTSEngineNode, F5TTSEngineNode…) only configures the
    voice; its "instruct" field is a style directive, so the user's sentence goes
    to the downstream UnifiedTTSTextNode, never into instruct.
  * the AUDIO output slot is looked up, never assumed index 0 (UnifiedTTSTextNode
    is AUDIO,STRING; others put STRING first).
  * a host with no audio saver, a node that outputs STRING (GoogleTranslate), an
    empty voice-enum socket (ElevenLabs), or a non-style empty required STRING are
    all rejected BEFORE submit — not discovered minutes into a render.

Pure/deterministic — synthetic /object_info, no network.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _node(req, outputs):
    return {"input": {"required": req}, "output": outputs}


def _fam(name):
    return next(f for f in dyva.load_node_classifier() if f["name"] == name)


SAVE = {"SaveAudio": _node({"audio": ["AUDIO", {}]}, [])}
STR = ["STRING", {"default": ""}]


# ---- _tts_workflow: engine path -------------------------------------------

def _engine_host():
    return {
        "Qwen3TTSEngineNode": _node(
            {"instruct": STR, "voice_preset": [["Ethan", "zero-shot"], {}], "language": [["en", "zh"], {}]},
            ["TTS_ENGINE"]),
        "UnifiedTTSTextNode": _node(
            {"TTS_engine": ["TTS_ENGINE", {}], "text": STR, "narrator_voice": STR, "seed": ["INT", {"default": 0}]},
            ["AUDIO", "STRING"]),
        "SaveAudio": _node({"audio": ["AUDIO", {}]}, []),
    }


ENGINE_SPEC = {"class": "Qwen3TTSEngineNode", "text": "instruct",
               "voice": "voice_preset", "lang": "language", "style": None}


def test_engine_puts_words_in_unified_node_not_instruct():
    host = _engine_host()
    g = dyva._tts_workflow(ENGINE_SPEC, host, "Hello there", None, host)
    # the user's sentence is spoken by node 2, NOT stuffed into the engine's instruct
    assert g["1"]["inputs"]["instruct"] == ""
    assert g["2"]["class_type"] == "UnifiedTTSTextNode"
    assert g["2"]["inputs"]["text"] == "Hello there"
    assert g["2"]["inputs"]["TTS_engine"] == ["1", 0]


def test_engine_save_node_pulls_audio_from_unified_output():
    host = _engine_host()
    g = dyva._tts_workflow(ENGINE_SPEC, host, "hi", None, host)
    assert g["3"]["class_type"] == "SaveAudio"
    assert g["3"]["inputs"]["audio"] == ["2", 0]   # UnifiedTTSTextNode AUDIO is slot 0


def test_engine_fills_a_real_voice_and_language():
    host = _engine_host()
    g = dyva._tts_workflow(ENGINE_SPEC, host, "你好世界", None, host)
    assert g["1"]["inputs"]["voice_preset"] == "Ethan"   # not the zero-shot placeholder
    assert g["1"]["inputs"]["language"] == "zh"            # CJK text -> zh


# ---- _tts_workflow: direct-audio path -------------------------------------

def test_direct_node_gets_text_and_saver_reads_its_audio_slot():
    host = dict(SAVE, IndexTTS2Run=_node({"text": STR, "seed": ["INT", {"default": 0}]}, ["AUDIO"]))
    spec = {"class": "IndexTTS2Run", "text": "text", "voice": None, "lang": None, "style": None}
    g = dyva._tts_workflow(spec, host, "Say this", None, host)
    assert g["1"]["inputs"]["text"] == "Say this"
    assert g["2"]["inputs"]["audio"] == ["1", 0]


def test_audio_output_index_is_looked_up_not_assumed_zero():
    # a node that declares STRING first, AUDIO second -> saver must read slot 1
    host = dict(SAVE, FooTTS=_node({"text": STR}, ["STRING", "AUDIO"]))
    spec = {"class": "FooTTS", "text": "text", "voice": None, "lang": None, "style": None}
    g = dyva._tts_workflow(spec, host, "hi", None, host)
    assert g["2"]["inputs"]["audio"] == ["1", 1]


def test_ascii_text_uses_en():
    host = dict(SAVE, QTTS=_node({"text": STR, "language": [["en", "zh"], {}]}, ["AUDIO"]))
    spec = {"class": "QTTS", "text": "text", "voice": None, "lang": "language", "style": None}
    g = dyva._tts_workflow(spec, host, "hello", None, host)
    assert g["1"]["inputs"]["language"] == "en"


def test_no_audio_saver_raises_before_submit():
    host = {"FooTTS": _node({"text": STR}, ["AUDIO"])}   # no SaveAudio
    spec = {"class": "FooTTS", "text": "text", "voice": None, "lang": None, "style": None}
    try:
        dyva._tts_workflow(spec, host, "hi", None, host)
        assert False, "expected _TtsError for a host with no audio saver"
    except dyva._TtsError:
        pass


# ---- _tts_pick_voice ------------------------------------------------------

def test_pick_voice_skips_zero_shot_and_custom_placeholders():
    assert dyva._tts_pick_voice(["zero-shot", "custom", "Ethan", "Bella"]) == "Ethan"
    assert dyva._tts_pick_voice(["none", "zero-shot"]) is None


# ---- _tts_viable: reject before the render --------------------------------

def test_string_output_node_is_not_viable():
    # GoogleTranslateTextNode matched the GoogleTTS pattern but emits STRING; the
    # saver would get text -> return_type_mismatch. Caught here instead.
    host = dict(SAVE, GoogleTranslateTextNode=_node({"text": STR}, ["STRING"]))
    assert dyva._tts_viable("GoogleTranslateTextNode", host, _fam("GoogleTTS")) is False


def test_empty_voice_enum_socket_is_not_viable():
    # ElevenLabs' voice is an ELEVENLABS_VOICE socket with no inline options.
    host = dict(SAVE, ElevenLabsTextToSpeech=_node({"text": STR, "voice": [[], {}]}, ["AUDIO"]))
    assert dyva._tts_viable("ElevenLabsTextToSpeech", host, _fam("ElevenLabs")) is False


def test_voice_design_empty_instruct_is_viable_because_style_is_supplied():
    # instruct is a required empty STRING, but it is THIS family's style field — we
    # fill it (DEFAULT_VOICE_STYLE), so it does not block viability.
    host = dict(SAVE, FB_Qwen3TTSVoiceDesign=_node(
        {"text": STR, "instruct": STR, "language": [["en"], {}]}, ["AUDIO"]))
    assert dyva._tts_viable("FB_Qwen3TTSVoiceDesign", host, _fam("Qwen3TTS-FB-Design")) is True


def test_a_non_style_empty_required_string_blocks_viability():
    # a required empty STRING we don't own is unfillable -> reject (the general rule
    # _empty_required_string enforces).
    host = dict(SAVE, SomeTTS=_node({"text": STR, "mandatory_desc": STR}, ["AUDIO"]))
    fam = {"name": "x", "text": "text", "voice": None, "lang": None, "style": None,
           "regs": [re.compile("SomeTTS")]}
    assert dyva._tts_viable("SomeTTS", host, fam) is False


def test_engine_without_unified_text_node_is_not_viable():
    host = dict(SAVE, Qwen3TTSEngineNode=_node(
        {"instruct": STR, "voice_preset": [["Ethan"], {}], "language": [["en"], {}]}, ["TTS_ENGINE"]))
    assert dyva._tts_viable("Qwen3TTSEngineNode", host, _fam("Qwen3TTSEngineNode")) is False
    host["UnifiedTTSTextNode"] = _node({"TTS_engine": ["TTS_ENGINE", {}], "text": STR}, ["AUDIO", "STRING"])
    assert dyva._tts_viable("Qwen3TTSEngineNode", host, _fam("Qwen3TTSEngineNode")) is True


def test_no_save_node_makes_an_otherwise_fine_node_not_viable():
    host = {"IndexTTS2Run": _node({"text": STR}, ["AUDIO"])}   # no saver anywhere
    assert dyva._tts_viable("IndexTTS2Run", host, _fam("IndexTTS")) is False


# ---- tts_excluded (name says it's a different job) ------------------------

def test_excluded_names_are_not_tts():
    for n in ("VoiceCloneNode", "SaveAudioMP3", "WhisperTranscribe", "LoadAudio", "AudioSaver"):
        assert dyva.tts_excluded(n) is True
    assert dyva.tts_excluded("Qwen3TTSEngineNode") is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
