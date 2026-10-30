"""A pre-response connection drop is retried immediately, not penalised — pytest.

The bug (seen in the activity feed): a fast "Server disconnected" — the classic
stale keepalive-reuse race, where the pool handed the request a socket the server
had already closed — was classified as a hard fault (add_bad), demoting a host that
otherwise succeeds on the very next try. Nothing was delivered downstream at that
point, so the fix retries the same host ONCE, right away. A single transient drop
costs no reputation; only a SECOND consecutive drop is treated as a real fault.

These drive `_quick_probe`, which wraps `_send_chat` with the same one-immediate-retry
idiom the live-request path (`_race_servers`) uses.
"""
import asyncio
import os
import sys
import tempfile

import aiohttp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402

H, M = "http://203.0.113.7:8001", "glm5-flash"


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(dyva, "STATUS_DB", os.path.join(d, "host-status.db"))
    monkeypatch.setattr(dyva, "_status_db", None)
    monkeypatch.setattr(dyva, "LAST_FILE", os.path.join(d, "last.json"))
    monkeypatch.setattr(dyva, "_last_cache", None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "broadcast_activity", _noop)


class _OKResp:
    """Minimal 200 response that _quick_probe grades as a PASS (answers 'black')."""
    status = 200

    async def json(self):
        return {"message": {"content": "black"}}

    async def read(self):
        return b'{"message":{"content":"black"}}'

    async def release(self):
        return None


def _fake_send_chat(script):
    """Return an async _send_chat stand-in that pops `script` per call. A class in
    the script is raised; an _OKResp instance is returned as a successful send."""
    calls = {"n": 0}

    async def _send(session, host, full, payload, endpoint, do_stream, timeout=None):
        calls["n"] += 1
        item = script.pop(0)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("connection dropped")
        return item, False, endpoint, None    # (resp, oai, ep, why)

    return _send, calls


@pytest.mark.parametrize("drop", [aiohttp.ServerDisconnectedError,
                                  aiohttp.ClientConnectionResetError,
                                  ConnectionResetError])
def test_one_drop_then_success_is_retried_without_penalty(monkeypatch, drop):
    _fresh(monkeypatch)
    send, calls = _fake_send_chat([drop, _OKResp()])
    monkeypatch.setattr(dyva, "_send_chat", send)

    ok, _msg = asyncio.run(dyva._quick_probe(None, H, M, M, grade=True))

    assert calls["n"] == 2                       # dropped once, reconnected and retried
    assert ok is True                            # the retry delivered a clean answer
    assert dyva._state_of(H, M) == "good"        # transient drop cost NO reputation


def test_two_consecutive_drops_is_a_real_fault(monkeypatch):
    _fresh(monkeypatch)
    send, calls = _fake_send_chat([aiohttp.ServerDisconnectedError,
                                   aiohttp.ServerDisconnectedError])
    monkeypatch.setattr(dyva, "_send_chat", send)

    ok, _msg = asyncio.run(dyva._quick_probe(None, H, M, M, grade=True))

    assert calls["n"] == 2                       # exactly one retry, not an unbounded loop
    assert ok is False
    assert dyva._state_of(H, M) == "bad"         # a persistent dropper is still condemned
