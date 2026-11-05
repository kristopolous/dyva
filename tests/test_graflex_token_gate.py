"""graflex token-gate detection — pytest (`uv run pytest tests/test_graflex_token_gate.py -q`).

graflex's ComfyUI check only did GETs (/models/checkpoints, /api/system_stats), which a
token-gated host answers 200 — so it got promoted as healthy, and dyva then burned ~30s per
request discovering the gate on POST /prompt. `_comfy_auth_gated` probes /prompt with a
throwaway empty graph: an auth refusal (401/403, or a 200/other carrying a token/auth error)
=> gated; a 400 validation error => OPEN (the empty graph is rejected, but only AFTER auth
passes); a connection failure is unreachable, not an auth verdict (the check handles that).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import graflex   # noqa: E402


class _Resp:
    def __init__(self, status, text=""):
        self.status, self._text = status, text
    async def text(self):
        return self._text
    async def release(self):
        pass


class _Session:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc
    def post(self, *a, **k):
        async def _c():
            if self._exc:
                raise self._exc
            return self._resp
        return _c()


def _gated(resp=None, exc=None):
    return asyncio.run(graflex._comfy_auth_gated(_Session(resp, exc), "http://h:8188/", timeout=1))


def test_401_is_gated():
    assert _gated(_Resp(401)) is True


def test_403_is_gated():
    assert _gated(_Resp(403)) is True


def test_200_with_token_error_is_gated():
    assert _gated(_Resp(200, '{"error": "token mismatch"}')) is True


def test_400_validation_is_open():
    assert _gated(_Resp(400, '{"error": {"type": "prompt_outputs_failed_validation"}}')) is False


def test_200_normal_is_open():
    assert _gated(_Resp(200, '{"prompt_id": "abc", "number": 1, "node_errors": {}}')) is False


def test_unreachable_is_not_an_auth_verdict():
    import aiohttp
    assert _gated(exc=aiohttp.ClientError("connection refused")) is False
