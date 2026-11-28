"""_active_hosts(): the set form of is_active(), for hoisting the per-candidate busy check
out of the _race_servers load-aware sort — pytest.

The load-aware sort (`sorted(servers, key=_order_key)`) called is_active(host) for EVERY
candidate, and is_active rescans the whole _workers registry each call: with a whole-pool
query that's candidates x workers per request, which pegged the event loop. _active_hosts()
builds the active-host set once so the sort can test membership in O(1). This pins the
equivalence that makes the hoist behavior-preserving: `host in _active_hosts(e)` must equal
`is_active(host, e)` for every host.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

WORKERS = {
    "w1": {"wid": "w1", "host": "a", "done": False},   # active
    "w2": {"wid": "w2", "host": "b", "done": True},     # finished -> not active
    "w3": {"wid": "w3", "host": "c", "done": False},    # active
    "w4": {"wid": "w4", "host": None, "done": False},   # no host -> ignored
    "w5": {"wid": "w5", "host": "a", "done": True},     # a also has a finished worker
}


def test_active_hosts_set(monkeypatch):
    monkeypatch.setattr(dyva, "_workers", WORKERS)
    assert dyva._active_hosts() == {"a", "c"}


def test_active_hosts_excludes_wid(monkeypatch):
    monkeypatch.setattr(dyva, "_workers", WORKERS)
    # excluding w1 (the only live worker on 'a') drops 'a' from the active set
    assert dyva._active_hosts(except_wid="w1") == {"c"}


def test_active_hosts_matches_is_active(monkeypatch):
    monkeypatch.setattr(dyva, "_workers", WORKERS)
    for h in ("a", "b", "c", "x", None):
        assert (bool(h) and h in dyva._active_hosts()) == dyva.is_active(h)
        assert (bool(h) and h in dyva._active_hosts(except_wid="w1")) == \
            dyva.is_active(h, except_wid="w1")
