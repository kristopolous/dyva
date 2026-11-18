"""Runaway-context clamp — pytest (`uv run pytest tests/test_strip_tool_output.py`).

When a request has searched hard and still can't place a host, dyva strips the OUTPUT
of older tool-call results from the payload and keeps racing the lighter prompt. These
pin the two pure pieces:

  _strip_tool_output(messages, keep_last) -> (n_stripped, chars_saved)
     replaces the CONTENT of older tool-result messages with a stub, IN PLACE, keeping
     each message dict, its role and its tool_call_id (so no tool_call/tool_result
     pairing desyncs and no upstream 400), and leaving the last `keep_last` whole.

  _strip_should_trip(tried, elapsed) -> bool
     True once a still-unplaced request has crossed either configured threshold.

See brainstorm/strip-tool-output-on-search-failure.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _convo():
    # A realistic agentic prompt: assistant tool_calls paired with role:"tool" results.
    return [
        {"role": "system", "content": "you are a coding agent"},
        {"role": "user", "content": "refactor the parser"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_a", "type": "function",
                         "function": {"name": "read", "arguments": {"path": "a.py"}}}]},
        {"role": "tool", "tool_call_id": "call_a", "name": "read",
         "content": "AAAA the entire contents of a.py, very long " * 20},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_b", "type": "function",
                         "function": {"name": "grep", "arguments": {"q": "parse"}}}]},
        {"role": "tool", "tool_call_id": "call_b", "name": "grep",
         "content": "BBBB forty grep hits " * 20},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_c", "type": "function",
                         "function": {"name": "read", "arguments": {"path": "b.py"}}}]},
        {"role": "tool", "tool_call_id": "call_c", "name": "read",
         "content": "CCCC the entire contents of b.py " * 20},
    ]


def test_strips_older_tool_bodies_keeps_last_whole():
    msgs = _convo()
    n, saved = dyva._strip_tool_output(msgs, keep_last=1)
    # Three tool results; keep_last=1 -> the first two are stubbed, the last is whole.
    assert n == 2
    assert saved > 0
    assert msgs[3]["content"] == dyva.STRIP_TOOL_STUB        # call_a stubbed
    assert msgs[5]["content"] == dyva.STRIP_TOOL_STUB        # call_b stubbed
    assert msgs[7]["content"].startswith("CCCC")             # call_c (most recent) kept


def test_structure_and_ids_survive():
    msgs = _convo()
    dyva._strip_tool_output(msgs, keep_last=1)
    # Every message dict stays, roles stay, tool_call_ids stay -> no pairing desync.
    assert [m["role"] for m in msgs] == \
        ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool"]
    assert msgs[3]["tool_call_id"] == "call_a"
    assert msgs[5]["tool_call_id"] == "call_b"
    # Assistant tool_calls are never touched (the call survives; only its OUTPUT shrinks).
    assert msgs[2]["tool_calls"][0]["id"] == "call_a"
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "read"


def test_non_tool_messages_untouched():
    msgs = _convo()
    sys_before, user_before = msgs[0]["content"], msgs[1]["content"]
    dyva._strip_tool_output(msgs, keep_last=0)
    assert msgs[0]["content"] == sys_before
    assert msgs[1]["content"] == user_before


def test_keep_last_zero_strips_all():
    msgs = _convo()
    n, _ = dyva._strip_tool_output(msgs, keep_last=0)
    assert n == 3
    assert all(msgs[i]["content"] == dyva.STRIP_TOOL_STUB for i in (3, 5, 7))


def test_keep_last_ge_count_is_noop():
    msgs = _convo()
    n, saved = dyva._strip_tool_output(msgs, keep_last=99)
    assert (n, saved) == (0, 0)
    assert msgs[3]["content"].startswith("AAAA")


def test_idempotent():
    msgs = _convo()
    dyva._strip_tool_output(msgs, keep_last=1)
    n2, saved2 = dyva._strip_tool_output(msgs, keep_last=1)
    assert (n2, saved2) == (0, 0)          # already-stub bodies are not re-counted


def test_no_tool_messages():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert dyva._strip_tool_output(msgs, keep_last=3) == (0, 0)


def test_chars_saved_is_positive_bytes_removed():
    msgs = _convo()
    big = msgs[3]["content"]
    n, saved = dyva._strip_tool_output(msgs, keep_last=2)   # strips only call_a
    assert n == 1
    assert saved == len(big) - len(dyva.STRIP_TOOL_STUB)


# ---- the trip predicate -------------------------------------------------------------

def _set_strip(monkeypatch, enabled=True, hosts=100, secs=1200):
    monkeypatch.setattr(dyva, "STRIP_ENABLED", enabled)
    monkeypatch.setattr(dyva, "STRIP_AFTER_HOSTS", hosts)
    monkeypatch.setattr(dyva, "STRIP_AFTER_SECS", secs)


def test_trip_on_host_count(monkeypatch):
    _set_strip(monkeypatch, hosts=100, secs=0)
    assert dyva._strip_should_trip(99, 10.0) is False
    assert dyva._strip_should_trip(100, 10.0) is True


def test_trip_on_elapsed(monkeypatch):
    _set_strip(monkeypatch, hosts=0, secs=1200)
    assert dyva._strip_should_trip(5, 1199.0) is False
    assert dyva._strip_should_trip(5, 1200.0) is True


def test_disabled_never_trips(monkeypatch):
    _set_strip(monkeypatch, enabled=False, hosts=1, secs=1)
    assert dyva._strip_should_trip(9999, 9999.0) is False


def test_zero_threshold_disables_that_trigger(monkeypatch):
    _set_strip(monkeypatch, hosts=0, secs=0)   # both off -> never trips on either
    assert dyva._strip_should_trip(9999, 9999.0) is False
