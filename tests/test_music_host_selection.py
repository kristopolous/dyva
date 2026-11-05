"""Music host selection: race only hosts that actually carry a music MODEL, not
every host with any audio-class model — pytest
(`uv run pytest tests/test_music_host_selection.py -q`).

The bug this pins: `_find_music_hosts` selected by `_AUDIO_CLASSES`
({audio, music, t2a}) — so a host with only a TTS model, or one with the AceStep
*nodes* but no *checkpoint*, counted as a music candidate. Measured against the
real pool that was ~120 "candidates" of which only ~20 had a loadable music
model; the other ~100 produced a guaranteed "no matching model to load" at
submit, drowning the race (94x in a live failure) and tanking the hit rate.

Selection must be by the `music` class (the text-to-music checkpoints:
ace-step, minimax-music, yue, songbloom, musicgen), so a TTS-only or image host
is never raced for music.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _seed(monkeypatch, servers):
    monkeypatch.setattr(dyva, "load_servers", lambda: servers)
    monkeypatch.setattr(dyva, "load_bad", lambda: set())
    monkeypatch.setattr(dyva, "load_good", lambda: set())
    monkeypatch.setattr(dyva, "get_last", lambda k: None)
    monkeypatch.setattr(dyva, "_idle_first", lambda hosts: list(hosts))


def test_only_music_model_hosts_are_candidates(monkeypatch):
    servers = [
        {"service": "comfyui", "server": "A",
         "models": ["checkpoints/ace_step_v1_3.5b.safetensors"]},            # music  -> in
        {"service": "comfyui", "server": "B",
         "models": ["qwen-tts/model.safetensors"]},                          # TTS    -> out
        {"service": "comfyui", "server": "C",
         "models": ["flux1-dev.safetensors"]},                               # image  -> out
        {"service": "comfyui", "server": "D",
         "models": ["diffusion_models/minimax_music3_dit.safetensors"]},     # music  -> in
        {"service": "ollama", "server": "E",
         "models": ["ace_step.safetensors"]},                                # not comfyui -> out
    ]
    _seed(monkeypatch, servers)
    assert set(dyva._find_music_hosts()) == {"A", "D"}


def test_node_only_host_without_a_checkpoint_is_not_a_candidate(monkeypatch):
    # the dominant real case: a host whose only audio-ish asset is a TTS model —
    # it may have AceStep *nodes* installed, but with no music checkpoint in its
    # model list it can never load one, so it must not be raced.
    servers = [
        {"service": "comfyui", "server": "nodeonly",
         "models": ["custom_nodes/ComfyUI-IndexTTS2/checkpoints/feat1.pt",
                    "mmaudio/mmaudio_synchformer_fp16.safetensors"]},
    ]
    _seed(monkeypatch, servers)
    assert dyva._find_music_hosts() == []


def test_target_host_must_have_a_music_model(monkeypatch):
    servers = [
        {"service": "comfyui", "server": "A",
         "models": ["ace_step_v1.safetensors"]},
        {"service": "comfyui", "server": "B",
         "models": ["qwen-tts/model.safetensors"]},
    ]
    _seed(monkeypatch, servers)
    assert dyva._find_music_hosts("A") == ["A"]
    assert dyva._find_music_hosts("B") == []
