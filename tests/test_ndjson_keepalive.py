"""ndjson /api/chat keepalive: while the race is still finding a host, dyva must emit empty
assistant chunks to the client so an ollama-dialect idle timeout doesn't bail before the first
token — the twin of the SSE keepalive. No network: _race_servers and the StreamResponse are
mocked. Run: `uv run pytest tests/test_ndjson_keepalive.py`.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


class _FakeResp:
    def __init__(self):
        self.headers = {}
        self.writes = []
        self.prepared = False
    async def prepare(self, request):
        self.prepared = True
    async def write(self, b):
        self.writes.append(b)


class _Req:
    remote = "127.0.0.1"


def _keepalive_chunks(resp):
    out = []
    for b in resp.writes:
        try:
            out.append(json.loads(b.decode().strip()))
        except Exception:
            pass
    return [c for c in out if c.get("message", {}).get("content") == "" and c.get("done") is False]


def test_emits_keepalives_during_slow_search_then_returns_result(monkeypatch):
    monkeypatch.setattr(dyva, "KEEPALIVE_INTERVAL", 0.05)
    monkeypatch.setattr(dyva.web, "StreamResponse", _FakeResp)

    async def slow_race(*a, **k):
        await asyncio.sleep(0.18)                 # ~3 keepalive intervals
        return ("WON",), [], False                # (result, errors, stopped)
    monkeypatch.setattr(dyva, "_race_servers", slow_race)

    resp, result, errors, stopped = asyncio.run(dyva._race_ndjson_keepalive(
        _Req(), None, "m:1b", ["h1"], {"messages": []}, ["completion"], "wid"))

    assert resp.prepared and resp.headers["Content-Type"] == "application/x-ndjson"
    assert len(_keepalive_chunks(resp)) >= 1       # client kept warm during the wait
    assert result == ("WON",) and errors == [] and stopped is False


def test_no_host_returns_prepared_response_and_no_result(monkeypatch):
    monkeypatch.setattr(dyva, "KEEPALIVE_INTERVAL", 0.05)
    monkeypatch.setattr(dyva.web, "StreamResponse", _FakeResp)

    async def dead_race(*a, **k):
        return None, ["all servers failed"], False
    monkeypatch.setattr(dyva, "_race_servers", dead_race)

    resp, result, errors, stopped = asyncio.run(dyva._race_ndjson_keepalive(
        _Req(), None, "m:1b", ["h1"], {"messages": []}, ["completion"], "wid"))
    assert resp.prepared and result is None and errors == ["all servers failed"]
