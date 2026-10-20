"""Context-window auto-sizing suite — pytest (`uv run pytest tests/test_context_window.py`).

Pins the "Context window" contract from dyva/README.md: dyva auto-sizes num_ctx per
native request from the prompt, snaps to a bucket, clamps to [MIN, 2**20], and ALWAYS
honours an explicit options.num_ctx. A response whose prompt filled the window is a
truncation (our sizing fell short) — a failure to re-send bigger, never the host's
fault, so it must be detectable without touching reputation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

SMALL = {"messages": [{"role": "user", "content": "hi"}]}


# ---- _auto_num_ctx: sizing + clamp ----------------------------------------

def test_auto_small_prompt_is_at_least_min_and_bucketed():
    n = dyva._auto_num_ctx(SMALL)
    assert n >= dyva.NUM_CTX_MIN
    assert n % dyva.NUM_CTX_STEP == 0        # snapped to the bucket


def test_auto_clamps_to_max_ceiling(monkeypatch):
    # A prompt estimate past the 2**20 ceiling is capped there (host sets the real
    # limit; this is only a sanity bound).
    monkeypatch.setattr(dyva, "_estimate_prompt_tokens", lambda p: 2_000_000)
    assert dyva._auto_num_ctx(SMALL) == dyva.NUM_CTX_MAX
    assert dyva.NUM_CTX_MAX == 2 ** 20


def test_auto_grows_with_prompt_size(monkeypatch):
    monkeypatch.setattr(dyva, "_estimate_prompt_tokens", lambda p: 100_000)
    big = dyva._auto_num_ctx(SMALL)
    monkeypatch.setattr(dyva, "_estimate_prompt_tokens", lambda p: 1_000)
    small = dyva._auto_num_ctx(SMALL)
    assert big > small


# ---- explicit num_ctx always wins -----------------------------------------

def test_with_num_ctx_honours_an_explicit_pin():
    pinned = {"messages": [], "options": {"num_ctx": 123}}
    assert dyva._with_num_ctx(pinned)["options"]["num_ctx"] == 123   # unchanged


def test_with_num_ctx_adds_one_when_absent():
    out = dyva._with_num_ctx(SMALL)
    assert out["options"]["num_ctx"] == dyva._auto_num_ctx(SMALL)


def test_effective_num_ctx_is_pin_else_estimate():
    assert dyva._effective_num_ctx({"messages": [], "options": {"num_ctx": 777}}) == 777
    assert dyva._effective_num_ctx(SMALL) == dyva._auto_num_ctx(SMALL)


# ---- truncation detection (sizing fell short, NOT a host fault) -----------

def test_prompt_truncated_only_when_it_fills_the_window():
    assert dyva._prompt_truncated(4096, 4096) is True     # reached num_ctx -> head dropped
    assert dyva._prompt_truncated(4097, 4096) is True
    assert dyva._prompt_truncated(4000, 4096) is False    # fit, reply just had less room


def test_prompt_truncated_guards_none_and_zero():
    assert dyva._prompt_truncated(None, 4096) is False
    assert dyva._prompt_truncated(4096, 0) is False
    assert dyva._prompt_truncated(4096, None) is False


def test_is_truncated_reads_either_usage_shape():
    assert dyva._is_truncated({"prompt_eval_count": 4096}, 4096) is True       # ollama
    assert dyva._is_truncated({"usage": {"prompt_tokens": 4096}}, 4096) is True  # openai
    assert dyva._is_truncated({"prompt_eval_count": 10}, 4096) is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
