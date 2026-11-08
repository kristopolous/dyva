"""Closed-catalogue bogus signal (`uv run pytest tests/test_closed_catalog.py -q`).

A SELF-HOSTED inference endpoint cannot run closed-weight proprietary models — the weights
(Claude, GPT, o-series, Gemini, Grok) are not publicly downloadable. So a host advertising
them is either a costume/honeypot that parrots the names, or a real host someone polluted
with fake entries; either way "probably junk" -> low-rank. Scored at SURVEY time, additive
per host, reconstitutable from the raw /api/tags or /v1/models payload — folds into the same
`bogus_score` the clone-fingerprint signal stamps. OPEN lookalikes (gpt-oss, gemma) must
NEVER be flagged: they're legitimate self-host catalogues.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import graflex   # noqa: E402


# ---- the family matcher ----------------------------------------------------

def test_flags_closed_families():
    for name in ("claude-opus-5.5", "claude-3-opus-20240229", "gpt-4o", "gpt-6-astra",
                 "gpt-4.1-mini", "o1", "o1:latest", "o3-mini", "gemini-2.5-pro", "grok-2"):
        assert graflex._closed_family(name), name


def test_does_not_flag_open_lookalikes():
    # gpt-oss (Apache-2.0) and gemma (open) are the classic false positives; the rest are
    # genuinely downloadable open weights a real host serves.
    for name in ("gpt-oss:120b-cloud", "gemma2:27b", "gemma4:cloud", "llama3.3:70b",
                 "qwen2.5:72b", "deepseek-r1:671b", "mistral-large", "phi-4:14b",
                 "nous-hermes-2:34b", "openhermes:7b", "command-r-plus"):
        assert not graflex._closed_family(name), name


def test_strips_vendor_prefix():
    # honeypots namespace the costume: "anthropic/claude...", "cc/claude...", "ag/..."
    for name in ("anthropic/claude-opus-5.5", "cc/claude-opus-5.5", "ag/claude-opus-5.5",
                 "cw/claude-opus-5.5"):
        assert graflex._closed_family(name), name
    # an open model behind a vendor prefix stays open
    assert not graflex._closed_family("ninjasatwork/qwen3.8-flash-next")


def test_matcher_tolerates_junk():
    assert graflex._closed_family(None) is False
    assert graflex._closed_family("") is False


# ---- the per-host additive score ------------------------------------------

def test_score_counts_closed_claims_openai_dialect():
    payload = {"object": "list", "data": [
        {"id": "claude-opus-5.5"}, {"id": "gpt-6-astra"}, {"id": "gemini-2.5-pro"},
        {"id": "llama3:8b"}, {"id": "gpt-oss:120b-cloud"}, {"id": "deepseek-v3"}]}
    assert graflex.closed_catalog_score_from_payload(payload) == 3   # only the 3 closed names


def test_score_counts_closed_claims_ollama_dialect():
    payload = {"models": [
        {"name": "claude-3-5-sonnet-latest"}, {"name": "qwen2.5:72b"},
        {"name": "o1:latest"}, {"name": "gemma2:27b"}]}
    assert graflex.closed_catalog_score_from_payload(payload) == 2   # claude + o1


def test_clean_host_scores_zero():
    payload = {"models": [{"name": "llama3.3:70b"}, {"name": "qwen3:72b"},
                          {"name": "mistral:7b"}, {"name": "gpt-oss:120b-cloud"}]}
    assert graflex.closed_catalog_score_from_payload(payload) == 0


def test_non_dict_payload_is_zero():
    assert graflex.closed_catalog_score_from_payload(None) == 0
    assert graflex.closed_catalog_score_from_payload([]) == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
