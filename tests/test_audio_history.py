"""Server-side speech history so API-generated TTS shows in the gallery
(`uv run pytest tests/test_audio_history.py -q`).

The Speech gallery used to read ONLY browser localStorage, which the dashboard's own
generate flow wrote — so a clip made over the raw /v1/audio/speech API (curl, an SDK) was
saved to disk but never appeared in the gallery. save_audio_history is the audio twin of the
image gallery's _save_image_history: every speech request appends an entry here, newest
first, capped to AUDIO_KEEP, carrying the text/voice/host so the gallery can label the clip.

Writes to a tmp dir via monkeypatched module paths — no real cache, no network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _hist(tmp_path, monkeypatch):
    d = tmp_path / "audio"
    d.mkdir()
    monkeypatch.setattr(dyva, "AUDIO_DIR", str(d))
    monkeypatch.setattr(dyva, "AUDIO_HISTORY_FILE", str(d / "history.json"))
    return d


def _read(d):
    with open(os.path.join(str(d), "history.json")) as f:
        return json.load(f)


def test_saves_entry_with_text_voice_and_node(tmp_path, monkeypatch):
    d = _hist(tmp_path, monkeypatch)
    dyva.save_audio_history("c1.flac", "hello world", voice="Ethan",
                            host="http://h:1", node="Qwen3TTSEngineNode")
    h = _read(d)
    assert len(h) == 1
    e = h[0]
    assert e["file"] == "c1.flac"
    assert e["text"] == "hello world"
    assert e["voice"] == "Ethan"
    assert e["node"] == "Qwen3TTSEngineNode"
    assert "when" in e


def test_newest_first(tmp_path, monkeypatch):
    d = _hist(tmp_path, monkeypatch)
    dyva.save_audio_history("old.flac", "first")
    dyva.save_audio_history("new.flac", "second")
    assert [e["file"] for e in _read(d)] == ["new.flac", "old.flac"]


def test_default_voice_when_none(tmp_path, monkeypatch):
    d = _hist(tmp_path, monkeypatch)
    dyva.save_audio_history("c.flac", "hi")
    assert _read(d)[0]["voice"] == "default"


def test_capped_at_audio_keep(tmp_path, monkeypatch):
    d = _hist(tmp_path, monkeypatch)
    monkeypatch.setattr(dyva, "AUDIO_KEEP", 3)
    for i in range(5):
        dyva.save_audio_history(f"c{i}.flac", f"t{i}")
    h = _read(d)
    assert [e["file"] for e in h] == ["c4.flac", "c3.flac", "c2.flac"]   # newest 3 only


def test_text_truncated_to_200(tmp_path, monkeypatch):
    d = _hist(tmp_path, monkeypatch)
    dyva.save_audio_history("c.flac", "x" * 500)
    assert len(_read(d)[0]["text"]) == 200


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
