"""A cloned (catalog) voice must fail loudly when msgpack is missing, instead of
silently returning a random host-default voice (`uv run pytest tests/test_tts_clone_guard.py -q`).

Cloning ships the reference wav to fish as a msgpack body. Without the optional msgpack
package the reference is dropped and every fish host speaks its own default voice — the
user asks for "bm_george" and gets a different random voice each time. handle_tts_speech
refuses up front with an actionable message when a catalog voice is requested but msgpack
isn't installed.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


class _FakeReq:
    def __init__(self, body, query=None):
        self._body = body
        self.query = query or {}
        self.app = {"session": None}

    async def json(self):
        return self._body


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


def _common(monkeypatch):
    async def _ok(_r):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)
    monkeypatch.setattr(dyva, "load_voice_catalog", lambda: [{"id": "bm_george"}])
    monkeypatch.setattr(dyva, "_resolve_voice",
                        lambda v, c: {"id": "bm_george"} if v == "bm_george" else None)
    # no hosts, so if the guard DOESN'T fire we fall to the catalog "needs a fish host" 503
    monkeypatch.setattr(dyva, "_find_tts_hosts", lambda *a, **k: [])
    monkeypatch.setattr(dyva, "_find_openai_tts_hosts", lambda *a, **k: [])


def test_catalog_voice_without_msgpack_refuses_with_actionable_error(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_msgpack", None)
    resp = asyncio.run(dyva.handle_tts_speech(_FakeReq({"input": "hi", "voice": "bm_george"})))
    assert resp.status == 503
    assert "msgpack" in _body(resp)["error"]


def test_catalog_voice_with_msgpack_does_not_hit_the_guard(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_msgpack", object())        # present
    resp = asyncio.run(dyva.handle_tts_speech(_FakeReq({"input": "hi", "voice": "bm_george"})))
    # falls through to the normal "no fish host available" path, NOT the msgpack refusal
    assert resp.status == 503
    assert "msgpack" not in _body(resp)["error"]


def test_non_catalog_voice_never_hits_the_guard(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_msgpack", None)            # missing, but voice isn't a clone
    resp = asyncio.run(dyva.handle_tts_speech(_FakeReq({"input": "hi", "voice": "Abigail"})))
    assert "msgpack" not in _body(resp)["error"]
