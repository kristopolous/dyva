"""Phase 2 wiring: `_pump_stream` routes OUR prefixed tool call to us, not the client
(`uv run pytest tests/test_session_pump_wiring.py -q`).

This exercises the one sacred-path change: with a `_SessionToolTap` passed in (opted-in agentic
request only — `tap=None` for everyone else, so the text path is byte-identical), the pump
withholds our tool-call fragments from the client stream, accumulates them, and returns the new
status "session_tool" at the turn boundary so the caller can run them and loop. Nothing of ours
reaches the client; the client's own content still streams. See brainstorm/user-account.md.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402

P = dyva.SESSION_TOOL_PREFIX


class _FakeContent:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakeResp:
    def __init__(self, lines):
        self.content = _FakeContent(lines)

    async def release(self):
        pass


class _FakeResponse:
    def __init__(self):
        self.writes = []

    async def write(self, b):
        self.writes.append(b)


def _line(obj):
    return (json.dumps(obj) + "\n").encode()


@pytest.fixture(autouse=True)
def _no_account(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "account_tokens", _noop)
    yield


def _run_pump(first, lines, tap, openai_format=False):
    resp = _FakeResp(lines)
    response = _FakeResponse()
    status = asyncio.run(dyva._pump_stream(
        response, resp, first, "h:1", "m", openai_format,
        False, None, None, ["", 0], tap=tap))
    return status, b"".join(response.writes)


def test_native_stream_withholds_our_tool_and_returns_session_tool():
    tap = dyva._SessionToolTap("uh1")
    first = _line({"message": {"role": "assistant", "content": "let me check"}})
    name = _line({"message": {"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": f"{P}_search_sessions", "arguments": ""}}]}})
    args = _line({"message": {"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "function": {"arguments": '{"query":"cats"}'}}]}})
    done = _line({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"})
    status, blob = _run_pump(first, [name, args, done], tap)
    assert status == "session_tool"                 # caller will run our tool and loop
    assert b"let me check" in blob                   # the client still saw the greeting
    assert P.encode() not in blob                    # ...but NOT our prefixed tool call
    assert tap.has_capture()
    assert json.loads(tap.captured()[0]["function"]["arguments"]) == {"query": "cats"}


def test_client_tool_call_streams_through_and_is_not_intercepted():
    tap = dyva._SessionToolTap("uh1")
    first = _line({"message": {"role": "assistant", "content": ""}})
    name = _line({"message": {"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "get_weather", "arguments": ""}}]}})
    args = _line({"message": {"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "function": {"arguments": '{"city":"NYC"}'}}]}})
    done = _line({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"})
    status, blob = _run_pump(first, [name, args, done], tap)
    assert status == "done"                          # a normal tool-calling turn for the client
    assert b"get_weather" in blob                     # the client's tool reaches the client
    assert not tap.has_capture()


def test_single_shot_done_line_carrying_our_tool_is_intercepted():
    # a buffered host returns the whole tool call in one message with done:true (the first line)
    tap = dyva._SessionToolTap("uh1")
    first = _line({"message": {"role": "assistant", "content": "", "tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": f"{P}_list_sessions", "arguments": "{}"}}]},
        "done": True, "done_reason": "stop"})
    status, blob = _run_pump(first, [], tap)
    assert status == "session_tool"
    assert P.encode() not in blob
    assert tap.has_capture()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
