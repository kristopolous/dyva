"""/api/generate non-stream must token-account — pytest
(`uv run pytest tests/test_generate_accounting.py -q`).

Regression: _proxy_chat's non-stream branch calls account_tokens (so the request lands
in the Stats view, and records tps / max_ok), but _proxy_generate's non-stream branch
returned the raced result WITHOUT accounting. Every `stream:false` /api/generate
request therefore vanished from Stats — one of the "stats stopped recording" cases.
This pins the call site _proxy_generate was missing, mirroring the chat path.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


class _FakeReq:
    def __init__(self, body):
        self._b = body
        self.remote = "127.0.0.1"

    async def json(self):
        return self._b


def test_proxy_generate_nonstream_accounts_tokens(monkeypatch):
    calls = []

    async def _account(wid, host, full, data, num_ctx):
        calls.append((wid, host, full, data, num_ctx))

    async def _register(*a, **k):
        return "w1"

    async def _unregister(*a, **k):
        return None

    async def _race(session, model, servers, payload, **k):
        # the buffered (client stream:false) path races with stream=False and gets the
        # whole response back as `data` — the 4-tuple (leader, host, full, data).
        assert payload.get("stream") is False
        return (("leader", "http://9.9.9.9:11434", "llama3:8b",
                 {"response": "hi", "done": True,
                  "prompt_eval_count": 5, "eval_count": 2}), [], False)

    monkeypatch.setattr(dyva, "caps_for", lambda model, *a, **k: (model, None))
    monkeypatch.setattr(dyva, "find_servers",
                        lambda *a, **k: [(0, "http://9.9.9.9:11434", ["llama3:8b"])])
    monkeypatch.setattr(dyva, "_client_title", lambda r: None)
    monkeypatch.setattr(dyva, "_register_worker", _register)
    monkeypatch.setattr(dyva, "_unregister_worker", _unregister)
    monkeypatch.setattr(dyva, "_stash_want_ctx", lambda *a, **k: None)
    monkeypatch.setattr(dyva, "mark_worker_found", lambda *a, **k: None)
    monkeypatch.setattr(dyva, "_race_servers", _race)
    monkeypatch.setattr(dyva, "account_tokens", _account)

    req = _FakeReq({"model": "llama3:8b", "prompt": "hello", "stream": False})
    resp = asyncio.run(dyva._proxy_generate(req, None))

    # the client still gets its generate-shaped answer ...
    assert json.loads(resp.body.decode())["response"] == "hi"
    # ... and the request was token-accounted exactly once (the regression).
    assert len(calls) == 1, "non-stream /api/generate must account tokens"
    _wid, host, full, data, _ctx = calls[0]
    assert host == "http://9.9.9.9:11434" and full == "llama3:8b"
    assert data["eval_count"] == 2
