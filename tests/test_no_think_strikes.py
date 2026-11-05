"""no-think stickiness needs more than one empty answer — pytest
(`uv run pytest tests/test_no_think_strikes.py -q`).

A `status == "empty"` leg (dyva/__init__.py `_pump_stream`) means the host sent a
clean terminal `done` chunk but delivered no content. The code treats that as the
thinking-channel bug and sends `think:false` forever after for that (host, model).
But an empty-with-a-done is ALSO what a host that choked / OOM'd mid-answer emits —
indistinguishable at that point — so a SINGLE empty must not permanently flip a
thinking-capable pair to no_think runtime-wide. We retry on every empty (cheap), but
only PROACTIVELY suppress thinking once a pair has gone empty NO_THINK_STRIKES times,
and a real (non-empty) answer in between clears the tally (proof it was a transient).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

H, M = "http://host:11434", "qwen3:27b"


def _reset():
    dyva._session_empty_strikes.clear()


def test_one_empty_does_not_suppress():
    _reset()
    assert dyva._note_empty_answer(H, M) == 1
    assert dyva._should_suppress_think(H, M) is False


def test_two_empties_suppress():
    _reset()
    dyva._note_empty_answer(H, M)
    dyva._note_empty_answer(H, M)
    assert dyva._should_suppress_think(H, M) is True


def test_a_real_answer_clears_the_tally():
    _reset()
    dyva._note_empty_answer(H, M)          # one transient choke
    dyva._clear_empty_strikes(H, M)        # then it answered fine
    assert dyva._should_suppress_think(H, M) is False
    assert dyva._note_empty_answer(H, M) == 1   # next empty starts from scratch


def test_strikes_are_per_pair():
    _reset()
    dyva._note_empty_answer(H, M)
    dyva._note_empty_answer(H, M)
    assert dyva._should_suppress_think(H, M) is True
    assert dyva._should_suppress_think(H, "other:7b") is False
    assert dyva._should_suppress_think("http://elsewhere:11434", M) is False
