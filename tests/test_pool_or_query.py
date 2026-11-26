"""The '|' model-query operator POOLS alternatives into one candidate set, assessed by
the existing reputation/priority — distinct from ',', which exhausts one target before the
next (`uv run pytest tests/test_pool_or_query.py -q`).

`qwen3.8|gemma4` gathers every qwen OR gemma host into a single pool (deduped, model lists
merged) and lets the normal race key order them — there is no "first". `qwen3.8,gemma4`
still means try all qwen, THEN gemma. '|' binds tighter than ',': `a|b,c` = pool(a,b),
then fall back to c. The loaders are stubbed; no DB, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _setup(monkeypatch, hosts, good=None):
    monkeypatch.setattr(dyva, "load_servers", lambda: hosts)
    monkeypatch.setattr(dyva, "load_good", lambda: set(good or []))
    monkeypatch.setattr(dyva, "load_bad", lambda: set())
    monkeypatch.setattr(dyva, "load_maybe", lambda: set())
    monkeypatch.setattr(dyva, "load_unreachable", lambda: set())
    monkeypatch.setattr(dyva, "load_incompatible", lambda: set())
    monkeypatch.setattr(dyva, "_load_votes", lambda: {})
    monkeypatch.setattr(dyva, "get_last", lambda sub: None)
    monkeypatch.setattr(dyva, "ALIASES", {})
    monkeypatch.setattr(dyva, "EXPLORE_MODE", False)


# hA serves model mA, hB serves mB, hAB serves both, hC serves mC.
_HOSTS = [
    {"server": "hA", "models": ["mA"], "service": "ollama", "checked": "2026-10-10T09:00:00"},
    {"server": "hB", "models": ["mB"], "service": "ollama", "checked": "2026-10-10T09:00:00"},
    {"server": "hAB", "models": ["mA", "mB"], "service": "ollama", "checked": "2026-10-10T09:00:00"},
    {"server": "hC", "models": ["mC"], "service": "ollama", "checked": "2026-10-10T09:00:00"},
]


def _hosts(result):
    return {h for _p, h, _m in result}


# --- the pooled union -------------------------------------------------------

def test_pipe_unions_the_matching_hosts(monkeypatch):
    _setup(monkeypatch, _HOSTS)
    assert _hosts(dyva.find_servers("mA|mB")) == {"hA", "hB", "hAB"}   # not hC


def test_pipe_dedups_a_host_matching_both_sides(monkeypatch):
    _setup(monkeypatch, _HOSTS)
    out = dyva.find_servers("mA|mB")
    assert [h for _p, h, _m in out].count("hAB") == 1


def test_pipe_merges_matched_model_lists_on_a_shared_host(monkeypatch):
    _setup(monkeypatch, _HOSTS)
    out = dyva.find_servers("mA|mB")
    hAB_ms = next(ms for _p, h, ms in out if h == "hAB")
    assert set(hAB_ms) == {"mA", "mB"}        # can serve either matched model


def test_single_side_is_unchanged(monkeypatch):
    _setup(monkeypatch, _HOSTS)
    assert _hosts(dyva.find_servers("mA")) == {"hA", "hAB"}


# --- fallback-chain integration: segment tagging ---------------------------

def test_pipe_has_no_segment_constraint(monkeypatch):
    # a pure pool is ONE candidate set assessed by reputation -> empty segment map (the
    # no-op signal the race reads as 'order by reputation'), exactly like a single target.
    _setup(monkeypatch, _HOSTS)
    servers, seg = dyva.find_servers_fallback("mA|mB")
    assert _hosts(servers) == {"hA", "hB", "hAB"}
    assert seg == {}


def test_comma_still_sequences_segments(monkeypatch):
    _setup(monkeypatch, _HOSTS)
    _servers, seg = dyva.find_servers_fallback("mA,mB")
    # mA hosts -> segment 0, mB-only host -> segment 1 (shared host keeps its earliest)
    assert seg["hA"] == 0 and seg["hAB"] == 0
    assert seg["hB"] == 1


def test_pipe_binds_tighter_than_comma(monkeypatch):
    # a|b,c  ==  pool(a,b) first (segment 0), THEN c (segment 1)
    _setup(monkeypatch, _HOSTS)
    _servers, seg = dyva.find_servers_fallback("mA|mB,mC")
    assert seg["hA"] == 0 and seg["hB"] == 0 and seg["hAB"] == 0
    assert seg["hC"] == 1


# --- media matcher (model_query_match / _match_rank) treats '|' as OR -------

def test_model_query_match_pipe_matches_any_alternative():
    q = "z*image|flux"
    assert dyva.model_query_match("z-image-turbo.safetensors", q) is True
    assert dyva.model_query_match("flux1-dev.safetensors", q) is True
    assert dyva.model_query_match("sd_xl_base.safetensors", q) is False


def test_match_rank_handles_pipe_alternatives():
    q = "z*image|flux"
    cands = ["flux1-dev.safetensors", "z-image-turbo.safetensors"]
    ranked = sorted(cands, key=dyva._match_rank(q))
    assert ranked == ["z-image-turbo.safetensors", "flux1-dev.safetensors"]
