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


# --- a curated catalog voice is fish-clone-only -----------------------------
# Catalog voices (en-US_f_jodi) are produced by CLONING a reference wav, which only
# fish hosts do. If the OpenAI-TTS race is allowed to land on a chatterbox host it
# silently substitutes its own first voice (the wrong-voice bug). So when the named
# voice resolves to the catalog, _find_openai_tts_hosts restricts to fish hosts.

def _neutral(monkeypatch):
    monkeypatch.setattr(dyva, "load_marks", lambda: {})
    monkeypatch.setattr(dyva, "get_last", lambda k: None)
    monkeypatch.setattr(dyva, "rerank_scores", lambda: {})
    monkeypatch.setattr(dyva, "capability_tier", lambda *a, **k: 0)
    monkeypatch.setattr(dyva, "_idle_first", lambda hs: hs)


OAI_SERVERS = [{"server": "fish1", "service": "fish", "models": ["fish"]},
               {"server": "cb1", "service": "chatterbox", "models": ["chatterbox"]}]


def test_catalog_voice_restricts_to_fish_hosts(monkeypatch):
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    monkeypatch.setattr(dyva, "load_voice_catalog", lambda: [{"id": "en-US_f_jodi"}])
    hosts = dyva._find_openai_tts_hosts("en-US_f_jodi")
    assert [h[0] for h in hosts] == ["fish1"]         # chatterbox can't clone it


def test_non_catalog_voice_keeps_all_openai_hosts(monkeypatch):
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    monkeypatch.setattr(dyva, "load_voice_catalog", lambda: [{"id": "en-US_f_jodi"}])
    # "Abigail" is a chatterbox voice, not in the catalog -> no fish restriction
    hosts = dyva._find_openai_tts_hosts("Abigail")
    assert set(h[0] for h in hosts) == {"fish1", "cb1"}


def test_no_voice_named_keeps_all_openai_hosts(monkeypatch):
    _neutral(monkeypatch)
    monkeypatch.setattr(dyva, "load_servers", lambda: OAI_SERVERS)
    monkeypatch.setattr(dyva, "load_voice_catalog", lambda: [{"id": "en-US_f_jodi"}])
    hosts = dyva._find_openai_tts_hosts(None)
    assert set(h[0] for h in hosts) == {"fish1", "cb1"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
