"""Fish Speech (fishaudio) capability survey — `uv run pytest graflex/tests/test_fish_check.py`.

The "OpenAPI power by Kuí" Swagger page serves its OpenAPI spec at /json. The SAME badge
fronts builds with DIFFERENT route sets, so the check records each host's real capability
set — the distinct `/v1/<seg>` endpoints. These pin the pure parser:

  _fish_capabilities(spec) -> {"version": str|None, "capabilities": [sorted seg names]} | None
     None when the spec isn't a Fish Speech API (wrong/missing info.title).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import graflex   # noqa: E402


# Real shapes observed in the wild (2026-11-18):
SPEC_TTS_ASR_CHAT = {   # hosts 117.50.46.254:8080, 36.133.239.252:8083
    "info": {"title": "Fish Speech API", "version": "1.5.0"},
    "paths": {
        "/v1/asr": {"post": {}}, "/v1/chat": {"post": {}}, "/v1/tts": {"post": {}},
        "/v1/vqgan/decode": {"post": {}}, "/v1/vqgan/encode": {"post": {}},
    },
}
SPEC_TTS_REFERENCES = {   # host 141.147.132.243:8090
    "info": {"title": "Fish Speech API", "version": "1.5.0"},
    "paths": {
        "/v1/tts": {"post": {}},
        "/v1/references/add": {"post": {}}, "/v1/references/delete": {"delete": {}},
        "/v1/references/list": {"get": {}}, "/v1/references/update": {"post": {}},
        "/v1/vqgan/decode": {"post": {}}, "/v1/vqgan/encode": {"post": {}},
    },
}


def test_tts_asr_chat_build():
    out = graflex._fish_capabilities(SPEC_TTS_ASR_CHAT)
    assert out["version"] == "1.5.0"
    assert out["capabilities"] == ["asr", "chat", "tts", "vqgan"]


def test_tts_references_build():
    out = graflex._fish_capabilities(SPEC_TTS_REFERENCES)
    # Same badge, different machine -> a different capability set. That's the whole point.
    assert out["capabilities"] == ["references", "tts", "vqgan"]


def test_capabilities_are_deduped_and_sorted():
    out = graflex._fish_capabilities(SPEC_TTS_REFERENCES)
    assert out["capabilities"] == sorted(set(out["capabilities"]))


def test_non_fish_spec_is_none():
    assert graflex._fish_capabilities(
        {"info": {"title": "VoiceStudio API", "version": "0.5.7"}, "paths": {"/v1/tts": {}}}
    ) is None


def test_missing_title_is_none():
    assert graflex._fish_capabilities({"paths": {"/v1/tts": {}}}) is None
    assert graflex._fish_capabilities(None) is None


def test_non_v1_paths_ignored():
    spec = {"info": {"title": "Fish Speech API", "version": "1.5.0"},
            "paths": {"/json": {}, "/heartbeat": {}, "/v1/tts": {"post": {}}}}
    assert graflex._fish_capabilities(spec)["capabilities"] == ["tts"]


def test_version_may_be_absent():
    spec = {"info": {"title": "Fish Speech API"}, "paths": {"/v1/tts": {"post": {}}}}
    out = graflex._fish_capabilities(spec)
    assert out["version"] is None and out["capabilities"] == ["tts"]
