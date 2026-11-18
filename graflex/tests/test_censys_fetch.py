"""platform.censys.io now gates the search endpoint behind Fastly TLS-fingerprint
bot management and answers with react-router turbo-stream (Content-Type
text/x-script), not application/json. _fetch_censys must (a) fetch through the
Chrome-impersonating seam _censys_http_get, and (b) treat a text/x-script 200 as
data and parse it -- not reject it as a Cloudflare/challenge non-JSON response.
A real block is still an HTML body or a non-200 and must still stop hard.

The live network path (curl_cffi impersonate="chrome") was verified by hand against
the real endpoint; these tests pin the content-type gate and the parse hand-off with
a faked HTTP layer, so they need no network and no curl_cffi install.
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import graflex  # noqa: E402


class _Resp:
    def __init__(self, status, ctype, text):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.text = text


def _run_fetch(monkeypatch, resp, run_ts):
    monkeypatch.setattr(graflex, "_censys_cookie", lambda: "cookie")
    monkeypatch.setattr(graflex, "_censys_http_get", lambda url, headers: resp)
    return graflex._fetch_censys(False, "ollama", '"ollama is running"', run_ts=run_ts)


def test_fetch_accepts_turbo_stream_and_parses(monkeypatch):
    # The real success shape: HTTP 200, Content-Type text/x-script, a turbo-stream
    # body. It must reach the parser, not the challenge guard.
    run_ts = "test_censys_ok_%d" % os.getpid()
    sentinel = [{"service": "ollama", "host": "9.9.9.9:11434", "site": "censys"}]
    monkeypatch.setattr(graflex, "_parse_censys_json", lambda path, svc: sentinel)
    try:
        got = _run_fetch(monkeypatch,
                         _Resp(200, "text/x-script; charset=utf-8", '[{"_1":2},"ok"]'),
                         run_ts)
        assert got == sentinel
    finally:
        shutil.rmtree(os.path.join("/tmp/graflex", run_ts), ignore_errors=True)


def test_fetch_rejects_html_block(monkeypatch):
    # A block/challenge is an HTML body (or non-200): must stop hard, never parse.
    run_ts = "test_censys_block_%d" % os.getpid()
    monkeypatch.setattr(graflex, "_parse_censys_json",
                        lambda path, svc: pytest.fail("parser must not run on a block"))
    try:
        with pytest.raises(SystemExit):
            _run_fetch(monkeypatch,
                       _Resp(403, "text/html; charset=utf-8", "<!DOCTYPE html>"),
                       run_ts)
    finally:
        shutil.rmtree(os.path.join("/tmp/graflex", run_ts), ignore_errors=True)
