"""👎 = "do not go back to this host" — pytest.

A thumbs-down is a SESSION-scoped, non-persistent, host-wide skip: find_servers
drops the host outright (not a demote, not a bad/honeypot mark, nothing written to
the DB). A restart or a 👍 clears it. This is the fix for the old -1 vote, which the
sticky top-slot outranked so the host came right back.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


def _pool(monkeypatch):
    monkeypatch.setattr(dyva, "_servers_cache", [
        {"server": "http://89.21.94.208:8001", "models": ["HivenetQuant/Qwen3.8-27B"], "service": "vllm"},
        {"server": "http://1.2.3.4:11434", "models": ["qwen3.8:27b"], "service": "ollama"},
    ])
    monkeypatch.setattr(dyva, "_session_skip_hosts", set())


def _hosts(q="qwen3.8"):
    return [h for _p, h, _ms in dyva.find_servers(q, ["completion"])]


def test_downvote_removes_host_from_routing(monkeypatch):
    _pool(monkeypatch)
    assert "http://89.21.94.208:8001" in _hosts()
    dyva._session_skip_hosts.add(dyva._norm_host("http://89.21.94.208:8001"))
    assert "http://89.21.94.208:8001" not in _hosts()      # hard-dropped, not demoted
    assert "http://1.2.3.4:11434" in _hosts()               # others unaffected


def test_skip_is_scheme_agnostic(monkeypatch):
    # the old vote missed on exact string match; the skip normalizes host form
    _pool(monkeypatch)
    dyva._session_skip_hosts.add("89.21.94.208:8001")        # stored scheme-stripped
    assert "http://89.21.94.208:8001" not in _hosts()        # still matched despite scheme


def test_upvote_discard_restores(monkeypatch):
    _pool(monkeypatch)
    dyva._session_skip_hosts.add(dyva._norm_host("http://89.21.94.208:8001"))
    assert "http://89.21.94.208:8001" not in _hosts()
    dyva._session_skip_hosts.discard(dyva._norm_host("http://89.21.94.208:8001"))
    assert "http://89.21.94.208:8001" in _hosts()            # 👍 undoes it


def test_skip_writes_nothing_to_reputation(monkeypatch):
    # it's purely in-memory: no bad/honeypot/vote state touched
    _pool(monkeypatch)
    before = set(dyva._session_skip_hosts)
    dyva._session_skip_hosts.add(dyva._norm_host("http://89.21.94.208:8001"))
    assert dyva._session_skip_hosts - before == {"89.21.94.208:8001"}
