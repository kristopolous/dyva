"""www.zoomeye.ai fronts its /api/search endpoint with TLS-fingerprint bot
management, so _fetch_zoomeye must fetch through the Chrome-impersonating seam
_zoomeye_http_get (curl_cffi impersonate="chrome") rather than a plain
requests.get. Same treatment as _fetch_censys, with one difference: the seam
KEEPS the caller's User-Agent, because ZoomEye's clearance is UA-bound.

The live network path (curl_cffi) was verified by hand against the real
endpoint; these tests pin that the fetch goes through the seam and that the
non-200 / API-error / success hand-offs still hold, with a faked HTTP layer, so
they need no network and no curl_cffi install.
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import graflex  # noqa: E402


class _Resp:
    def __init__(self, status, text, payload=None):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _run_fetch(monkeypatch, resp, run_ts, seen=None):
    monkeypatch.setattr(graflex, "_zoomeye_cookie", lambda: "cookie")
    # Record that the fetch went through the impersonating seam (and not plain requests).
    def _seam(url, headers, params):
        if seen is not None:
            seen.append((url, headers, params))
        return resp
    monkeypatch.setattr(graflex, "_zoomeye_http_get", _seam)
    return graflex._fetch_zoomeye(False, "ollama", 'app="ollama"', run_ts=run_ts)


def test_fetch_goes_through_impersonating_seam_and_returns_data(monkeypatch):
    # The real success shape: HTTP 200, JSON body, no error status. It must come back
    # as the parsed data dict — and it must have been fetched through _zoomeye_http_get.
    run_ts = "test_zoomeye_ok_%d" % os.getpid()
    payload = {"total": 1, "matches": [{"ip": "9.9.9.9", "portinfo": {"port": 11434}}]}
    seen = []
    try:
        got = _run_fetch(monkeypatch, _Resp(200, "{}", payload), run_ts, seen=seen)
        assert got == payload
        assert len(seen) == 1                       # fetched through the seam, once
        assert "User-Agent" in seen[0][1]           # UA kept (UA-bound clearance), unlike censys
    finally:
        shutil.rmtree(os.path.join("/tmp/graflex", run_ts), ignore_errors=True)


def test_fetch_non200_stops_soft(monkeypatch):
    # A block/challenge/other non-200 is a SOFT stop for zoomeye: return None, never raise.
    run_ts = "test_zoomeye_block_%d" % os.getpid()
    try:
        got = _run_fetch(monkeypatch, _Resp(403, "<!DOCTYPE html>"), run_ts)
        assert got is None
    finally:
        shutil.rmtree(os.path.join("/tmp/graflex", run_ts), ignore_errors=True)


def test_fetch_out_of_credits_aborts_run(monkeypatch):
    # HTTP 200 but an API-level 402 in the body = out of points: abort the whole run.
    run_ts = "test_zoomeye_402_%d" % os.getpid()
    try:
        with pytest.raises(SystemExit):
            _run_fetch(monkeypatch, _Resp(200, "{}", {"status": 402, "message": "no points"}), run_ts)
    finally:
        shutil.rmtree(os.path.join("/tmp/graflex", run_ts), ignore_errors=True)
