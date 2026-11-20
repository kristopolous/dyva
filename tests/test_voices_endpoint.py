"""GET /v1/audio/voices lists the curated catalog even when the pool is fish-only
(`uv run pytest tests/test_voices_endpoint.py -q`).

The bug: the endpoint surveyed ComfyUI node hosts only and 503'd ("no ComfyUI hosts")
when there were none — so the shipped fish-cloneable catalog voices never showed, even
with a hundred fish hosts up. handle_audio_voices now merges load_voice_catalog() into
the list, each catalog voice carrying the live fish-host count, and only 503s when there
is genuinely nothing to offer. Driven with make_mocked_request; no DB, no network.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402
from aiohttp.test_utils import make_mocked_request  # noqa: E402


def _req():
    r = make_mocked_request("GET", "/v1/audio/voices")
    r.app["session"] = None          # unused on the no-ComfyUI-host path
    return r


def _setup(monkeypatch, comfy_hosts, survey, fish_count, catalog, oai_servers=()):
    async def _ok(_r):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)
    monkeypatch.setattr(dyva, "_find_tts_hosts", lambda *a, **k: list(comfy_hosts))
    monkeypatch.setattr(dyva, "load_node_survey", lambda key: dict(survey))
    monkeypatch.setattr(dyva, "_fish_host_count", lambda: fish_count)
    monkeypatch.setattr(dyva, "load_voice_catalog", lambda: list(catalog))
    monkeypatch.setattr(dyva, "load_servers", lambda: list(oai_servers))


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


def test_fish_only_pool_lists_catalog_voices(monkeypatch):
    # no ComfyUI TTS hosts, but 50 fish hosts -> the catalog voices show, each with the
    # fish-host count as its confidence number; no 503.
    _setup(monkeypatch, comfy_hosts=[], survey={}, fish_count=50,
           catalog=[{"id": "en-US_f_jodi"}, {"id": "en-US_m_dan"}])
    resp = asyncio.run(dyva.handle_audio_voices(_req()))
    assert resp.status == 200
    out = _body(resp)
    assert out["voices"] == [
        {"voice": "en-US_f_jodi", "hosts": 50, "source": "catalog"},
        {"voice": "en-US_m_dan", "hosts": 50, "source": "catalog"}]


def test_catalog_merges_with_host_voices(monkeypatch):
    survey = {"h1": {"node": "Qwen3TTS", "voices": ["Dylan", "Dylan2"]}}
    _setup(monkeypatch, comfy_hosts=["h1"], survey=survey, fish_count=2,
           catalog=[{"id": "en-US_f_jodi"}])
    resp = asyncio.run(dyva.handle_audio_voices(_req()))
    out = _body(resp)
    voices = {v["voice"]: v for v in out["voices"]}
    assert voices["Dylan"]["hosts"] == 1
    assert voices["en-US_f_jodi"] == {"voice": "en-US_f_jodi", "hosts": 2, "source": "catalog"}


def test_empty_pool_still_503s(monkeypatch):
    # no ComfyUI hosts, no fish hosts, empty catalog -> nothing to offer -> 503
    _setup(monkeypatch, comfy_hosts=[], survey={}, fish_count=0, catalog=[])
    resp = asyncio.run(dyva.handle_audio_voices(_req()))
    assert resp.status == 503


def test_chatterbox_host_voices_show_in_the_picker(monkeypatch):
    # the bug: fast chatterbox voices (surveyed by graflex into `voices`) were never listed,
    # so only the slow comfy voices were pickable. They must appear now, as clean names.
    oai = [{"server": "cb1", "service": "chatterbox", "models": ["ChatterboxTTS"],
            "voices": ["Michael.wav", "Olivia.wav"]},
           {"server": "cb2", "service": "chatterbox", "models": ["ChatterboxTTS"],
            "voices": ["Michael.wav"]}]
    _setup(monkeypatch, comfy_hosts=[], survey={}, fish_count=0, catalog=[], oai_servers=oai)
    resp = asyncio.run(dyva.handle_audio_voices(_req()))
    assert resp.status == 200
    by = {v["voice"]: v["hosts"] for v in _body(resp)["voices"]}
    assert by == {"Michael": 2, "Olivia": 1}       # fast voices, clean names, host counts


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
