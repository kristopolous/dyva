"""/v1/audio/voices lists voices with host counts, popularity-sorted
(`uv run pytest tests/test_voice_aggregate.py -q`).

The Speech pane should show every voice and how many hosts offer it, sorted by the
ones on the most hosts — like the models panel. _aggregate_voices rolls the per-host
TTS survey up into {voice, hosts} rows: case-insensitive dedup (Uncle_fu / Uncle_Fu
are ONE voice, their hosts summed — the live survey had both), zero-shot/custom
placeholders dropped (they're selectors, not voices), sorted by host count then name.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def test_counts_hosts_per_voice_sorted_by_popularity():
    survey = {
        "h1": {"node": "X", "voices": ["Dylan", "Eric", "Aiden"]},
        "h2": {"node": "X", "voices": ["Dylan", "Eric"]},
        "h3": {"node": "X", "voices": ["Dylan"]},
    }
    out = dyva._aggregate_voices(survey)
    assert out == [{"voice": "Dylan", "hosts": 3},
                   {"voice": "Eric", "hosts": 2},
                   {"voice": "Aiden", "hosts": 1}]


def test_case_variants_collapse_and_sum_hosts():
    survey = {
        "h1": {"node": "X", "voices": ["Uncle_fu"]},
        "h2": {"node": "X", "voices": ["Uncle_Fu"]},
        "h3": {"node": "X", "voices": ["Uncle_fu"]},
    }
    out = dyva._aggregate_voices(survey)
    assert len(out) == 1
    assert out[0]["hosts"] == 3                 # all three hosts, one voice
    assert out[0]["voice"] == "Uncle_fu"        # the dominant casing (2 of 3)


def test_placeholders_are_dropped():
    survey = {"h1": {"node": "X",
                     "voices": ["None (Zero-shot / Custom)", "Dylan", "custom", "zero-shot"]}}
    assert [v["voice"] for v in dyva._aggregate_voices(survey)] == ["Dylan"]


def test_hosts_without_a_node_or_voices_are_ignored():
    survey = {
        "h1": {"node": None, "voices": ["Ghost"]},   # no node -> not a real tts host
        "h2": {"node": "X", "voices": []},           # node but no voices
        "h3": {"node": "X", "voices": ["Dylan"]},
    }
    assert dyva._aggregate_voices(survey) == [{"voice": "Dylan", "hosts": 1}]


def test_empty_survey_is_empty_list():
    assert dyva._aggregate_voices({}) == []


# --- curated catalog voices merged into the list ----------------------------
# The shipped curated catalog is fish-cloneable, so those voices must appear in
# /v1/audio/voices even when the pool is fish-only (no ComfyUI node hosts). Each
# carries the LIVE fish-host count as its confidence number — "about this many hosts
# have a plausible plan of execution for this voice" — the signal the UI shows.

def test_fish_host_count_counts_only_fish_with_a_host(monkeypatch):
    servers = [{"server": "f1", "service": "fish"},
               {"server": "f2", "service": "fish"},
               {"server": "c1", "service": "comfyui"},      # not fish
               {"server": None, "service": "fish"}]          # no host -> not counted
    monkeypatch.setattr(dyva, "load_servers", lambda: servers)
    assert dyva._fish_host_count() == 2


def test_catalog_voice_rows_carry_fish_count_and_source(monkeypatch):
    monkeypatch.setattr(dyva, "load_voice_catalog",
                        lambda: [{"id": "en-US_f_jodi"}, {"id": "en-US_m_dan"}])
    assert dyva._catalog_voice_rows(7) == [
        {"voice": "en-US_f_jodi", "hosts": 7, "source": "catalog"},
        {"voice": "en-US_m_dan", "hosts": 7, "source": "catalog"}]


def test_merge_sorts_catalog_in_by_count():
    host_voices = [{"voice": "Dylan", "hosts": 3}, {"voice": "Aiden", "hosts": 1}]
    catalog = [{"voice": "en-US_f_jodi", "hosts": 2, "source": "catalog"}]
    assert dyva._merge_voice_rows(host_voices, catalog) == [
        {"voice": "Dylan", "hosts": 3},
        {"voice": "en-US_f_jodi", "hosts": 2, "source": "catalog"},
        {"voice": "Aiden", "hosts": 1}]


def test_merge_drops_a_catalog_voice_a_host_already_advertises():
    # a host advertising the same voice -> its live row wins, no double listing
    host_voices = [{"voice": "en-US_f_jodi", "hosts": 5}]
    catalog = [{"voice": "en-US_f_jodi", "hosts": 2, "source": "catalog"}]
    assert dyva._merge_voice_rows(host_voices, catalog) == [
        {"voice": "en-US_f_jodi", "hosts": 5}]


def test_merge_with_no_host_voices_is_just_the_catalog():
    catalog = [{"voice": "en-US_f_jodi", "hosts": 4, "source": "catalog"}]
    assert dyva._merge_voice_rows([], catalog) == catalog


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
