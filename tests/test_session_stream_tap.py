"""Phase 2 streaming: the magic-string stream tap and the turn-boundary loop step
(`uv run pytest tests/test_session_stream_tap.py -q`).

The opt-in session tools are intercepted WITHOUT buffering the stream. Chunks flow to the
client unmolested until a tool-call fragment names one of OUR prefixed tools — on modern
models the function name arrives whole in a single delta (see the qwen paste in the design
notes), which is the whole reason the random prefix works as a magic string. From that
index on, the tap withholds the fragments from the client and accumulates them; at the turn
boundary the caller runs them server-side (`_session_loop_step`), feeds the result back as a
tool message, and the model's real answer streams down to the user. Inert unless a fragment
carries SESSION_TOOL_PREFIX, which can only happen when we injected the tools.

The tap operates on ollama-shaped assistant messages (`obj["message"]`), the normalized shape
`_pump_stream` already works in. Grouping mirrors the reference tool-call parser in
llcat/llcat.py (~1074-1097): a non-empty `name` opens a new call, `arguments` accumulate onto
the currently-open call, and `index` is never consulted (on modern tool calls it's "either 0
or the utime" — meaningless). See brainstorm/user-account.md.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402

P = dyva.SESSION_TOOL_PREFIX


def _name_frag(idx, cid, name):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"index": idx, "id": cid,
                            "function": {"name": name, "arguments": ""}}]}


def _arg_frag(idx, args):
    # idx is deliberately varied in tests — the tap must ignore it (index is meaningless
    # on modern tool calls). Arg-only fragments carry no name and no id.
    return {"role": "assistant", "content": None,
            "tool_calls": [{"index": idx, "function": {"arguments": args}}]}


# ---- the tap: pass theirs through, withhold ours ---------------------------

def test_plain_content_chunk_passes_through_untouched():
    tap = dyva._SessionToolTap("uh1")
    msg = {"role": "assistant", "content": "hello", "tool_calls": None}
    assert tap.filter(msg) == msg
    assert not tap.has_capture()


def test_client_tool_call_is_forwarded_and_not_captured():
    tap = dyva._SessionToolTap("uh1")
    out = tap.filter(_name_frag(0, "c1", "get_weather"))
    assert out is not None and out["tool_calls"][0]["function"]["name"] == "get_weather"
    assert tap.filter(_arg_frag(0, '{"city":')) is not None   # arg fragments flow too
    assert tap.filter(_arg_frag(0, '"NYC"}')) is not None
    assert not tap.has_capture()                              # nothing of ours taken


def test_our_tool_call_is_withheld_and_reassembled():
    tap = dyva._SessionToolTap("uh1")
    # name arrives whole in one delta, then arguments dribble in — all withheld
    assert tap.filter(_name_frag(0, "c9", f"{P}_search_sessions")) is None
    assert tap.filter(_arg_frag(0, '{"query":')) is None
    assert tap.filter(_arg_frag(0, '"cats"}')) is None
    assert tap.has_capture()
    caps = tap.captured()
    assert len(caps) == 1
    assert caps[0]["function"]["name"] == f"{P}_search_sessions"
    assert json.loads(caps[0]["function"]["arguments"]) == {"query": "cats"}


def test_arg_only_fragment_sticks_to_the_open_call_ignoring_index():
    # the name opened an OURS call at index 0; the arg fragment arrives with a totally
    # different index (a utime) and no name — it must STILL be judged ours and withheld,
    # proving the grouping is sequential (open call), never index-based.
    tap = dyva._SessionToolTap("uh1")
    assert tap.filter(_name_frag(0, "c1", f"{P}_list_sessions")) is None
    assert tap.filter(_arg_frag(1795405296, '{"limit": 5}')) is None   # wild index, still ours
    assert tap.has_capture()
    assert json.loads(tap.captured()[0]["function"]["arguments"]) == {"limit": 5}


def test_two_of_our_calls_in_one_turn_captured_separately():
    # two prefixed calls stream back-to-back; each non-empty name opens a new call
    tap = dyva._SessionToolTap("uh1")
    assert tap.filter(_name_frag(0, "a", f"{P}_list_sessions")) is None
    assert tap.filter(_arg_frag(0, "{}")) is None
    assert tap.filter(_name_frag(0, "b", f"{P}_search_sessions")) is None   # same index, new call
    assert tap.filter(_arg_frag(0, '{"query": "x"}')) is None
    caps = tap.captured()
    assert [c["function"]["name"] for c in caps] == [f"{P}_list_sessions", f"{P}_search_sessions"]
    assert json.loads(caps[1]["function"]["arguments"]) == {"query": "x"}


def test_mixed_chunk_keeps_theirs_and_takes_ours():
    tap = dyva._SessionToolTap("uh1")
    mixed = {"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "get_weather", "arguments": "{}"}},
        {"index": 1, "id": "c2", "function": {"name": f"{P}_list_sessions", "arguments": "{}"}}]}
    out = tap.filter(mixed)
    assert [tc["function"]["name"] for tc in out["tool_calls"]] == ["get_weather"]
    assert tap.has_capture()
    assert tap.captured()[0]["function"]["name"] == f"{P}_list_sessions"


def test_saw_client_tool_flags_a_relayed_client_call():
    # the mixed-turn guard: if the model opened one of the CLIENT's tools this turn, we must
    # not hijack the stream to loop — relay it. saw_client_tool() reports that.
    tap = dyva._SessionToolTap("uh1")
    assert not tap.saw_client_tool()
    tap.filter(_name_frag(0, "c1", f"{P}_search_sessions"))   # ours — not a client call
    assert not tap.saw_client_tool()
    tap.filter(_name_frag(0, "c2", "get_weather"))            # the client's tool
    assert tap.saw_client_tool()


def test_saw_client_tool_false_on_pure_content_and_our_calls():
    tap = dyva._SessionToolTap("uh1")
    tap.filter({"role": "assistant", "content": "hi", "tool_calls": None})
    tap.filter(_name_frag(0, "c1", f"{P}_list_sessions"))
    assert not tap.saw_client_tool()


def test_reset_clears_per_turn_state_for_the_next_turn():
    # the failover loop reuses ONE tap across turns — reset() must wipe captures and flags
    tap = dyva._SessionToolTap("uh1")
    tap.filter(_name_frag(0, "c1", f"{P}_search_sessions"))
    tap.filter(_name_frag(0, "c2", "get_weather"))
    assert tap.has_capture() and tap.saw_client_tool()
    tap.reset()
    assert not tap.has_capture()
    assert not tap.saw_client_tool()
    assert tap.captured() == []
    assert tap.userhash == "uh1"            # identity survives the reset


def test_content_alongside_our_tool_call_still_streams():
    # assistant said something AND opened our tool in the same delta: show the words,
    # swallow the tool call.
    tap = dyva._SessionToolTap("uh1")
    msg = {"role": "assistant", "content": "let me check",
           "tool_calls": [{"index": 0, "id": "c1",
                           "function": {"name": f"{P}_search_sessions", "arguments": "{}"}}]}
    out = tap.filter(msg)
    assert out is not None and out["content"] == "let me check"
    assert not out.get("tool_calls")
    assert tap.has_capture()


# ---- the loop step: run ours, append the transcript the next turn sees -----

@pytest.fixture
def indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(dyva, "_sessions_db", None)
    monkeypatch.setattr(dyva, "SEMINDEX_DB", str(tmp_path / "sem.db"))
    monkeypatch.setattr(dyva, "_sem_db", None)

    async def _fake_embed(session, model, text):
        v = [float((text or "").lower().count(w)) for w in ("routing", "vision", "cat")]
        return v if sum(v) else [1e-6, 0.0, 0.0]
    monkeypatch.setattr(dyva, "_embed_text", _fake_embed)
    yield


def test_loop_step_runs_tool_and_appends_transcript(indexes):
    dyva._session_upsert("uh1", "aff1", "Routing chat",
                         {"title": "Routing chat", "messages": [{"role": "user", "content": "x"}]})
    captured = [{"id": "c1", "function": {"name": f"{P}_list_sessions", "arguments": "{}"}}]
    msgs = [{"role": "user", "content": "what did we talk about"}]
    out = asyncio.run(dyva._session_loop_step(None, "uh1", captured, msgs))
    assert out[0] == msgs[0]                                   # original preserved
    assert out[1]["role"] == "assistant" and out[1]["tool_calls"] == captured
    assert out[2]["role"] == "tool" and out[2]["tool_call_id"] == "c1"
    assert "Routing chat" in out[2]["content"]                # the model sees the result


def test_loop_step_does_not_mutate_the_caller_messages(indexes):
    dyva._session_upsert("uh1", "aff1", "Routing chat",
                         {"title": "Routing chat", "messages": [{"role": "user", "content": "x"}]})
    captured = [{"id": "c1", "function": {"name": f"{P}_list_sessions", "arguments": "{}"}}]
    msgs = [{"role": "user", "content": "hi"}]
    asyncio.run(dyva._session_loop_step(None, "uh1", captured, msgs))
    assert msgs == [{"role": "user", "content": "hi"}]         # caller's list untouched


def test_loop_step_with_no_valid_calls_returns_messages_unchanged(indexes):
    msgs = [{"role": "user", "content": "hi"}]
    out = asyncio.run(dyva._session_loop_step(None, "uh1", [{"function": {"name": ""}}], msgs))
    assert out == msgs


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
