"""The node classifier recognizes the popular HF text-to-speech engines
(`uv run pytest tests/test_tts_hf_coverage.py -q`).

Driven by huggingface.co/models?pipeline_tag=text-to-speech&sort=downloads. Engines whose
ComfyUI node class names CONTAIN "tts" (MeloTTS, VieNeu-TTS, E2-TTS, Kitten-TTS, MMS-TTS,
Parler-TTS, Kyutai-TTS, Kani-TTS, Breeze-TTS, sano-TTS …) are already caught by the generic
`(?i)tts` family. The gap this file pins is the popular engines whose REAL node class names
do NOT contain "tts" — the catch-all can't see them, so each needs a dedicated or broadened
family. Every node name below is a real ComfyUI class observed in a published pack:

  * CosyVoice  — CosyVoiceNode / CosyVoice2Node / CosyVoiceZeroShotNode (FunAudioLLM, AIFSH).
                 Our family was the narrow ^CosyVoiceEngineNode, so these were UNclassified.
  * VibeVoice  — VibeVoiceEngineNode (TTS Audio Suite). Family was ^VibeVoiceTTS -> missed it.
  * Higgs      — HiggsAudio / HiggsAudioEngineNode (boson higgs-audio; 1M+ downloads).
  * VoxCPM     — RunningHub_VoxCPM_Generate (openbmb VoxCPM; ~1M downloads).
  * Orpheus    — orpheus / orpheusAdvanced (canopylabs orpheus-3b).
  * OmniVoice  — OmniVoiceGenerate / OmniVoiceTTS (k2-fsa OmniVoice; 3.9M downloads).

Loaders/savers for the same engines must still be rejected by tts_excluded (they are not the
generator), so a host isn't promoted off a model-loader node.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _fam(node):
    f = dyva._tts_node_family(node)
    return f["name"] if f else None


# real ComfyUI node class -> the family it should resolve to
COVERED = {
    "CosyVoiceNode": "CosyVoice",
    "CosyVoice2Node": "CosyVoice",
    "CosyVoiceZeroShotNode": "CosyVoice",
    "VibeVoiceEngineNode": "VibeVoiceTTS",
    "HiggsAudio": "Higgs",
    "HiggsAudioEngineNode": "Higgs",
    "RunningHub_VoxCPM_Generate": "VoxCPM",
    "VoxCPM_TTS": "VoxCPM",
    "orpheus": "Orpheus",
    "orpheusAdvanced": "Orpheus",
    "OmniVoiceGenerate": "OmniVoice",
    "OmniVoiceTTS": "OmniVoice",
    "SpeechT5Node": "SpeechT5",
    "SpeechT5_Generate": "SpeechT5",
}


def test_no_tts_in_name_engines_are_classified():
    for node, want in COVERED.items():
        assert _fam(node) == want, f"{node} -> {_fam(node)!r}, expected {want!r}"


def test_tts_named_engines_still_classified_by_the_catch_all():
    # these contain "tts" and were always caught; guard against a regression that drops them
    for node in ("MeloTTSNode", "KittenTTS", "ParlerTTSNode", "KokoroTTS", "KaniTTS"):
        assert _fam(node) is not None, node


def test_loaders_and_savers_for_these_engines_are_excluded():
    # classification may match (e.g. (?i)higgs), but these are NOT the generator. The
    # name-exclusion contract covers the Load*/Save*/*Loader shapes; other awkward loaders
    # (e.g. OmniVoiceLoadModel) are caught later by _tts_viable instead (no `text` input).
    for node in ("LoadHiggsAudioModel", "SaveHiggsAudio", "OrpheusLoader"):
        assert dyva.tts_excluded(node) is True, node


def test_non_tts_nodes_still_match_no_family():
    for node in ("KSampler", "VAEDecode", "CLIPTextEncode", "LoadImage"):
        assert dyva._tts_node_family(node) is None, node


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
