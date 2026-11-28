"""Anthropic `/v1/messages` wire-compatibility — pytest
(`uv run pytest tests/test_anthropic_messages.py -q`).

dyva already speaks two INBOUND dialects (OpenAI `/v1/chat/completions`,
Ollama `/api/chat`) that both normalize into the one internal chat pipeline
and race the pool. This adds a third inbound dialect — Anthropic's Messages
API — as a pair of pure translators at the edge, so the routing/failover
machinery is reused untouched:

  * anthropic_to_openai(body)  : Anthropic request  -> OpenAI chat body
                                 (then the existing to_ollama() takes over)
  * to_anthropic(resp, model)  : internal ollama reply -> Anthropic Message
  * _OpenAIToAnthropicSSE       : OpenAI SSE stream -> Anthropic event stream

Pure-function tests — no host is contacted.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


# ---- request translation: anthropic_to_openai ------------------------------

def test_system_string_becomes_leading_system_message():
    body = {"model": "m", "system": "Be terse.",
            "messages": [{"role": "user", "content": "hi"}]}
    out = dyva.anthropic_to_openai(body)
    assert out["messages"][0] == {"role": "system", "content": "Be terse."}
    assert out["messages"][1] == {"role": "user", "content": "hi"}
    assert out["model"] == "m"


def test_system_as_block_list_is_flattened_to_text():
    body = {"model": "m",
            "system": [{"type": "text", "text": "A."}, {"type": "text", "text": "B."}],
            "messages": [{"role": "user", "content": "hi"}]}
    out = dyva.anthropic_to_openai(body)
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][0]["content"] == "A.\nB."


def test_plain_string_content_passes_through():
    body = {"model": "m", "messages": [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"}]}
    out = dyva.anthropic_to_openai(body)
    assert [m["content"] for m in out["messages"]] == ["one", "two", "three"]


def test_text_blocks_collapse_to_a_string():
    body = {"model": "m", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "a"},
                                     {"type": "text", "text": "b"}]}]}
    out = dyva.anthropic_to_openai(body)
    # a text-only block list becomes a plain string (the shape to_ollama likes)
    assert out["messages"][0]["content"] == "a\nb"


def test_image_block_becomes_openai_image_url_data_uri():
    body = {"model": "m", "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png", "data": "QUJD"}}]}]}
    out = dyva.anthropic_to_openai(body)
    parts = out["messages"][0]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "what is this"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_image_block_url_source_is_passed_through():
    body = {"model": "m", "messages": [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}]}]}
    out = dyva.anthropic_to_openai(body)
    assert out["messages"][0]["content"][0]["image_url"]["url"] == "https://x/y.png"


def test_assistant_tool_use_becomes_openai_tool_calls():
    body = {"model": "m", "messages": [
        {"role": "assistant", "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather",
             "input": {"city": "LA"}}]}]}
    out = dyva.anthropic_to_openai(body)
    msg = out["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "let me check"
    tc = msg["tool_calls"][0]
    assert tc["id"] == "toolu_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "LA"}


def test_user_tool_result_becomes_openai_tool_message():
    body = {"model": "m", "messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": "72F and sunny"}]}]}
    out = dyva.anthropic_to_openai(body)
    msg = out["messages"][0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "toolu_1"
    assert msg["content"] == "72F and sunny"


def test_tool_result_block_content_list_is_flattened():
    body = {"model": "m", "messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2",
             "content": [{"type": "text", "text": "line1"},
                         {"type": "text", "text": "line2"}]}]}]}
    out = dyva.anthropic_to_openai(body)
    assert out["messages"][0]["content"] == "line1\nline2"


def test_tools_and_tool_choice_map_to_openai():
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "get_weather", "description": "gets weather",
                       "input_schema": {"type": "object",
                                        "properties": {"city": {"type": "string"}}}}],
            "tool_choice": {"type": "auto"}}
    out = dyva.anthropic_to_openai(body)
    t = out["tools"][0]
    assert t["type"] == "function"
    assert t["function"]["name"] == "get_weather"
    assert t["function"]["description"] == "gets weather"
    assert t["function"]["parameters"] == {"type": "object",
                                           "properties": {"city": {"type": "string"}}}
    assert out["tool_choice"] == "auto"


def test_tool_choice_any_is_required_and_tool_is_named():
    assert dyva.anthropic_to_openai(
        {"model": "m", "messages": [], "tool_choice": {"type": "any"}}
    )["tool_choice"] == "required"
    out = dyva.anthropic_to_openai(
        {"model": "m", "messages": [], "tool_choice": {"type": "tool", "name": "f"}})
    assert out["tool_choice"] == {"type": "function", "function": {"name": "f"}}


def test_sampling_params_and_stop_sequences_carry_over():
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 256, "temperature": 0.3, "top_p": 0.9,
            "stop_sequences": ["STOP", "END"], "stream": True}
    out = dyva.anthropic_to_openai(body)
    assert out["max_tokens"] == 256
    assert out["temperature"] == 0.3
    assert out["top_p"] == 0.9
    assert out["stop"] == ["STOP", "END"]
    assert out["stream"] is True


# ---- non-streaming response: to_anthropic ----------------------------------

def test_to_anthropic_text_reply():
    resp = {"message": {"role": "assistant", "content": "The sky is blue."},
            "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 5}
    out = dyva.to_anthropic(resp, "claude-3-haiku")
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert out["model"] == "claude-3-haiku"
    assert out["content"] == [{"type": "text", "text": "The sky is blue."}]
    assert out["stop_reason"] == "end_turn"
    assert out["stop_sequence"] is None
    assert out["usage"] == {"input_tokens": 12, "output_tokens": 5}
    assert out["id"].startswith("msg_")


def test_to_anthropic_length_maps_to_max_tokens():
    resp = {"message": {"role": "assistant", "content": "cut"}, "done_reason": "length"}
    assert dyva.to_anthropic(resp, "m")["stop_reason"] == "max_tokens"


def test_to_anthropic_tool_call_becomes_tool_use_block():
    resp = {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "get_weather",
                                                     "arguments": {"city": "LA"}}}]},
            "done_reason": "stop"}
    out = dyva.to_anthropic(resp, "m")
    assert out["stop_reason"] == "tool_use"
    block = [b for b in out["content"] if b["type"] == "tool_use"][0]
    assert block["name"] == "get_weather"
    assert block["input"] == {"city": "LA"}
    assert block["id"].startswith("toolu_")


def test_to_anthropic_tool_call_string_arguments_are_parsed():
    resp = {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "f",
                                                     "arguments": '{"x": 1}'}}]}}
    block = [b for b in dyva.to_anthropic(resp, "m")["content"]
             if b["type"] == "tool_use"][0]
    assert block["input"] == {"x": 1}


def test_to_anthropic_includes_thinking_block_when_present():
    resp = {"message": {"role": "assistant", "content": "answer",
                        "thinking": "reasoning here"}}
    out = dyva.to_anthropic(resp, "m")
    assert out["content"][0] == {"type": "thinking", "thinking": "reasoning here"}
    assert out["content"][1] == {"type": "text", "text": "answer"}


def test_to_anthropic_empty_text_with_no_tool_still_has_text_block():
    # a bare empty answer must still be a valid Message with one (empty) text block
    out = dyva.to_anthropic({"message": {"role": "assistant", "content": ""}}, "m")
    assert out["content"] == [{"type": "text", "text": ""}]


# ---- streaming translation: _OpenAIToAnthropicSSE --------------------------

def _events(raw):
    """Parse an Anthropic SSE byte string into [(event_type, data_obj), ...],
    skipping SSE comment lines."""
    out = []
    for block in raw.decode().split("\n\n"):
        block = block.strip("\n")
        if not block or block.startswith(":"):
            continue
        ev, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        out.append((ev, data))
    return out


def _openai_sse(delta=None, finish_reason=None):
    chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
             "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]}
    return ("data: " + json.dumps(chunk) + "\n\n").encode()


def test_stream_text_produces_well_formed_anthropic_events():
    tr = dyva._OpenAIToAnthropicSSE("claude-x")
    raw = b""
    raw += tr.feed(_openai_sse({"role": "assistant", "content": "Hel"}))
    raw += tr.feed(_openai_sse({"content": "lo"}))
    raw += tr.feed(_openai_sse({}, finish_reason="stop"))
    raw += tr.close()
    evs = _events(raw)
    types = [e[0] for e in evs]
    assert types == ["message_start", "content_block_start", "content_block_delta",
                     "content_block_delta", "content_block_stop",
                     "message_delta", "message_stop"]
    # message_start carries the requested model and an assistant role
    assert evs[0][1]["message"]["model"] == "claude-x"
    assert evs[0][1]["message"]["role"] == "assistant"
    # the text deltas reassemble to the full answer
    text = "".join(e[1]["delta"]["text"] for e in evs if e[0] == "content_block_delta")
    assert text == "Hello"
    # the opening text block is index 0, type text
    assert evs[1][1] == {"type": "content_block_start", "index": 0,
                         "content_block": {"type": "text", "text": ""}}
    # terminal stop_reason mapped stop -> end_turn
    assert evs[-2][1]["delta"]["stop_reason"] == "end_turn"


def test_stream_length_finish_maps_to_max_tokens():
    tr = dyva._OpenAIToAnthropicSSE("m")
    raw = tr.feed(_openai_sse({"content": "hi"})) + tr.feed(_openai_sse({}, finish_reason="length")) + tr.close()
    md = [d for e, d in _events(raw) if e == "message_delta"][0]
    assert md["delta"]["stop_reason"] == "max_tokens"


def test_stream_comment_keepalive_passes_through_without_breaking():
    tr = dyva._OpenAIToAnthropicSSE("m")
    out = tr.feed(b": keepalive\n\n")
    # a keepalive before any token must not emit a premature message_start;
    # it is forwarded as an (ignored) SSE comment
    assert b"message_start" not in out
    assert out.strip().startswith(b":")


def test_stream_tool_call_becomes_tool_use_block_and_input_json_delta():
    tr = dyva._OpenAIToAnthropicSSE("m")
    raw = b""
    # opening tool-call delta: index+id+name + start of arguments
    raw += tr.feed(_openai_sse({"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"ci'}}]}))
    # continuation: same index, more argument text
    raw += tr.feed(_openai_sse({"tool_calls": [
        {"index": 0, "function": {"arguments": 'ty": "LA"}'}}]}))
    raw += tr.feed(_openai_sse({}, finish_reason="tool_calls"))
    raw += tr.close()
    evs = _events(raw)
    start = [d for e, d in evs if e == "content_block_start"][0]
    assert start["content_block"]["type"] == "tool_use"
    assert start["content_block"]["name"] == "get_weather"
    assert start["content_block"]["id"] == "call_1"
    frags = "".join(d["delta"]["partial_json"]
                    for e, d in evs if e == "content_block_delta"
                    and d["delta"]["type"] == "input_json_delta")
    assert json.loads(frags) == {"city": "LA"}
    md = [d for e, d in evs if e == "message_delta"][0]
    assert md["delta"]["stop_reason"] == "tool_use"


def test_stream_close_without_terminal_still_ends_the_message():
    # A mid-stream drop with no finish chunk (pool exhausted): close() must synthesize
    # content_block_stop + message_delta + message_stop so the client doesn't hang.
    tr = dyva._OpenAIToAnthropicSSE("m")
    raw = tr.feed(_openai_sse({"content": "partial"})) + tr.close()
    types = [e for e, _ in _events(raw)]
    assert types[-1] == "message_stop"
    assert "content_block_stop" in types and "message_delta" in types


def test_stream_close_before_any_output_emits_nothing():
    # Pure keepalive then client/host gone before a single token: no message_start was
    # emitted, so close() must emit nothing (no half-open Anthropic message).
    tr = dyva._OpenAIToAnthropicSSE("m")
    assert tr.feed(b": keepalive\n\n").strip().startswith(b":")
    assert tr.close() == b""


def test_stream_error_chunk_becomes_anthropic_error_event():
    tr = dyva._OpenAIToAnthropicSSE("m")
    raw = tr.feed(("data: " + json.dumps({"error": {"message": "all servers failed"}}) + "\n\n").encode())
    evs = _events(raw)
    assert evs[0][0] == "error"
    assert evs[0][1]["type"] == "error"
    assert evs[0][1]["error"]["message"] == "all servers failed"


# ---- models list (Anthropic's /v1/models is the SAME path as OpenAI's) ------

def test_to_anthropic_models_shape():
    out = dyva.to_anthropic_models([{"id": "qwen3"}, {"id": "llama3"}])
    assert out["has_more"] is False
    assert out["first_id"] == "qwen3" and out["last_id"] == "llama3"
    m = out["data"][0]
    assert m["type"] == "model"
    assert m["id"] == "qwen3"
    assert m["display_name"] == "qwen3"
    assert "created_at" in m


def test_to_anthropic_models_empty():
    out = dyva.to_anthropic_models([])
    assert out["data"] == [] and out["first_id"] is None and out["last_id"] is None


def test_models_handler_negotiates_on_anthropic_version_header(monkeypatch):
    async def _ok(_r):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)
    monkeypatch.setattr(dyva, "listed_models", lambda: [{"id": "qwen3", "count": 1}])

    class _R:
        def __init__(self, headers):
            self.headers = headers
            self.query = {}
    # an Anthropic client (sends anthropic-version) gets the Anthropic catalog shape
    a = asyncio.run(dyva.handle_v1_models(_R({"anthropic-version": "2023-06-01"})))
    assert json.loads(a.body)["data"][0]["type"] == "model"
    # a plain OpenAI client still gets the OpenAI shape
    o = asyncio.run(dyva.handle_v1_models(_R({})))
    assert json.loads(o.body)["object"] == "list"
    assert json.loads(o.body)["data"][0]["object"] == "model"


# ---- the handler wiring ----------------------------------------------------

class _Req:
    def __init__(self, payload):
        self._payload = payload
        self.app = {"semaphore": _NullCtx(), "session": object()}
        self.remote = "127.0.0.1"
        self.headers = {}

    async def json(self):
        return self._payload


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_handler_translates_request_and_flags_anthropic_output(monkeypatch):
    captured = {}

    async def _ok(_r):
        return None

    async def _fake_proxy(request, session, model_in, opayload, do_stream, openai_format,
                          anthropic=False):
        captured.update(model=model_in, do_stream=do_stream,
                        openai_format=openai_format, anthropic=anthropic,
                        payload=opayload)
        return "SENTINEL"

    monkeypatch.setattr(dyva, "_check_local", _ok)
    monkeypatch.setattr(dyva, "_proxy_chat", _fake_proxy)

    body = {"model": "qwen3", "max_tokens": 100, "stream": True,
            "system": "Be nice.",
            "messages": [{"role": "user", "content": "hi"}]}
    out = asyncio.run(dyva.handle_anthropic_messages(_Req(body)))
    assert out == "SENTINEL"
    assert captured["model"] == "qwen3"
    assert captured["do_stream"] is True
    assert captured["anthropic"] is True
    assert captured["openai_format"] is True        # Anthropic reuses the SSE transport path
    # the system prompt was folded into a leading system message, max_tokens -> num_predict
    assert captured["payload"]["messages"][0]["role"] == "system"
    assert captured["payload"].get("num_predict") == 100


def test_handler_requires_model(monkeypatch):
    async def _ok(_r):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)
    resp = asyncio.run(dyva.handle_anthropic_messages(_Req({"messages": []})))
    assert resp.status == 400


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
