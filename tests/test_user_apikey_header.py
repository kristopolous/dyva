"""Read the client api-key across dialects → userhash
(`uv run pytest tests/test_user_apikey_header.py -q`).

Confirmed with `llcat --curlify` (2026-11-22): where the api-key rides depends on the
protocol the client speaks to dyva —
  - openai / ollama / llama.cpp:  Authorization: Bearer <key>
  - anthropic:                    x-api-key: <key>        (no 'Bearer')
Some clients also send a bare `Authorization: <key>` with no scheme. `_apikey_from_request`
normalises all of these to the raw `k=v;` string; `_userhash_from_request` is the whole chain
the capture hook calls (None = anonymous / blank, so nothing is stored).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

from multidict import CIMultiDict   # noqa: E402  (aiohttp's case-insensitive headers)


class _Req:
    def __init__(self, **headers):
        self.headers = CIMultiDict(headers)


# ---- extracting the raw key across dialects --------------------------------

def test_bearer_token_is_stripped():
    assert dyva._apikey_from_request(_Req(Authorization="Bearer user=chris")) == "user=chris"


def test_bearer_scheme_is_case_insensitive():
    assert dyva._apikey_from_request(_Req(Authorization="bearer user=chris")) == "user=chris"


def test_bare_authorization_is_taken_whole():
    # a client that sends the key with no 'Bearer ' scheme
    assert dyva._apikey_from_request(_Req(Authorization="user=chris;pass=x")) == "user=chris;pass=x"


def test_anthropic_x_api_key():
    assert dyva._apikey_from_request(
        _Req(**{"x-api-key": "user=chris;pass=x"})) == "user=chris;pass=x"


def test_header_names_are_case_insensitive():
    # aiohttp headers are case-insensitive; the reader must not care about header casing.
    assert dyva._apikey_from_request(_Req(**{"X-API-Key": "user=bob"})) == "user=bob"


def test_missing_key_is_empty_string():
    assert dyva._apikey_from_request(_Req()) == ""


# ---- the whole chain the capture hook calls --------------------------------

def test_userhash_from_request_matches_bag_chain():
    r = _Req(Authorization="Bearer user=chris;pass=hunter2")
    assert (dyva._userhash_from_request(r)
            == dyva._userhash_from_bag({"user": "chris", "pass": "hunter2"}))


def test_anonymous_request_has_no_userhash():
    assert dyva._userhash_from_request(_Req()) is None
    assert dyva._userhash_from_request(_Req(Authorization="Bearer ")) is None   # blank after scheme


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
