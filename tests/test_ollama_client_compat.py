"""Ollama-client wire-compatibility — pytest.

These pin the response SHAPES the real ollama client (`api/client.go`,
`cmd/cmd.go`) requires, which dyva has broken more than once:

  * /api/chat replies carry `message.content`; /api/generate replies carry a
    top-level `response` STRING (NOT `message`). A generate client reads
    `resp.Response` and renders blank if it only finds `message` — the
    "`ollama run MODEL \"prompt\"` prints nothing" bug.
  * dyva routes /api/generate to an upstream /v1/completions, whose text is
    in `choices[].text` (not `choices[].message.content`), so the converter
    must read both or the answer is silently emptied.
  * `__dyva_info__` must come back as a valid chat/generate message, not a
    bare `{host,model,service}` dict (which unmarshals to an empty response).

Pure-function tests — no host is contacted.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


# --- /api/generate: OpenAI-dialect (/v1/completions) conversion -------------

def test_openai_completions_text_is_not_dropped():
    # /v1/completions puts the answer in choices[].text — the old converter only
    # read message.content and emitted "" (the blank-generate bug).
    comp = {"model": "m", "choices": [{"text": "Rayleigh scattering.", "finish_reason": "stop"}]}
    out = dyva.openai_to_ollama(comp, "qwen3.8")
    assert out["message"]["content"] == "Rayleigh scattering."


def test_openai_chat_completions_content_still_read():
    chat = {"model": "m", "choices": [{"message": {"role": "assistant", "content": "hi"},
                                        "finish_reason": "stop"}]}
    assert dyva.openai_to_ollama(chat, "m")["message"]["content"] == "hi"


def test_stream_completions_text_is_not_dropped():
    line = 'data: ' + json.dumps({"choices": [{"text": "blue"}]})
    obj = dyva.openai_stream_to_ollama(line, "m")
    assert obj["message"]["content"] == "blue"


# --- chat -> generate shape translation -------------------------------------

def test_chat_to_generate_moves_content_to_response():
    chat = {"model": "m", "created_at": "t",
            "message": {"role": "assistant", "content": "the answer", "thinking": "reasoning"},
            "done": True, "done_reason": "stop", "eval_count": 9}
    gen = dyva._ollama_chat_to_generate(chat)
    assert gen["response"] == "the answer"      # generate clients read THIS
    assert gen["thinking"] == "reasoning"
    assert "message" not in gen                 # must NOT carry the chat field
    assert gen["done"] is True and gen["eval_count"] == 9   # passthrough of the rest


def test_chat_to_generate_native_passthrough_untouched():
    # A native-ollama upstream already streams generate shape (no `message` key) —
    # the translator must leave it exactly alone.
    native = {"model": "m", "response": "HELLO.", "done": True, "context": [1, 2]}
    assert dyva._ollama_chat_to_generate(native) == native


def test_generate_line_translates_one_ndjson_line():
    line = json.dumps({"model": "m", "message": {"role": "assistant", "content": "Hi"},
                       "done": False}).encode()
    out = json.loads(dyva._generate_line(line))
    assert out["response"] == "Hi" and "message" not in out


def test_generate_line_passes_blank_and_nonjson_through():
    assert dyva._generate_line(b"\n") == b"\n"
    assert dyva._generate_line(b"not json") == b"not json"


# --- __dyva_info__ response shape -------------------------------------------

def _body(resp):
    return json.loads(resp.body.decode())


def test_info_response_ollama_nonstream_is_a_chat_message():
    info = {"host": "1.2.3.4:8002", "model": "qwen3.8-27b", "service": "llama.cpp"}
    b = _body(dyva._info_response("qwen3.8", info, do_stream=False, openai_format=False))
    # the ollama client reads message.content — a bare {host,...} dict rendered blank
    assert b["message"]["content"] == json.dumps(info)
    assert b["done"] is True
    assert b["dyva_info"] == info               # structured escape hatch preserved


def test_info_response_openai_nonstream_is_a_chat_completion():
    info = {"host": "1.2.3.4:8002", "model": "qwen3.8-27b", "service": "llama.cpp"}
    b = _body(dyva._info_response("qwen3.8", info, do_stream=False, openai_format=True))
    assert b["choices"][0]["message"]["content"] == json.dumps(info)
    assert b["object"] == "chat.completion"
    assert b["dyva_info"] == info


def test_info_response_stream_ollama_has_content_and_done():
    info = {"host": "h", "model": "m", "service": "ollama"}
    resp = dyva._info_response("m", info, do_stream=True, openai_format=False)
    lines = [json.loads(l) for l in resp.text.strip().split("\n")]
    assert lines[0]["message"]["content"] == json.dumps(info)
    assert lines[-1]["done"] is True


def test_info_response_generate_shape_uses_response_not_message():
    # The one-shot `ollama run MODEL "__dyva_info__"` hits /api/generate, which reads
    # `response` — the chat-shaped `message` reply rendered blank there.
    info = {"host": "1.2.3.4:8002", "model": "qwen3.8-27b", "service": "llama.cpp"}
    b = _body(dyva._info_response("qwen3.8", info, do_stream=False, generate=True))
    assert b["response"] == json.dumps(info)
    assert "message" not in b
    assert b["dyva_info"] == info


def test_info_response_generate_stream_uses_response():
    info = {"host": "h", "model": "m", "service": "ollama"}
    resp = dyva._info_response("m", info, do_stream=True, generate=True)
    lines = [json.loads(l) for l in resp.text.strip().split("\n")]
    assert lines[0]["response"] == json.dumps(info)
    assert "message" not in lines[0]
    assert lines[-1]["done"] is True


# --- call-site test: the plain __dyva_info__ path THROUGH _proxy_generate ---
# (the earlier tests pinned the function; the bug was a call site that forgot
# generate=True, so exercise the handler end-to-end for the info branch.)

class _FakeReq:
    def __init__(self, body):
        self._b = body
        self.remote = "127.0.0.1"

    async def json(self):
        return self._b


def test_proxy_generate_info_path_returns_generate_shape(monkeypatch):
    import asyncio
    info = {"host": "1.2.3.4:8002", "model": "qwen3.8-27b", "service": "llama.cpp"}
    monkeypatch.setattr(dyva, "_would_use", lambda *a, **k: info)
    req = _FakeReq({"model": "qwen3.8", "prompt": "__dyva_info__"})
    resp = asyncio.run(dyva._proxy_generate(req, None))
    b = json.loads(resp.body.decode())
    assert b["response"] == json.dumps(info)   # one-shot `ollama run` reads THIS
    assert "message" not in b
    assert b["dyva_info"] == info


def test_proxy_generate_info_next_path_returns_generate_shape(monkeypatch):
    import asyncio
    info = {"host": "5.6.7.8:1234", "model": "m", "service": "ollama"}
    monkeypatch.setattr(dyva, "_would_use", lambda *a, **k: info)
    monkeypatch.setattr(dyva, "get_last", lambda m: None)
    monkeypatch.setattr(dyva, "_drop_last_host", lambda m: None)
    req = _FakeReq({"model": "m", "prompt": "__dyva_info__:next"})
    resp = asyncio.run(dyva._proxy_generate(req, None))
    b = json.loads(resp.body.decode())
    assert b["response"] == json.dumps(info) and "message" not in b


# --- __dyva_info__:hosts -----------------------------------------------------

def _fake_find_servers(*a, **k):
    # find_servers yields (tier, host, [models]); some rows have no models (skipped).
    return [(0, "http://1.1.1.1:8002", ["qwen3.8-27b"]),
            (0, "http://2.2.2.2:1234", ["qwen3.8-27b"]),
            (1, "http://3.3.3.3:11434", [])]


def test_info_hosts_lists_candidates(monkeypatch):
    monkeypatch.setattr(dyva, "find_servers", _fake_find_servers)
    cand = dyva._info_hosts(";state=good;tps>20;qwen3.8", [])
    assert [c["host"] for c in cand] == ["1.1.1.1:8002", "2.2.2.2:1234"]  # modelless row dropped
    assert cand[0]["model"] == "qwen3.8-27b"


def test_proxy_generate_hosts_returns_bare_lines(monkeypatch):
    import asyncio
    monkeypatch.setattr(dyva, "find_servers", _fake_find_servers)
    req = _FakeReq({"model": ";state=good;tps>20;qwen3.8", "prompt": "__dyva_info__:hosts"})
    resp = asyncio.run(dyva._proxy_generate(req, None))
    b = json.loads(resp.body.decode())
    # one bare host per line, pipes straight to a file
    assert b["response"] == "1.1.1.1:8002\n2.2.2.2:1234"
    assert "message" not in b
    assert [c["host"] for c in b["dyva_info"]] == ["1.1.1.1:8002", "2.2.2.2:1234"]
