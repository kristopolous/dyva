"""Speech-to-text: OpenAI /v1/audio/transcriptions -> fish /v1/asr
(`uv run pytest tests/test_stt_transcription.py -q`).

dyva fronts fish-speech `asr` hosts with the OpenAI transcription shape. These tests
cover the pure pieces (language mapping, response parsing), the host filter
(`_find_asr_hosts` = fish hosts advertising `asr`, nothing else), the one-shot
`_asr_transcribe` against a fake fish host, and the handler's status/format branches
with the race stubbed out. No DB, no network.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402
from aiohttp.test_utils import make_mocked_request  # noqa: E402

import msgpack   # noqa: E402  (a dev/test dep; the runtime import is optional)


# --- language mapping -------------------------------------------------------

def test_asr_lang_passes_through_fish_enum():
    for l in ("zh", "en", "ja", "auto"):
        assert dyva._asr_lang(l) == l


def test_asr_lang_maps_openai_hints():
    assert dyva._asr_lang("EN") == "en"          # case-insensitive
    assert dyva._asr_lang("en-US") == "en"
    assert dyva._asr_lang("zh-CN") == "zh"
    assert dyva._asr_lang("ja-JP") == "ja"
    assert dyva._asr_lang("japanese") == "ja"


def test_asr_lang_unknown_and_empty_fall_back_to_auto():
    assert dyva._asr_lang("fr") == "auto"
    assert dyva._asr_lang("") == "auto"
    assert dyva._asr_lang(None) == "auto"


# --- response parsing -------------------------------------------------------

def test_asr_parse_joins_transcription_parts():
    raw = msgpack.packb({"transcriptions": [{"text": "hello "}, {"text": "world"}]})
    assert dyva._asr_parse_text(raw) == "hello world"


def test_asr_parse_tolerates_bare_text():
    raw = msgpack.packb({"text": "hi there"})
    assert dyva._asr_parse_text(raw) == "hi there"


def test_asr_parse_falls_back_to_json():
    # a host that answered JSON instead of msgpack still parses (msgpack decode fails
    # on the leading '{', we fall through to json).
    assert dyva._asr_parse_text(b'{"text":"json body"}') == "json body"


def test_asr_parse_unexpected_shape_raises():
    raw = msgpack.packb({"not_a_transcript": 1})
    try:
        dyva._asr_parse_text(raw)
    except dyva._AsrError:
        return
    assert False, "expected _AsrError on an unexpected response shape"


# --- host filter ------------------------------------------------------------

def _seed_hosts(monkeypatch, servers):
    monkeypatch.setattr(dyva, "load_servers", lambda: servers)
    monkeypatch.setattr(dyva, "load_marks", lambda: {})
    monkeypatch.setattr(dyva, "get_last", lambda k: None)
    monkeypatch.setattr(dyva, "rerank_scores", lambda: {})
    monkeypatch.setattr(dyva, "_idle_first", lambda hosts: list(hosts))
    monkeypatch.setattr(dyva, "capability_tier", lambda *a, **k: 0)


def test_find_asr_hosts_only_fish_with_asr_cap(monkeypatch):
    servers = [
        {"service": "fish", "server": "A", "capabilities": ["asr", "tts", "chat"]},  # in
        {"service": "fish", "server": "B", "capabilities": ["tts", "references"]},   # no asr -> out
        {"service": "fish", "server": "C", "capabilities": ["asr"]},                 # in
        {"service": "chatterbox", "server": "D", "capabilities": ["asr"]},           # not fish -> out
        {"service": "fish", "server": "E"},                                          # no caps -> out
    ]
    _seed_hosts(monkeypatch, servers)
    assert set(dyva._find_asr_hosts()) == {"A", "C"}


def test_find_asr_hosts_empty_when_no_supply(monkeypatch):
    _seed_hosts(monkeypatch, [
        {"service": "fish", "server": "B", "capabilities": ["tts"]},
        {"service": "ollama", "server": "O", "models": ["qwen3.8:latest"]},
    ])
    assert dyva._find_asr_hosts() == []


# --- one-shot transcribe against a fake fish host ---------------------------

class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._body


class _Session:
    def __init__(self, resp):
        self._resp = resp
        self.sent = None

    def post(self, url, data=None, timeout=None, headers=None):
        self.sent = {"url": url, "data": data, "headers": headers}
        return self._resp


def test_asr_transcribe_returns_text_and_posts_msgpack():
    body = msgpack.packb({"transcriptions": [{"text": "a transcript"}]})
    sess = _Session(_Resp(200, body))
    text = asyncio.run(dyva._asr_transcribe(sess, "http://h:8080", b"RIFFfake", 16000, "en"))
    assert text == "a transcript"
    # it POSTed msgpack to the fish ASR route, carrying the audio + mapped language
    assert sess.sent["url"] == "http://h:8080/v1/asr"
    assert sess.sent["headers"]["Content-Type"] == "application/msgpack"
    req = msgpack.unpackb(sess.sent["data"], raw=False)
    assert req["audios"] == [b"RIFFfake"]
    assert req["language"] == "en"


def test_asr_transcribe_raises_on_model_absent_500():
    sess = _Session(_Resp(500, b'{"message":"\'ModelManager\' object has no attribute \'asr_model\'"}'))
    try:
        asyncio.run(dyva._asr_transcribe(sess, "http://h:8080", b"x", 16000, "auto"))
    except dyva._AsrError as e:
        assert "500" in str(e)
        return
    assert False, "expected _AsrError on a 500"


def test_asr_transcribe_requires_msgpack(monkeypatch):
    monkeypatch.setattr(dyva, "_msgpack", None)
    try:
        asyncio.run(dyva._asr_transcribe(object(), "http://h:8080", b"x", 16000, "auto"))
    except dyva._AsrError:
        return
    assert False, "expected _AsrError when msgpack is unavailable"


# --- handler (race stubbed) -------------------------------------------------

class _Part:
    def __init__(self, name, data=None, text=None):
        self.name = name
        self._data = data
        self._text = text
        self._read = False

    async def read_chunk(self, size=8192):
        if self._read:
            return b""
        self._read = True
        return self._data

    async def text(self):
        return self._text


class _Reader:
    def __init__(self, parts):
        self._parts = list(parts)
        self._i = 0

    async def next(self):
        if self._i >= len(self._parts):
            return None
        p = self._parts[self._i]
        self._i += 1
        return p


def _req(parts):
    r = make_mocked_request("POST", "/v1/audio/transcriptions")
    r.app["session"] = None

    async def _multipart():
        return _Reader(parts)

    r.multipart = _multipart
    return r


def _common(monkeypatch):
    async def _ok(_r):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)

    async def _reg(*a, **k):
        return "wid1"

    async def _unreg(*a, **k):
        return None

    async def _bcast(*a, **k):
        return None
    monkeypatch.setattr(dyva, "_register_worker", _reg)
    monkeypatch.setattr(dyva, "_unregister_worker", _unreg)
    monkeypatch.setattr(dyva, "broadcast_activity", _bcast)


def _stub_race(monkeypatch, result):
    async def _race(hosts, attempt, key, **kw):
        return result, False, 1, {}
    monkeypatch.setattr(dyva, "_race_hosts", _race)


def test_transcriptions_no_audio_is_400(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_find_asr_hosts", lambda: ["h"])
    resp = asyncio.run(dyva.handle_transcriptions(_req([_Part("model", text="whisper-1")])))
    assert resp.status == 400


def test_transcriptions_no_host_is_503(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_find_asr_hosts", lambda: [])
    resp = asyncio.run(dyva.handle_transcriptions(_req([_Part("file", data=b"RIFFaudio")])))
    assert resp.status == 503


def test_transcriptions_requires_msgpack(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_msgpack", None)
    resp = asyncio.run(dyva.handle_transcriptions(_req([_Part("file", data=b"RIFFaudio")])))
    assert resp.status == 503


def test_transcriptions_all_hosts_failed_is_502(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_find_asr_hosts", lambda: ["h"])
    _stub_race(monkeypatch, None)
    resp = asyncio.run(dyva.handle_transcriptions(_req([_Part("file", data=b"RIFFaudio")])))
    assert resp.status == 502


def test_transcriptions_success_json(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_find_asr_hosts", lambda: ["h"])
    _stub_race(monkeypatch, ("http://h:8080", "the words"))
    resp = asyncio.run(dyva.handle_transcriptions(_req([_Part("file", data=b"RIFFaudio")])))
    assert resp.status == 200
    assert json.loads(resp.body.decode("utf-8")) == {"text": "the words"}
    assert resp.headers["X-Dyva-Host"] == "http://h:8080"


def test_transcriptions_response_format_text(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_find_asr_hosts", lambda: ["h"])
    _stub_race(monkeypatch, ("http://h:8080", "plain words"))
    resp = asyncio.run(dyva.handle_transcriptions(_req([
        _Part("file", data=b"RIFFaudio"), _Part("response_format", text="text")])))
    assert resp.status == 200
    assert resp.content_type == "text/plain"
    assert resp.text == "plain words"


def test_transcriptions_verbose_json_includes_language(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(dyva, "_find_asr_hosts", lambda: ["h"])
    _stub_race(monkeypatch, ("http://h:8080", "bonjour"))
    resp = asyncio.run(dyva.handle_transcriptions(_req([
        _Part("file", data=b"RIFFaudio"),
        _Part("response_format", text="verbose_json"),
        _Part("language", text="ja")])))
    out = json.loads(resp.body.decode("utf-8"))
    assert out["text"] == "bonjour"
    assert out["language"] == "ja"
