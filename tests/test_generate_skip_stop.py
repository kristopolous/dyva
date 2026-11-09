"""Stop/Skip on an /api/generate stream — pytest (`uv run pytest tests/test_generate_skip_stop.py -q`).

/api/generate streams through `_forward_stream`, which (unlike the chat path's
`_stream_chat_failover`) had NEVER wired the operator controls past host-selection: it
passed no cancel/skip into `_pump_stream` and never re-armed the worker's Stop/Skip
handles. So once the stream committed to a host, BOTH buttons fired a dead signal —
clicks did nothing.

`_pump_stream` already honours the signals (see test_skip_signal.py); this pins the
wiring `_forward_stream` was missing:
  * it re-arms `_workers[wid]["stop"]`/`["skip"]` onto THIS leg's events, so a click
    during the committed stream hits a live switch;
  * a set Stop or Skip abandons the host EARLY (we don't drain the upstream);
  * either way the client's stream is closed with a terminal done — the connection is
    never dropped. Skip's content is intentionally undefined (zero guarantees, esp. in
    a tool call); the only promise is the open connection.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


class _ClientResp:
    """The StreamResponse back to the client."""
    def __init__(self):
        self.headers = {}
        self.chunks = []
    async def prepare(self, request):
        pass
    async def write(self, b):
        self.chunks.append(b)


class _Upstream:
    """The won upstream: hands out `lines` one readline at a time, then EOF."""
    def __init__(self, lines):
        self._q = list(lines)
        self.content = self
    async def readline(self):
        return self._q.pop(0) if self._q else b""
    async def release(self):
        pass
    def close(self):
        pass


def _line(obj):
    return (json.dumps(obj) + "\n").encode()


def _event(set_it):
    e = asyncio.Event()
    if set_it:
        e.set()
    return e


# A stream with NO terminal done among these lines, so left alone it would run the queue
# dry; a live Stop/Skip must bail before that and still close the client cleanly.
_FIRST = {"message": {"content": "partial"}, "done": False}
_MORE = [{"message": {"content": " more"}, "done": False},
         {"message": {"content": " yet more"}, "done": False},
         {"message": {"content": " and more"}, "done": False}]


def _run(monkeypatch, wid="w1", cancel=None, skip=None, more=None):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "account_tokens", _noop)
    monkeypatch.setattr(dyva, "broadcast_activity", _noop)
    monkeypatch.setattr(dyva, "_broadcast_workers", _noop)
    dyva._workers[wid] = {"wid": wid, "started": dyva.time.time(),
                          "stop": lambda: None, "skip": lambda: None, "done": None}
    out = _ClientResp()
    up = _Upstream([_line(o) for o in (more if more is not None else _MORE)])
    asyncio.run(dyva._forward_stream(
        object(), out, up, _line(_FIRST), "http://h:1", "m",
        False, upstream_openai=False, wid=wid, num_ctx=None,
        cancel=cancel, skip=skip))
    return out, up, dyva._workers[wid]


def _has_done(out):
    for b in out.chunks:
        try:
            if json.loads(b).get("done"):
                return True
        except Exception:
            pass
    return False


def test_forward_stream_arms_worker_stop_and_skip(monkeypatch):
    # The core bug: the generate path never pointed the worker's buttons at the live
    # stream events. After wiring, a click routes to THIS leg's cancel/skip.
    cancel, skip = _event(False), _event(False)
    _, _, w = _run(monkeypatch, cancel=cancel, skip=skip, more=[
        {"message": {"content": ""}, "done": True, "done_reason": "stop"}])
    assert w["stop"] == cancel.set
    assert w["skip"] == skip.set


def test_forward_stream_skip_abandons_host_without_dropping_client(monkeypatch):
    out, up, _ = _run(monkeypatch, skip=_event(True))
    assert len(up._q) > 0          # bailed early — did NOT drain the upstream
    assert _has_done(out)          # client stream closed cleanly, connection kept


def test_forward_stream_stop_abandons_host_without_dropping_client(monkeypatch):
    out, up, _ = _run(monkeypatch, cancel=_event(True))
    assert len(up._q) > 0
    assert _has_done(out)


def test_forward_stream_no_signal_runs_to_completion(monkeypatch):
    # Backward compat: with neither button pressed the stream consumes the whole
    # upstream and ends on the real terminal done.
    out, up, _ = _run(monkeypatch, cancel=_event(False), skip=_event(False), more=[
        {"message": {"content": " more"}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop"}])
    assert len(up._q) == 0         # drained to the real done
    assert _has_done(out)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
