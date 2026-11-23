"""Stop / Skip / wall-clock cap on a stream:false (buffered) answer — pytest
(`uv run pytest tests/test_drain_stream_interrupt.py -q`).

A client that asks for stream:false is served by `_drain_stream`, which consumes the
WHOLE upstream generation into one response. Unlike the streaming path (`_pump_stream`)
and the /api/generate path (`_forward_stream`, see test_generate_skip_stop.py), the
buffered path had NEVER honoured the operator controls OR the WORKER_MAX wall-clock
cap. `_drain_stream` took no cancel/skip and had only a per-read silence watchdog
(TIMEOUT), so a host that TRICKLES one line every <TIMEOUT seconds (keep-alive / slow
reasoning / empty deltas) reset the watchdog forever: the buffered drain ran unbounded,
holding an upstream connection, and Stop/Skip clicks hit a dead signal (the worker's
handles still pointed at the finished race). This pins the fix:
  * a set Stop  -> outcome 'stopped' (bail; caller ends with the partial, no failover);
  * a set Skip  -> outcome 'skip'    (bail; caller abandons the host and fails over);
  * past the WORKER_MAX deadline     -> outcome 'capped';
  * nothing set, host signals done   -> outcome 'done', full content assembled (as before);
  * silent host past TIMEOUT         -> outcome 'stalled', not a hang.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


class _Upstream:
    """Won upstream: one readline per queued line, then EOF. A 0-second sleep each
    read yields the loop so a set cancel/skip event can win the race."""
    def __init__(self, lines):
        self._q = list(lines)
        self.content = self
    async def readline(self):
        await asyncio.sleep(0)
        return self._q.pop(0) if self._q else b""
    async def release(self):
        pass


def _line(obj):
    return (json.dumps(obj) + "\n").encode()


def _event(set_it):
    e = asyncio.Event()
    if set_it:
        e.set()
    return e


# No terminal done among these — left alone the drain would run the queue dry; an
# interrupt or the cap must bail before that.
_MORE = [_line({"message": {"content": c}, "done": False}) for c in (" a", " b", " c", " d", " e")]


def test_skip_bails_and_reports_skip():
    up = _Upstream(list(_MORE))
    data, outcome = asyncio.run(dyva._drain_stream(up, None, False, "m", skip=_event(True)))
    assert outcome == "skip"
    assert len(up._q) > 0            # bailed early — did NOT drain the upstream


def test_stop_bails_and_reports_stopped():
    up = _Upstream(list(_MORE))
    data, outcome = asyncio.run(dyva._drain_stream(up, None, False, "m", cancel=_event(True)))
    assert outcome == "stopped"
    assert len(up._q) > 0


def test_worker_max_deadline_caps(monkeypatch):
    monkeypatch.setattr(dyva, "WORKER_MAX", 1)      # 1-second cap
    wid = "wd_cap"
    dyva._workers[wid] = {"wid": wid, "started": dyva.time.time() - 100,   # already past the cap
                          "stop": lambda: None, "skip": lambda: None, "done": None}
    try:
        up = _Upstream(list(_MORE))
        data, outcome = asyncio.run(dyva._drain_stream(up, None, False, "m", wid=wid))
        assert outcome == "capped"
        assert len(up._q) > 0        # capped before draining
    finally:
        dyva._workers.pop(wid, None)


def test_no_signal_drains_to_done():
    # Backward compat: neither button, no cap -> consume the whole upstream and end on
    # the real terminal done, content fully assembled.
    lines = list(_MORE) + [_line({"message": {"content": ""}, "done": True, "done_reason": "stop"})]
    up = _Upstream(lines)
    data, outcome = asyncio.run(dyva._drain_stream(up, None, False, "m"))
    assert outcome == "done"
    assert len(up._q) == 0
    assert data["message"]["content"] == " a b c d e"
    assert data.get("done") is True


def test_silent_host_reports_stalled(monkeypatch):
    # A fully silent upstream (readline never returns within TIMEOUT) -> 'stalled', not a hang.
    monkeypatch.setattr(dyva, "TIMEOUT", 0.05)

    class _Silent:
        def __init__(self):
            self.content = self
        async def readline(self):
            await asyncio.sleep(10)
            return b""
        async def release(self):
            pass

    data, outcome = asyncio.run(dyva._drain_stream(_Silent(), None, False, "m"))
    assert outcome == "stalled"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
