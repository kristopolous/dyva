"""SOCKS/proxy egress wiring (`uv run pytest tests/test_socks.py -q`).

dyva sends ALL upstream traffic — inference, probes, the hedged race, embeddings, web
read/search, link-unfurl — through ONE shared aiohttp connector. With a proxy
configured, that connector becomes a SOCKS/HTTP ProxyConnector with rdns=True so DNS
resolves AT the proxy (the ~2k hostname hosts must not resolve locally, or we leak the
very egress the proxy was for). Config precedence: the --socks arg, then $DYVA_SOCKS,
then $SOCKS_PROXY; a malformed value must fail LOUD, never fall through to a direct
(leaking) connection.
"""
import asyncio
import os
import sys

import aiohttp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ---- _resolve_socks: precedence + validation -------------------------------

def test_resolve_none_when_unset():
    assert dyva._resolve_socks(None, {}) is None
    assert dyva._resolve_socks("", {}) is None


def test_resolve_cli_beats_env():
    assert dyva._resolve_socks("socks5://a:1", {"DYVA_SOCKS": "socks5://b:2",
                                                "SOCKS_PROXY": "socks5://c:3"}) == "socks5://a:1"


def test_resolve_dyva_env_when_no_cli():
    assert dyva._resolve_socks(None, {"DYVA_SOCKS": "socks5h://b:2"}) == "socks5h://b:2"


def test_resolve_socks_proxy_env_is_lowest():
    assert dyva._resolve_socks(None, {"SOCKS_PROXY": "socks5://c:3"}) == "socks5://c:3"
    assert dyva._resolve_socks(None, {"DYVA_SOCKS": "socks5://b:2",
                                      "SOCKS_PROXY": "socks5://c:3"}) == "socks5://b:2"


def test_resolve_strips_whitespace():
    assert dyva._resolve_socks("  socks5://a:1  ", {}) == "socks5://a:1"


def test_resolve_accepts_known_schemes():
    # curl-style socks5h/socks4a are accepted (normalized for the backend in _make_connector).
    for u in ("socks5://a:1", "socks5h://a:1", "socks4://a:1", "socks4a://a:1", "http://a:1"):
        assert dyva._resolve_socks(u, {}) == u


def test_resolve_rejects_bad_scheme():
    # A typo must fail LOUD, not silently fall through to a direct connection that would
    # leak the egress the proxy was meant to hide.
    with pytest.raises(ValueError):
        dyva._resolve_socks("ftp://a:1", {})
    with pytest.raises(ValueError):
        dyva._resolve_socks("127.0.0.1:1080", {})   # no scheme at all
    with pytest.raises(ValueError):
        dyva._resolve_socks("https://a:1", {})      # TLS-to-proxy unsupported by the backend


# ---- _make_connector: the type branch --------------------------------------

def _build_connector_class():
    """Build the connector _make_connector() returns, close it, hand back its class.
    Construction opens no socket; it just picks TCPConnector vs ProxyConnector."""
    async def go():
        c = dyva._make_connector()
        try:
            return type(c)
        finally:
            await c.close()
    return asyncio.run(go())


def test_make_connector_direct_when_unset(monkeypatch):
    monkeypatch.setattr(dyva, "SOCKS_PROXY", None)
    # EXACT type — a plain direct TCPConnector, not a Proxy subclass of it.
    assert _build_connector_class() is aiohttp.TCPConnector


def test_make_connector_proxy_when_set(monkeypatch):
    aiohttp_socks = pytest.importorskip("aiohttp_socks")
    monkeypatch.setattr(dyva, "SOCKS_PROXY", "socks5h://127.0.0.1:1080")
    assert issubclass(_build_connector_class(), aiohttp_socks.ProxyConnector)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
