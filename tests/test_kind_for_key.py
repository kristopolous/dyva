"""_kind_for_key classification — pytest (`uv run pytest tests/test_kind_for_key.py -q`).

The worker/job `kind` (text / image / speech / video / music / edit / decision) is derived
from the race key = model name. Decision models (the /v1/systemone family: clef/clef-flash,
laya, nimble, kev, tev1, jev) ran as `kind="text"`, so the front-end lumped them in with chat.
They get their own `decision` kind. The match is a BOUNDED codename match (same shape as the
dashboard's _KINDS regex) so look-alikes (`nimblefoot`, `clever`, probe trap names) don't catch.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def test_decision_codenames_are_decision():
    for k in ("nimble-v3", "clef-flash", "clef", "laya", "tev1", "jev", "kev",
              "nimble-v3:latest", "jev-27b-vl"):
        assert dyva._kind_for_key(k) == "decision", k


def test_plain_chat_models_are_text():
    for k in ("qwen3:8b", "llama3.1:70b", "gemma2", "mistral-small", "phi4"):
        assert dyva._kind_for_key(k) == "text", k


def test_bounded_match_skips_lookalikes():
    # The codename must be delimited: a longer word that merely starts with it is NOT decision.
    for k in ("nimblefoot-7b", "clever-13b", "probe_zz_tev", "jevon"):
        assert dyva._kind_for_key(k) == "text", k


def test_other_media_kinds_unaffected():
    assert dyva._kind_for_key("image") == "image"
    assert dyva._kind_for_key("tts-xtts") == "speech"
    assert dyva._kind_for_key("video") == "video"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
