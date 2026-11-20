"""Routing prefers voice-capable hosts when a voice is named
(`uv run pytest tests/test_tts_voice_routing.py -q`).

"All the voices sound identical" root cause: ~87 of ~90 TTS hosts run LongCatTTS,
which has NO voice field, so it silently ignores whatever voice you asked for. The
router ranked those voiceless hosts equal to the ~17 voice-capable Qwen hosts, so
the race usually landed on a voiceless one and every clip came out the same voice.

_voice_rank is the preference the router folds in just under reputation: when a
voice is named, a host whose surveyed node has NO voice field (it will ignore the
request) sorts LAST; a host that advertises the exact voice sorts FIRST; unknown
hosts aren't buried below the known-voiceless ones. With no voice named it's inert
(every host 0) so the existing ordering is untouched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

VOICED = {"node": "Qwen3TTS", "voices": ["Dylan", "Serena", "Uncle_Fu"]}
VOICELESS = {"node": "LongCatTTS", "voices": []}


def test_no_voice_named_is_inert():
    # nothing asked -> no preference, so the existing tier ordering is untouched
    assert dyva._voice_rank(None, VOICED) == 0
    assert dyva._voice_rank(None, VOICELESS) == 0
    assert dyva._voice_rank("", VOICED) == 0


def test_exact_voice_match_ranks_first():
    assert dyva._voice_rank("Dylan", VOICED) == 0
    assert dyva._voice_rank("dylan", VOICED) == 0          # case-insensitive
    assert dyva._voice_rank("  Uncle_Fu ", VOICED) == 0    # whitespace-tolerant


def test_voice_capable_but_different_catalog_ranks_second():
    assert dyva._voice_rank("Ryan", VOICED) == 1           # has voices, not this one


def test_voiceless_known_node_ranks_last():
    # LongCatTTS will ignore the voice outright -> worst choice when a voice is named
    assert dyva._voice_rank("Dylan", VOICELESS) == 2


def test_unknown_host_is_not_buried_below_voiceless():
    # no survey row: we can't tell, so don't rank it below a host we KNOW ignores voices
    assert dyva._voice_rank("Dylan", None) == 1
    assert dyva._voice_rank("Dylan", {}) == 1


def test_ordering_voiced_before_unknown_before_voiceless():
    rows = {"voiced": VOICED, "unknown": None, "voiceless": VOICELESS}
    order = sorted(rows, key=lambda k: dyva._voice_rank("Dylan", rows[k]))
    assert order == ["voiced", "unknown", "voiceless"]


# --- _find_openai_tts_hosts routes the OpenAI-passthrough race by voice -----
# A named voice goes to a PASSTHROUGH host that can produce it (fast), never forced onto
# the slow ComfyUI graph:
#   - a curated catalog voice (en-US_f_jodi) is a fish CLONE -> fish hosts only;
#   - a chatterbox named voice (Dylan/Abigail) -> the chatterbox host that advertises it;
#     fish (clone-only) is dropped. Chatterbox stays the fast path for its own voices;
#   - a named voice nobody advertises -> any non-fish passthrough host (chatterbox/localai
#     carry named voices), still fast, never comfy;
#   - no voice -> every passthrough host is in play.

def _neutral(monkeypatch):
    monkeypatch.setattr(dyva, "load_marks", lambda: {})
    monkeypatch.setattr(dyva, "get_last", lambda k: None)
    monkeypatch.setattr(dyva, "rerank_scores", lambda: {})
    monkeypatch.setattr(dyva, "capability_tier", lambda *a, **k: 0)
    monkeypatch.setattr(dyva, "_idle_first", lambda hs: hs)
    monkeypatch.setattr(dyva, "load_voice_catalog", lambda: [{"id": "en-US_f_jodi"}])


OAI_SERVERS = [
    {"server": "fish1", "service": "fish", "models": ["fish"], "voices": []},
    {"server": "cb1", "service": "chatterbox", "models": ["chatterbox"],
     "voices": ["Abigail.wav", "Adrian.wav", "Dylan.wav"]},
    {"server": "cb2", "service": "chatterbox", "models": ["chatterbox"],
     "voices": ["Serena.wav"]}]


def test_catalog_voice_restricts_to_fish_hosts(monkeypatch):
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    assert [h[0] for h in dyva._find_openai_tts_hosts("en-US_f_jodi")] == ["fish1"]


def test_named_voice_goes_to_the_chatterbox_host_that_has_it(monkeypatch):
    # "Dylan" is a chatterbox voice -> the chatterbox host advertising it, FAST (no comfy).
    # fish (clone-only) and the other chatterbox (wrong catalog) are excluded.
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    assert [h[0] for h in dyva._find_openai_tts_hosts("Dylan")] == ["cb1"]


def test_unadvertised_named_voice_falls_back_to_non_fish_passthrough(monkeypatch):
    # a voice no table lists -> still served by the passthrough path (chatterbox hosts),
    # never forced to comfy; fish dropped (it can't honour a named voice).
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    assert set(h[0] for h in dyva._find_openai_tts_hosts("Nonexistent")) == {"cb1", "cb2"}


def test_no_voice_named_keeps_all_passthrough_hosts(monkeypatch):
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    assert set(h[0] for h in dyva._find_openai_tts_hosts(None)) == {"fish1", "cb1", "cb2"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
