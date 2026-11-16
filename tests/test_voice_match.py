"""Voice matching is case-insensitive partial with token narrowing, like model matching
(`uv run pytest tests/test_voice_match.py -q`).

Voices are named as chaotically as models across engines (adam_us, us_adam, Adam_US,
adam_24bit, am_adam, bm_adam). Routing must not hard-fail on an exact string — "ask for
adam, get an adam" — but you can still narrow: ask "adam uk" and you get the British one.

`voice_query_match(query, voice)` splits the query into tokens (whitespace/separators) and
matches when EVERY token is a case-insensitive, separator-blind substring of the voice.
So "adam" matches any adam; "adam uk" matches only adam voices that also carry "uk";
"24bit"/"q4" junk in the voice name never blocks a match, because we test query⊆voice.
Empty query matches anything. It feeds per-host voice resolution and ComfyUI voice ranking.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def test_loose_query_matches_decorated_voice():
    for v in ["Adam_US", "us_adam", "adamus", "adam_24bit",
              "adam_24bit_q4_km_01.safetensors", "am_adam", "ADAM.wav"]:
        assert dyva.voice_query_match("adam", v), v


def test_token_narrowing_selects_the_variant():
    # extra query words NARROW, they don't break: "adam uk" -> only the uk adam
    assert dyva.voice_query_match("adam uk", "Adam_UK.wav")
    assert dyva.voice_query_match("adam uk", "uk_adam_24bit")
    assert not dyva.voice_query_match("adam uk", "Adam_US.wav")
    assert not dyva.voice_query_match("adam uk", "Serena_UK.wav")


def test_case_insensitive_and_separator_blind():
    assert dyva.voice_query_match("DYLAN", "dylan.wav")
    assert dyva.voice_query_match("am_adam", "amadam")      # separators ignored both sides


def test_non_matching_voice_is_rejected():
    assert not dyva.voice_query_match("adam", "serena.wav")
    assert not dyva.voice_query_match("dylan", "abigail")


def test_empty_query_matches_anything():
    assert dyva.voice_query_match("", "anything")
    assert dyva.voice_query_match(None, "anything")


def test_resolve_chatterbox_voice_is_partial_and_narrows():
    voices = ["Abigail.wav", "Adam_US.wav", "Adam_UK.wav", "Dylan.wav"]
    assert dyva._resolve_chatterbox_voice("adam", voices) == "Adam_US.wav"      # first adam
    assert dyva._resolve_chatterbox_voice("adam uk", voices) == "Adam_UK.wav"   # narrowed
    assert dyva._resolve_chatterbox_voice("nobody", voices) == "Abigail.wav"    # never 404s


def test_voice_rank_uses_partial_match():
    row = {"node": "X", "voices": ["Adam_US", "Serena"]}
    assert dyva._voice_rank("adam", row) == 0                                   # has an adam
    assert dyva._voice_rank("adam", {"node": "X", "voices": ["Serena", "Dylan"]}) == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
