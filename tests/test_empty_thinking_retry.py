"""Empty (thinking-only) answer -> one-shot think:false retry, then a strike — pytest.

Some models (a custom-GGUF reasoner under ollama) stream their whole answer into the
`thinking` channel and send `content` empty — the reply looks blank. think:false fixes it.
dyva detects the SYMPTOM (a done chunk with no content and no tool call), not the cause:

  - `_is_empty_answer` / `_pump_stream` classify such a completion as "empty";
  - the stream driver retries that turn once with think:false (verified end-to-end live);
  - and COUNTS a strike for the (host, model). A done-but-empty is also what a choked/OOM'd
    host sends, so one strike is NOT enough to change routing: only after NO_THINK_STRIKES of
    them does a later request start with think:false up front (`_session_empty_strikes`,
    `_should_suppress_think`). No persistence — a restart resets.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


def test_is_empty_answer_only_when_no_content_and_no_toolargs():
    assert dyva._is_empty_answer(["", 0]) is True
    assert dyva._is_empty_answer(["   ", 0]) is True          # whitespace-only is still empty
    assert dyva._is_empty_answer(["hello", 0]) is False       # real content
    assert dyva._is_empty_answer(["", 42]) is False           # a tool call (arg chars) counts


class _Out:
    def __init__(self):
        self.chunks = []

    async def write(self, b):
        self.chunks.append(b)


class _Resp:
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


def _pump(monkeypatch, first, rest, partial):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "account_tokens", _noop)
    monkeypatch.setattr(dyva, "broadcast_activity", _noop)
    out, resp = _Out(), _Resp([_line(o) for o in rest])
    status = asyncio.run(dyva._pump_stream(
        out, resp, _line(first), "http://h:1", "m",
        False, False, None, None, partial))
    return status, out


def test_pump_returns_empty_when_whole_answer_went_to_thinking(monkeypatch):
    first = {"message": {"role": "assistant", "content": "", "thinking": "planning"}, "done": False}
    done = {"message": {"content": "", "thinking": "more"}, "done": True, "done_reason": "stop"}
    status, _ = _pump(monkeypatch, first, [done], ["", 0])
    assert status == "empty"


def test_pump_returns_done_when_there_is_real_content(monkeypatch):
    first = {"message": {"content": ""}, "done": False}
    chunk = {"message": {"content": "hello"}, "done": False}
    done = {"message": {"content": ""}, "done": True, "done_reason": "stop"}
    status, _ = _pump(monkeypatch, first, [chunk, done], ["", 0])
    assert status == "done"


def test_pump_returns_done_for_a_tool_only_turn(monkeypatch):
    first = {"message": {"content": ""}, "done": False}
    done = {"message": {"content": "",
                        "tool_calls": [{"function": {"name": "web", "arguments": {"q": "x"}}}]},
            "done": True, "done_reason": "stop"}
    status, _ = _pump(monkeypatch, first, [done], ["", 0])
    assert status == "done"           # a tool call is a real answer, not the empty bug


class _FakeResp:
    status = 200

    async def release(self):
        pass

    def close(self):
        pass


class _FakeSession:
    def __init__(self):
        self.posted = []

    async def post(self, url, json=None, timeout=None, allow_redirects=False):
        self.posted.append(json)
        return _FakeResp()


def test_struck_out_pairing_sends_think_false_up_front(monkeypatch):
    # a (host, model) that has gone empty NO_THINK_STRIKES times is taken as the thinking
    # bug and gets think:false on the NEXT request without waiting to fail again.
    monkeypatch.setattr(dyva, "speaks_openai", lambda host: False)   # force the native dialect
    host, model = "http://h:1", "mymodel"
    key = (dyva._norm_host(host), model)
    monkeypatch.setattr(dyva, "_session_empty_strikes", {key: dyva.NO_THINK_STRIKES})

    sess = _FakeSession()
    asyncio.run(dyva._send_chat(sess, host, model,
                                {"messages": [{"role": "user", "content": "hi"}]},
                                "/api/chat", False))
    assert sess.posted and sess.posted[0].get("think") is False       # thinking turned off up front


def test_one_strike_pairing_still_leaves_think_unset(monkeypatch):
    # the behaviour change: a SINGLE empty (maybe just a choked host) must not flip routing —
    # the next request still tries thinking.
    monkeypatch.setattr(dyva, "speaks_openai", lambda host: False)
    host, model = "http://h:1", "mymodel"
    key = (dyva._norm_host(host), model)
    monkeypatch.setattr(dyva, "_session_empty_strikes", {key: 1})
    sess = _FakeSession()
    asyncio.run(dyva._send_chat(sess, host, model,
                                {"messages": [{"role": "user", "content": "hi"}]},
                                "/api/chat", False))
    assert "think" not in sess.posted[0]                              # one strike is not enough


def test_unstruck_pairing_leaves_think_unset(monkeypatch):
    monkeypatch.setattr(dyva, "speaks_openai", lambda host: False)
    monkeypatch.setattr(dyva, "_session_empty_strikes", {})           # nothing learned
    sess = _FakeSession()
    asyncio.run(dyva._send_chat(sess, "http://h:1", "mymodel",
                                {"messages": [{"role": "user", "content": "hi"}]},
                                "/api/chat", False))
    assert "think" not in sess.posted[0]                              # default behaviour: reasoning left on
