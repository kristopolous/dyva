"""find_servers result cache (LAZY_TIERS fast path) — pytest.

A '*'-storm rebuilds a byte-identical ~12k candidate list every request; find_servers(sub, caps)
is a pure function of (pool, reputation, query), so under the LAZY_TIERS fast path its result is
TTL-cached per (sub, caps). Default (flag off) stays uncached so routing tests see a fresh build
each call. These pin the wrapper: cache hit under the flag, keyed by caps, bypassed when off.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def test_find_servers_caches_under_lazy_flag(monkeypatch):
    monkeypatch.setattr(dyva, "LAZY_TIERS", True)
    n = [0]
    def impl(sub, caps=None):
        n[0] += 1
        return ["built", sub]
    monkeypatch.setattr(dyva, "_find_servers_impl", impl)
    dyva._FIND_CACHE.clear()

    a = dyva.find_servers("q", None)
    b = dyva.find_servers("q", None)
    assert a is b                                  # same object: served from cache
    assert n[0] == 1                               # impl built it once, not twice
    dyva.find_servers("q", frozenset({"vision"}))  # different caps -> different key
    assert n[0] == 2


def test_find_servers_uncached_when_flag_off(monkeypatch):
    monkeypatch.setattr(dyva, "LAZY_TIERS", False)
    n = [0]
    def impl(sub, caps=None):
        n[0] += 1
        return ["x"]
    monkeypatch.setattr(dyva, "_find_servers_impl", impl)

    a = dyva.find_servers("q", None)
    b = dyva.find_servers("q", None)
    assert a is not b                              # fresh build each call
    assert n[0] == 2
