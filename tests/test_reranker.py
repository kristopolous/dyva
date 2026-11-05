"""Reranker — a temporary, expiring, sqlite-backed reorder layer on top of the reputation
tiers (`uv run pytest tests/test_reranker.py -q`).

Two units under test:

- the STORE: `add_rerank(host, direction, ttl)` appends a signed, expiring vote; `rerank_scores()`
  returns {_norm_host: net} summed over ACTIVE rows only (expire_at > now), net-zero hosts
  dropped, expired rows ignored (never deleted — the table is append-only, no GC).
- the pure REORDER helper `_rerank_prio(prio, net)`: folds a net sum into a tier priority —
  net>0 floats the host above every tier (bigger = further front), net<0 sinks it below every
  tier (more negative = further back), net==0 leaves the earned tier untouched. Lower = earlier.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Point the store at a throwaway db and force a fresh connection.
    monkeypatch.setattr(dyva, "RERANKER_DB", str(tmp_path / "reranker.db"))
    monkeypatch.setattr(dyva, "_reranker_db", None)
    yield
    db = getattr(dyva, "_reranker_db", None)
    if db is not None:
        db.close()
    dyva._reranker_db = None


# ---- store ----------------------------------------------------------------

def test_empty_store_is_empty(store):
    assert dyva.rerank_scores() == {}


def test_single_vote(store):
    dyva.add_rerank("http://h:1", 1, 3600)
    assert dyva.rerank_scores() == {dyva._norm_host("http://h:1"): 1}


def test_votes_net_sum(store):
    # append-only: three active rows on one host sum to their net.
    dyva.add_rerank("http://h:1", 1, 3600)
    dyva.add_rerank("http://h:1", 1, 3600)
    dyva.add_rerank("http://h:1", -1, 3600)
    assert dyva.rerank_scores()[dyva._norm_host("http://h:1")] == 1


def test_net_zero_host_dropped(store):
    dyva.add_rerank("http://h:1", 1, 3600)
    dyva.add_rerank("http://h:1", -1, 3600)
    assert dyva._norm_host("http://h:1") not in dyva.rerank_scores()


def test_expired_rows_ignored(store):
    # a row whose ttl has already passed is not counted (but not deleted either).
    dyva.add_rerank("http://h:1", -1, -5)      # expired 5s ago
    dyva.add_rerank("http://h:1", 1, 3600)     # active
    assert dyva.rerank_scores()[dyva._norm_host("http://h:1")] == 1


def test_two_hosts_independent(store):
    dyva.add_rerank("http://a:1", 1, 3600)
    dyva.add_rerank("http://b:1", -1, 3600)
    s = dyva.rerank_scores()
    assert s[dyva._norm_host("http://a:1")] == 1
    assert s[dyva._norm_host("http://b:1")] == -1


# ---- pure reorder helper --------------------------------------------------

def test_prio_zero_is_untouched():
    assert dyva._rerank_prio(dyva.TIER_GOOD, 0) == dyva.TIER_GOOD
    assert dyva._rerank_prio(dyva.TIER_UNKNOWN, 0) == dyva.TIER_UNKNOWN


def test_positive_floats_above_every_tier():
    # even a bad host, if +1'd, sorts ahead of the plain prefer baseline and every tier.
    assert dyva._rerank_prio(dyva.TIER_BAD, 1) < dyva.TIER_PREFER
    assert dyva._rerank_prio(dyva.TIER_GOOD, 1) < dyva.TIER_GOOD


def test_positive_magnitude_orders_front():
    assert dyva._rerank_prio(dyva.TIER_GOOD, 2) < dyva._rerank_prio(dyva.TIER_GOOD, 1)


def test_negative_sinks_below_every_tier():
    assert dyva._rerank_prio(dyva.TIER_GOOD, -1) > dyva.TIER_BAD


def test_negative_magnitude_orders_back():
    assert dyva._rerank_prio(dyva.TIER_GOOD, -2) > dyva._rerank_prio(dyva.TIER_GOOD, -1)


def test_downvoted_sticky_still_sinks():
    # a warm sticky (TIER_LAST) that gets a -1 must not keep its head-start.
    assert dyva._rerank_prio(dyva.TIER_LAST, -1) > dyva.TIER_BAD


def test_tier_ladder_invariant():
    # _rerank_prio leans on this ordering (prefer ahead of all, bad behind all).
    assert dyva.TIER_PREFER < dyva.TIER_LAST < dyva.TIER_GOOD < dyva.TIER_MAYBE \
        < dyva.TIER_UNKNOWN < dyva.TIER_BAD


# ---- find_servers integration (the reorder actually lands) ----------------
# Replaces the old test_session_skip / test_session_prefer, now that 👎/👍 route through
# the reranker: 👎 sinks (NOT a hard-drop — decision A), 👍 floats, net-zero is untouched.

HA = "http://10.0.0.1:11434"
HB = "http://10.0.0.2:11434"


@pytest.fixture
def routed(store, monkeypatch):
    # Two cold/unknown hosts; keep legacy votes out so only the reranker reorders.
    monkeypatch.setattr(dyva, "_servers_cache", [
        {"server": HA, "models": ["qwen3.8:27b"], "service": "ollama"},
        {"server": HB, "models": ["qwen3.8:27b"], "service": "ollama"},
    ])
    monkeypatch.setattr(dyva, "_load_votes", lambda: {})


def _order(q="qwen3.8"):
    return [h for _p, h, _m in dyva.find_servers(q, ["completion"])]


def test_find_servers_downvote_sinks_to_back(routed):
    assert HA in _order() and HB in _order()
    dyva.add_rerank(HB, -1, 3600)
    order = _order()
    assert HB in order            # NOT dropped — sink, not hard-drop (decision A)
    assert order[-1] == HB        # sunk to the very back


def test_find_servers_upvote_floats_to_front(routed):
    dyva.add_rerank(HB, 1, 3600)
    assert _order()[0] == HB


def test_find_servers_netzero_leaves_in_place(routed):
    before = _order()
    dyva.add_rerank(HA, 1, 3600)
    dyva.add_rerank(HA, -1, 3600)    # +1 -1 = 0 -> no reorder
    assert _order() == before


def _boom(*a, **k):
    raise AssertionError("reputation db touched by the reranker")


def test_add_rerank_never_touches_reputation_db(store, monkeypatch):
    # orthogonal to self-healing reputation: writes go to reranker.db ONLY.
    monkeypatch.setattr(dyva, "_get_db", _boom)
    dyva.add_rerank("http://h:1", -1, 3600)       # must not reach host_status.db
    assert dyva.rerank_scores()[dyva._norm_host("http://h:1")] == -1
