"""_post_follow flips http->https when a host 400s as a TLS listener — pytest
(`uv run pytest tests/test_scheme_flip.py -q`).

Seen live: `status 400: Client sent an HTTP request to an HTTPS server.` The host is a
TLS listener recorded (or discovered) as http://; instead of a 301-to-https (which
_post_follow already follows) it 400s with that message. We treat it the same as the
redirect: flip the scheme and retry once. A 400 that is NOT a scheme error is returned
unchanged, and its body stays readable by the caller (aiohttp read() caches). A host
already on https is never re-flipped (no loop).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.released = False

    async def read(self):
        return self._body        # aiohttp caches _body; repeated reads return the same bytes

    async def release(self):
        self.released = True

    def close(self):
        pass


class _Session:
    def __init__(self, by_scheme):
        self.by_scheme = by_scheme     # {"http": resp, "https": resp}
        self.posts = []

    def post(self, url, json=None, timeout=None, allow_redirects=False):
        self.posts.append(url)
        scheme = url.split("://", 1)[0]

        async def _c():
            return self.by_scheme[scheme]
        return _c()


def _run(sess, url):
    return asyncio.run(dyva._post_follow(sess, url, json={}, timeout=1))


def test_http_flips_to_https_on_scheme_400():
    http400 = _Resp(400, b"400 Bad Request\nClient sent an HTTP request to an HTTPS server.")
    https200 = _Resp(200, b'{"ok":1}')
    sess = _Session({"http": http400, "https": https200})
    resp = _run(sess, "http://h:8080/api/chat")
    assert resp is https200                                           # the https answer is returned
    assert sess.posts == ["http://h:8080/api/chat", "https://h:8080/api/chat"]
    assert http400.released


def test_nginx_plain_http_phrasing_also_flips():
    http400 = _Resp(400, b"The plain HTTP request was sent to HTTPS port")
    https200 = _Resp(200, b"{}")
    sess = _Session({"http": http400, "https": https200})
    resp = _run(sess, "http://h:443/v1/chat/completions")
    assert resp is https200


def test_real_400_is_not_flipped_and_body_survives():
    http400 = _Resp(400, b'{"error":"exceeds the available context"}')
    sess = _Session({"http": http400})
    resp = _run(sess, "http://h:11434/api/chat")
    assert resp is http400                                            # unchanged
    assert sess.posts == ["http://h:11434/api/chat"]                  # no https retry
    assert asyncio.run(resp.read()) == b'{"error":"exceeds the available context"}'


def test_https_host_is_not_reflipped():
    https400 = _Resp(400, b"Client sent an HTTP request to an HTTPS server.")
    sess = _Session({"https": https400})
    resp = _run(sess, "https://h:8080/api/chat")
    assert resp is https400
    assert sess.posts == ["https://h:8080/api/chat"]                  # already https, no loop
