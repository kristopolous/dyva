"""A manual 👎 (deprioritize) must push a host to the BACK of routing, not just reorder
it within its tier — pytest (`uv run pytest dyva/test_deprioritize_routing.py`).

The bug: a down-voted host that was the sticky (last-good) stayed TIER_LAST and was tried
FIRST every time, because the vote only broke ties WITHIN a tier. These pin the fix: a
down-vote strips the sticky slot and forces the host to the last-resort tier, so in a pool
of alternatives it isn't raced early. No DB is touched — the loaders are stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _setup(monkeypatch, votes):
    hosts = [{"server": h, "models": ["mdl"], "service": "ollama",
              "checked": "2026-10-10T09:00:00"} for h in ("h1", "h2", "h3")]
    monkeypatch.setattr(dyva, "load_servers", lambda: hosts)
    monkeypatch.setattr(dyva, "load_good", lambda: {"h1 mdl", "h2 mdl", "h3 mdl"})
    monkeypatch.setattr(dyva, "load_bad", lambda: set())
    monkeypatch.setattr(dyva, "load_maybe", lambda: set())
    monkeypatch.setattr(dyva, "load_unreachable", lambda: set())
    monkeypatch.setattr(dyva, "load_incompatible", lambda: set())
    monkeypatch.setattr(dyva, "_load_votes", lambda: dict(votes))
    monkeypatch.setattr(dyva, "get_last", lambda sub: ("h1", "mdl"))  # h1 is the sticky
    monkeypatch.setattr(dyva, "EXPLORE_MODE", False)


def test_sticky_host_is_first_without_a_vote(monkeypatch):
    _setup(monkeypatch, {})
    order = [h for _, h, _ in dyva.find_servers("mdl")]
    assert order[0] == "h1"           # the warm/sticky host leads, as designed


def test_downvote_pushes_host_to_the_back(monkeypatch):
    _setup(monkeypatch, {("h1", "mdl"): -1})
    order = [h for _, h, _ in dyva.find_servers("mdl")]
    assert order[-1] == "h1"          # last resort now, not first
    assert order[0] in ("h2", "h3")   # some other host leads


def test_downvoted_host_still_a_candidate(monkeypatch):
    # Deprioritize is NOT exclude: if it's the only option it's still returned.
    monkeypatch.setattr(dyva, "load_servers",
                        lambda: [{"server": "h1", "models": ["mdl"], "service": "ollama"}])
    monkeypatch.setattr(dyva, "load_good", lambda: {"h1 mdl"})
    monkeypatch.setattr(dyva, "load_bad", lambda: set())
    monkeypatch.setattr(dyva, "load_maybe", lambda: set())
    monkeypatch.setattr(dyva, "load_unreachable", lambda: set())
    monkeypatch.setattr(dyva, "load_incompatible", lambda: set())
    monkeypatch.setattr(dyva, "_load_votes", lambda: {("h1", "mdl"): -1})
    monkeypatch.setattr(dyva, "get_last", lambda sub: ("h1", "mdl"))
    monkeypatch.setattr(dyva, "EXPLORE_MODE", False)
    order = [h for _, h, _ in dyva.find_servers("mdl")]
    assert order == ["h1"]
