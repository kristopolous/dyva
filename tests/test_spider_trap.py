"""Spider-trap screen: a host that CALLS a tool in response to the (tool-free) colour probe
is a trap and is condemned host-wide; one that ignores the bait is marked tool-safe.

Mocks the send primitive and the DB writes — no network, no reputation DB touched.
Run: `uv run pytest tests/test_spider_trap.py`.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


class _Resp:
    def __init__(self, data):
        self.status = 200
        self._data = data
    async def json(self):
        return self._data
    async def read(self):
        return b""
    async def release(self):
        return None


def _wire(monkeypatch, reply):
    """Make _send_chat return a 200 whose JSON is `reply`; capture the DB marks."""
    async def fake_send(session, host, full, payload, endpoint, do_stream, timeout=None):
        return _Resp(reply), False, "/api/chat", None
    async def noop_activity(*a, **k):
        return None
    marks = {}
    monkeypatch.setattr(dyva, "_send_chat", fake_send)
    monkeypatch.setattr(dyva, "broadcast_activity", noop_activity)
    for fn in ("force_bad", "mark_honeypot", "mark_smoke", "mark_smoke_tools",
               "add_good", "add_bad", "mark_maybe"):
        monkeypatch.setattr(dyva, fn, (lambda name: (lambda *a, **k: marks.__setitem__(name, True)))(fn))
    return marks


def _run(**kw):
    return asyncio.run(dyva._quick_probe(None, "h:11434", "m:1b", "m",
                                         tools=dyva.SPIDER_TRAP_BAIT, grade=False, trap_check=True, **kw))


def test_tool_call_on_bait_is_a_trap_and_condemns_the_host(monkeypatch):
    marks = _wire(monkeypatch, {"message": {"content": "",
                 "tool_calls": [{"function": {"name": "get_passwords", "arguments": {}}}]}})
    verdict, detail = _run()
    assert verdict is False
    assert "spider-trap" in detail and "get_passwords" in detail
    assert marks.get("mark_honeypot") is True       # condemned host-wide
    assert marks.get("mark_smoke") is True          # recorded as a smoke failure
    assert "mark_smoke_tools" not in marks          # NOT marked tool-safe


def test_clean_answer_marks_tool_safe(monkeypatch):
    marks = _wire(monkeypatch, {"message": {"content": "black"}})
    verdict, detail = _run()
    assert verdict is True
    assert detail == "tool-safe"
    assert marks.get("mark_smoke_tools") is True    # cleared for tools
    assert "mark_honeypot" not in marks             # not condemned


def test_dumb_wrong_answer_without_a_tool_call_is_still_tool_safe(monkeypatch):
    # A dumb-but-honest model flubs the colour but ignores the bait -> still safe with tools
    # (we don't re-grade the colour in the trap check).
    marks = _wire(monkeypatch, {"message": {"content": "that's an interesting question!"}})
    verdict, _ = _run()
    assert verdict is True
    assert marks.get("mark_smoke_tools") is True
    assert "mark_honeypot" not in marks
