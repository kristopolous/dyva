"""The comma fallback chain is STRICT ORDERED EXHAUSTION, not a pool
(`uv run pytest tests/test_fallback_chain.py -q`).

`qwen3.8*ablit,gemma4*ablit` means: race every reachable qwen host first; only once
that segment is exhausted does gemma get a turn. The comma is NOT "group/pool these and
let reputation pick" — a proven or sticky gemma host must still lose to a thousand cold
qwen hosts, because the user asked for qwen *first*.

find_servers already concatenates the segments in order, but the racer then re-sorted the
whole set by reputation tier (so a good/sticky gemma leapfrogged the cold qwen pool and
won "after only a few"). The fix carries a {host: segment_index} map into the race sort as
its LEADING key, so segment dominates tier; within a segment the normal race key still
orders (good before unknown, warm, size-fit, votes).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# --- the race-key composition: segment leads, tier orders within -----------

def _rk(prio, warm0=1, toobig=0):
    # a bare race sort key (vote/size/fav neutral) at a given tier
    return dyva._race_sort_key(prio, 0, 0, warm0, toobig, 0.0)


def test_segment_leads_over_tier():
    # THE regression: a GOOD host in segment 1 must sort AFTER an UNKNOWN host in
    # segment 0 — qwen (cold, first) beats gemma (proven, second).
    seg = {"q": 0, "g": 1}
    q = dyva._race_key_with_segment("q", _rk(dyva.TIER_UNKNOWN), seg)
    g = dyva._race_key_with_segment("g", _rk(dyva.TIER_GOOD, warm0=0), seg)
    assert q < g


def test_sticky_segment1_still_loses_to_segment0():
    # the sharpest form: the last-used (sticky) host is a gemma; it must NOT be raced
    # before the qwen segment it was asked to fall back FROM.
    seg = {"q": 0, "g": 1}
    q = dyva._race_key_with_segment("q", _rk(dyva.TIER_UNKNOWN), seg)
    sticky_g = dyva._race_key_with_segment("g", _rk(dyva.TIER_LAST, warm0=0), seg)
    assert q < sticky_g


def test_within_segment_tier_still_decides():
    seg = {"a": 0, "b": 0}
    good = dyva._race_key_with_segment("a", _rk(dyva.TIER_GOOD, warm0=0), seg)
    unknown = dyva._race_key_with_segment("b", _rk(dyva.TIER_UNKNOWN), seg)
    assert good < unknown


def test_empty_map_is_noop():
    # no chain (single target) -> every host gets the same leading 0, so ordering is
    # exactly the bare race key: today's behavior is untouched.
    for seg in ({}, None):
        good = dyva._race_key_with_segment("a", _rk(dyva.TIER_GOOD, warm0=0), seg)
        unknown = dyva._race_key_with_segment("b", _rk(dyva.TIER_UNKNOWN), seg)
        assert good < unknown
        assert good[0] == 0 and unknown[0] == 0


# --- find_servers_fallback: the ordered, segment-tagged candidate set ------

def test_non_chain_returns_empty_segment_map(monkeypatch):
    monkeypatch.setattr(dyva, "ALIASES", {})
    monkeypatch.setattr(dyva, "find_servers", lambda sub, caps=None: [(-2, "h1", ["qwen3.8"])])
    servers, seg = dyva.find_servers_fallback("qwen3.8")
    assert servers == [(-2, "h1", ["qwen3.8"])]
    assert seg == {}                       # empty == no fallback constraint == no-op


def test_chain_tags_segments_in_listed_order(monkeypatch):
    pools = {"a": [(0, "ha1", ["a"]), (0, "ha2", ["a"])], "b": [(-2, "hb1", ["b"])]}
    monkeypatch.setattr(dyva, "ALIASES", {})
    monkeypatch.setattr(dyva, "find_servers", lambda sub, caps=None: pools[sub])
    servers, seg = dyva.find_servers_fallback("a,b")
    assert [h for _p, h, _m in servers] == ["ha1", "ha2", "hb1"]
    assert seg == {"ha1": 0, "ha2": 0, "hb1": 1}


def test_host_in_two_segments_keeps_earliest(monkeypatch):
    # a host serving BOTH targets belongs to the first segment (raced in the qwen wave,
    # not re-queued behind it) — deduped to its earliest segment, listed once.
    pools = {"a": [(0, "shared", ["a"]), (0, "ha", ["a"])],
             "b": [(-2, "shared", ["b"]), (-2, "hb", ["b"])]}
    monkeypatch.setattr(dyva, "ALIASES", {})
    monkeypatch.setattr(dyva, "find_servers", lambda sub, caps=None: pools[sub])
    servers, seg = dyva.find_servers_fallback("a,b")
    assert [h for _p, h, _m in servers] == ["shared", "ha", "hb"]
    assert seg["shared"] == 0


def test_alias_expands_before_the_split(monkeypatch):
    # !uncensored -> "a,b": the chain must be seen AFTER expansion, or the whole thing
    # stays one literal segment and never falls back.
    monkeypatch.setattr(dyva, "ALIASES", {"uncensored": "a,b"})
    pools = {"a": [(0, "ha", ["a"])], "b": [(0, "hb", ["b"])]}
    monkeypatch.setattr(dyva, "find_servers", lambda sub, caps=None: pools[sub])
    servers, seg = dyva.find_servers_fallback("!uncensored")
    assert seg == {"ha": 0, "hb": 1}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
