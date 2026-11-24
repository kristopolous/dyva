"""Context-overflow recognition across transports — pytest
(`uv run pytest tests/test_ctx_overflow.py -q`).

A host whose LOADED context window is smaller than the prompt rejects it with an
"exceeds the available context" / "maximum context length" / "exceed_context_size_error"
message. That is NOT a bad host: the prompt didn't fit THIS host's CURRENT window, which
the operator can reload bigger, and the host still serves smaller prompts fine. dyva must
(a) recognise it regardless of HOW it arrives — a non-2xx body, a mid-stream structured
{"error":...} chunk, OR (LocalAI) the error prose leaking through a `reasoning` delta —
(b) record the host's ceiling so the banker routes oversized prompts to a bigger host, and
(c) fail over WITHOUT dinging reputation, never leaking the error text to the client.

Live wire format captured from LocalAI (94.19.158.171:8081 qwen3.8-2b-q8), a 200k-token
prompt against a 64000 window:
  stream=false -> HTTP 500  {"error":{"code":500,"message":"rpc error: code = Internal
     desc = request (200020 tokens) exceeds the available context size (64000 tokens),
     try increasing it","type":""}}
  stream=true  -> HTTP 200, SSE:
     delta.content=null
     delta.reasoning="request (200020 tokens) exceeds the available context size (64000 tokens), try increasing it"
     data: {"error":{"code":"server_error","message":"rpc error: ... (64000 tokens)...","type":"server_error"}}
     data: [DONE]
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402

# The exact overflow strings dyva can see, one per dialect/transport.
_LOCALAI = "rpc error: code = Internal desc = request (200020 tokens) exceeds the available context size (64000 tokens), try increasing it"
_LMSTUDIO = '{"error":{"type":"exceed_context_size_error","n_ctx":8192}}'
_VLLM = "This model's maximum context length is 32768 tokens. However, you requested 40000 tokens."
_OLLAMA_NIMBLE = '{"error":"Context size has been exceeded."}'
_CONFIGURED = '{"error":{"message":"Prompt has 300954 tokens, but the configured context size is 262144 tokens"}}'


def test_is_ctx_overflow_recognises_every_dialect():
    for s in (_LOCALAI, _LMSTUDIO, _VLLM, _OLLAMA_NIMBLE, _CONFIGURED):
        assert dyva._is_ctx_overflow(s), s


def test_is_ctx_overflow_ignores_normal_text():
    assert not dyva._is_ctx_overflow("The quick brown fox jumps over the lazy dog.")
    assert not dyva._is_ctx_overflow("")
    assert not dyva._is_ctx_overflow(None)


def test_parse_ctx_limit_reads_localai_ceiling():
    # The ceiling is the window (64000), NOT the prompt size (200020).
    assert dyva._parse_ctx_limit(_LOCALAI) == 64000


def test_parse_ctx_limit_reads_configured_ceiling():
    assert dyva._parse_ctx_limit(_CONFIGURED) == 262144


def test_parse_ctx_limit_still_reads_vllm_and_nctx():
    assert dyva._parse_ctx_limit(_VLLM) == 32768
    assert dyva._parse_ctx_limit(_LMSTUDIO) == 8192


# --- streaming interception (the committed-stream pump) -----------------------

class _Resp:
    """A won upstream: one readline per queued raw SSE line, then EOF."""
    def __init__(self, lines):
        self._q = list(lines)
        self.content = self
    async def readline(self):
        await asyncio.sleep(0)
        return self._q.pop(0) if self._q else b""
    async def release(self):
        pass


class _Client:
    def __init__(self):
        self.writes = []
    async def write(self, b):
        self.writes.append(b)


# The LocalAI streamed overflow, as raw SSE lines the pump reads AFTER the (already
# converted & forwarded) first line. content:null first chunk is the first_line below.
_OVERFLOW_SSE = [
    b'data: {"choices":[{"index":0,"finish_reason":null,"delta":{"content":null,"reasoning":"request (200020 tokens) exceeds the available context size (64000 tokens), try increasing it"}}]}\n',
    b'data: {"error":{"code":"server_error","message":"rpc error: code = Internal desc = request (200020 tokens) exceeds the available context size (64000 tokens), try increasing it","type":"server_error"}}\n',
    b'data: [DONE]\n',
]
# first_line is the race-normalised chunk1 (content null -> ""), in Ollama shape.
_FIRST = json.dumps({"model": "m", "message": {"role": "assistant", "content": ""}, "done": False}).encode()


def test_pump_overflow_records_ceiling_and_suppresses_leak(monkeypatch):
    recorded = []
    monkeypatch.setattr(dyva, "mark_ctx_limit", lambda h, m, n: recorded.append((h, m, n)))
    client, resp = _Client(), _Resp(list(_OVERFLOW_SSE))
    status = asyncio.run(dyva._pump_stream(
        client, resp, _FIRST, "http://h", "m", False, True, None, None, ["", 0]))
    assert status == "overflow"
    assert recorded == [("http://h", "m", 64000)]
    # The error text NEVER reaches the client (no reasoning/thinking leak).
    blob = b"".join(client.writes)
    assert b"exceeds the available context" not in blob


# --- buffered (stream:false) interception -------------------------------------

def test_drain_overflow_records_ceiling(monkeypatch):
    recorded = []
    monkeypatch.setattr(dyva, "mark_ctx_limit", lambda h, m, n: recorded.append((h, m, n)))
    resp = _Resp(list(_OVERFLOW_SSE))
    data, outcome = asyncio.run(dyva._drain_stream(
        resp, _FIRST, True, "m", host="http://h", full="m"))
    assert outcome == "overflow"
    assert recorded == [("http://h", "m", 64000)]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
