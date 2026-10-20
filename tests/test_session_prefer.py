"""👍 = "route here first" — pytest.

The mirror of the 👎 session skip: a thumbs-up adds the host to a SESSION-scoped,
non-persistent, host-wide PREFERRED set, and find_servers floats it to TIER_PREFER —
above every tier, even the warm sticky — so it's raced first. A restart or a 👎 clears
it. Before this, 👍 only gave a one-time reputation boost and never actually put the
host first, which is why it "did nothing" from the operator's seat.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


def _pool(monkeypatch):
    monkeypatch.setattr(dyva, "_servers_cache", [
        {"server": "http://1.1.1.1:11434", "models": ["qwen3.8:27b"], "service": "ollama"},
        {"server": "http://2.2.2.2:11434", "models": ["qwen3.8:27b"], "service": "ollama"},
    ])
    monkeypatch.setattr(dyva, "_session_skip_hosts", set())
    monkeypatch.setattr(dyva, "_session_prefer_hosts", set())


def _ranked(q="qwen3.8"):
    return dyva.find_servers(q, ["completion"])   # [(prio, host, ms), ...], already sorted


def test_prefer_floats_host_to_top(monkeypatch):
    _pool(monkeypatch)
    before = [h for _p, h, _m in _ranked()]
    assert before[0] == "http://1.1.1.1:11434"            # baseline order
    dyva._session_prefer_hosts.add(dyva._norm_host("http://2.2.2.2:11434"))
    ranked = _ranked()
    assert ranked[0][1] == "http://2.2.2.2:11434"         # 👍'd host is now first
    assert ranked[0][0] == dyva.TIER_PREFER               # ...at the top tier
    # the other host is still present, just behind
    assert "http://1.1.1.1:11434" in [h for _p, h, _m in ranked]


def test_prefer_tier_beats_every_other_tier():
    # the ordering guarantee this whole feature rests on
    assert dyva.TIER_PREFER < dyva.TIER_LAST < dyva.TIER_GOOD < dyva.TIER_MAYBE \
        < dyva.TIER_UNKNOWN < dyva.TIER_BAD


def test_prefer_is_scheme_agnostic(monkeypatch):
    _pool(monkeypatch)
    dyva._session_prefer_hosts.add("2.2.2.2:11434")       # stored scheme-stripped
    ranked = _ranked()
    assert ranked[0][1] == "http://2.2.2.2:11434" and ranked[0][0] == dyva.TIER_PREFER


def test_skip_wins_over_prefer_if_both(monkeypatch):
    # the handlers keep the sets mutually exclusive, but defensively: a host in both is
    # DROPPED (skip is checked first and short-circuits before the prefer float).
    _pool(monkeypatch)
    h = dyva._norm_host("http://2.2.2.2:11434")
    dyva._session_prefer_hosts.add(h)
    dyva._session_skip_hosts.add(h)
    assert "http://2.2.2.2:11434" not in [x[1] for x in _ranked()]
