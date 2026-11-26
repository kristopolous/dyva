"""graflex verifies a fish host's `asr` capability instead of trusting the route
(`uv run pytest tests/test_graflex_asr_probe.py -q`).

fish-speech lists /v1/asr in its OpenAPI whenever the route is built, even with NO ASR
model loaded — most hosts in the wild advertise it but 500 "'ModelManager' object has no
attribute 'asr_model'" on use. `_fish_asr_loaded` probes with a tiny silent clip and drops
`asr` ONLY on that definitive model-absent signal; a 200, a different error, or any
transient keeps the advertised cap (never drop a real cap on a probe hiccup). No network.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import graflex   # noqa: E402


# --- the probe clip ---------------------------------------------------------

def test_tiny_silent_wav_is_a_valid_wav():
    w = graflex._tiny_silent_wav(ms=200, sr=16000)
    assert w[:4] == b"RIFF" and w[8:12] == b"WAVE"
    assert b"data" in w
    assert len(w) == 44 + 2 * int(16000 * 0.2)     # header + 16-bit mono PCM


# --- fake async session -----------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def read(self):
        return self._body

    async def release(self):
        return None


class _Session:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = 0

    async def post(self, url, data=None, headers=None, allow_redirects=None):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._resp


def _loaded(resp=None, exc=None):
    return asyncio.run(graflex._fish_asr_loaded(_Session(resp, exc), "http://h:8080/", 20))


# --- verification behaviour -------------------------------------------------

def test_model_absent_500_drops_asr():
    body = b'{"message":"\'ModelManager\' object has no attribute \'asr_model\'"}'
    assert _loaded(_Resp(500, body)) is False


def test_modelmanager_wording_also_drops():
    assert _loaded(_Resp(503, b"ModelManager not ready")) is False


def test_200_keeps_asr():
    assert _loaded(_Resp(200, b"\x80")) is True


def test_other_error_is_inconclusive_and_keeps_asr():
    # a non-model-absent error (e.g. a bad-audio 400) must NOT drop a cap — the host
    # clearly HAS an asr model, it just didn't like our probe clip.
    assert _loaded(_Resp(400, b'{"message":"could not decode audio"}')) is True


def test_transient_exception_keeps_asr():
    assert _loaded(exc=asyncio.TimeoutError()) is True


def test_missing_msgpack_skips_probe_and_keeps_asr(monkeypatch):
    monkeypatch.setattr(graflex, "_msgpack", None)
    sess = _Session(_Resp(500, b"asr_model"))
    assert asyncio.run(graflex._fish_asr_loaded(sess, "http://h:8080/", 20)) is True
    assert sess.calls == 0        # no probe attempted without msgpack


def test_probe_targets_the_asr_route():
    class _Cap(_Session):
        def __init__(self):
            super().__init__(_Resp(200, b"\x80"))
            self.url = None

        async def post(self, url, data=None, headers=None, allow_redirects=None):
            self.url = url
            return await super().post(url, data, headers, allow_redirects)

    sess = _Cap()
    asyncio.run(graflex._fish_asr_loaded(sess, "http://h:8080/", 20))
    assert sess.url == "http://h:8080/v1/asr"
