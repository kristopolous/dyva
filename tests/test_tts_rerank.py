"""A 👎/👍 on a TTS host must actually reorder TTS routing
(`uv run pytest tests/test_tts_rerank.py -q`).

Thumbs-down on a finished TTS worker calls add_rerank(host, -1) — the same temporary,
expiring "sink to the back" vote the text path uses, folded into find_servers via
_rerank_prio. But _find_tts_hosts ranked hosts purely by (capability_tier, within) and
NEVER consulted rerank_scores(): the vote was written and then ignored, so the very same
host sorted first again on the next request. That is the bug — "thumbs downing a tts host
doesn't work." These tests pin that TTS ranking folds the reranker in, exactly like text.

All state is monkeypatched (servers, marks, surveys, reranker) — no DB, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

HOSTS = ["h1", "h2", "h3"]


def _setup(monkeypatch, rr):
    servers = [{"server": h, "service": "comfyui", "models": []} for h in HOSTS]
    monkeypatch.setattr(dyva, "load_servers", lambda: servers)
    tkey = dyva.tts_key(None)
    good = {f"{h} {tkey}" for h in HOSTS}      # every host equally 'good' for tts
    monkeypatch.setattr(dyva, "load_good", lambda: good)
    monkeypatch.setattr(dyva, "load_maybe", lambda: set())
    monkeypatch.setattr(dyva, "load_bad", lambda: set())
    monkeypatch.setattr(dyva, "load_unreachable", lambda: set())
    monkeypatch.setattr(dyva, "get_last", lambda k: None)          # no sticky
    monkeypatch.setattr(dyva, "load_node_survey", lambda key: {})  # nothing surveyed
    monkeypatch.setattr(dyva, "_TTS_NODE_CACHE", {}, raising=False)
    monkeypatch.setattr(dyva, "_inflight", {}, raising=False)      # all idle, stable order
    monkeypatch.setattr(dyva, "_workers", {}, raising=False)
    monkeypatch.setattr(dyva, "rerank_scores", lambda: dict(rr))


def test_no_votes_keeps_the_survey_order(monkeypatch):
    _setup(monkeypatch, {})
    assert dyva._find_tts_hosts() == ["h1", "h2", "h3"]


def test_downvote_sinks_the_tts_host_to_the_back(monkeypatch):
    _setup(monkeypatch, {dyva._norm_host("h1"): -1})
    out = dyva._find_tts_hosts()
    assert out[-1] == "h1"                     # 👎 -> last, no longer first
    assert set(out) == set(HOSTS)              # still a candidate, not hard-dropped


def test_upvote_floats_the_tts_host_to_the_front(monkeypatch):
    _setup(monkeypatch, {dyva._norm_host("h3"): 1})
    assert dyva._find_tts_hosts()[0] == "h3"   # 👍 -> first


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
