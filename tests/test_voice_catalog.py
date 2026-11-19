"""The dyva voice catalog: a small, curated, ethically-clean set of reference voices
(`uv run pytest tests/test_voice_catalog.py -q`).

The fish pool offers zero-shot cloning but no coherent voice catalog — each host carries
whatever private/junk reference clips its operator uploaded (test fixtures, non-consensual
celebrity clones), with NO gender/accent/language metadata. So dyva owns a tiny curated
catalog instead: a few CC0 reference clips tagged {lang, region, gender}, named Piper-style
(`en-US_f_jodi`), that a user can ask for in plain words ("female american accent").

`_voice_query_constraints(text)` parses that plain-words request into tag constraints;
`_resolve_voice(text, catalog)` returns the best-matching entry (or None -> host default).
Both are pure so the matching logic is tested without touching disk or a host.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

CATALOG = [
    {"id": "en-US_f_jodi", "file": "en-US_f_jodi.wav", "lang": "en", "region": "US", "gender": "f"},
    {"id": "en-US_m_dan", "file": "en-US_m_dan.wav", "lang": "en", "region": "US", "gender": "m"},
    {"id": "en-GB_f_cori", "file": "en-GB_f_cori.wav", "lang": "en", "region": "GB", "gender": "f"},
]


# --- query -> constraints ---------------------------------------------------

def test_constraints_gender_synonyms():
    assert dyva._voice_query_constraints("female")["gender"] == "f"
    assert dyva._voice_query_constraints("a woman")["gender"] == "f"
    assert dyva._voice_query_constraints("male voice")["gender"] == "m"
    assert dyva._voice_query_constraints("a man")["gender"] == "m"


def test_constraints_region_synonyms():
    for q in ("american", "america", "us", "usa"):
        assert dyva._voice_query_constraints(q)["region"] == "US", q
    for q in ("british", "uk", "england", "english"):
        assert dyva._voice_query_constraints(q)["region"] == "GB", q


def test_constraints_combined_and_case_insensitive():
    c = dyva._voice_query_constraints("Female American accent")
    assert c["gender"] == "f" and c["region"] == "US"


def test_constraints_empty_when_nothing_recognized():
    assert dyva._voice_query_constraints("purple monday") == {}
    assert dyva._voice_query_constraints("") == {}


# --- resolve ----------------------------------------------------------------

def test_resolve_female_american():
    assert dyva._resolve_voice("female american accent", CATALOG)["id"] == "en-US_f_jodi"


def test_resolve_man_picks_male():
    assert dyva._resolve_voice("a man", CATALOG)["id"] == "en-US_m_dan"


def test_resolve_british_woman():
    assert dyva._resolve_voice("british woman", CATALOG)["id"] == "en-GB_f_cori"


def test_resolve_exact_id():
    assert dyva._resolve_voice("en-US_m_dan", CATALOG)["id"] == "en-US_m_dan"


def test_resolve_no_match_is_none():
    # a constraint nothing satisfies -> None, so the caller falls back to host default
    assert dyva._resolve_voice("french man", CATALOG) is None


def test_resolve_empty_query_is_none():
    assert dyva._resolve_voice("", CATALOG) is None
    assert dyva._resolve_voice(None, CATALOG) is None


# --- the shipped catalog file -----------------------------------------------

def test_shipped_catalog_is_valid_and_files_exist():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dyva", "voices")
    cat = json.load(open(os.path.join(base, "catalog.json")))
    assert isinstance(cat, list) and len(cat) >= 2
    for e in cat:
        for k in ("id", "file", "lang", "region", "gender"):
            assert e.get(k), f"{e.get('id')} missing {k}"
        assert os.path.exists(os.path.join(base, e["file"])), f"missing wav for {e['id']}"


# --- fish catalog-voice cloning ---------------------------------------------
# A resolved catalog voice drives fish /v1/tts by riding inline as a reference
# wav. The bytes must stay raw (msgpack), which is why cloning can't use the
# bare-{text} JSON body.

def test_load_voice_catalog_resolves_shipped_wavs():
    cat = dyva.load_voice_catalog()
    assert isinstance(cat, list) and len(cat) >= 2
    assert {"en-US_f_jodi", "en-US_m_dan"} <= {e["id"] for e in cat}
    for e in cat:
        assert os.path.isabs(e["path"]) and os.path.exists(e["path"])


def test_voice_reference_audio_reads_bytes_for_a_match():
    jodi = next(e for e in dyva.load_voice_catalog() if e["id"] == "en-US_f_jodi")
    want = open(jodi["path"], "rb").read()
    assert dyva._voice_reference_audio("en-US_f_jodi") == want       # by id
    assert dyva._voice_reference_audio("female american accent") == want  # by words


def test_voice_reference_audio_none_when_no_match_or_empty():
    assert dyva._voice_reference_audio("french man") is None   # recognized, uncatalogued
    assert dyva._voice_reference_audio("") is None
    assert dyva._voice_reference_audio(None) is None


def test_fish_reference_body_carries_raw_bytes_inline():
    body = dyva._fish_reference_body("hello there", b"RIFFxxxx")
    assert body["text"] == "hello there"
    assert body["references"] == [{"audio": b"RIFFxxxx", "text": ""}]
    # raw bytes (not a base64 str) is the point: only msgpack preserves them, so the
    # clone path steps up from the bare-{text} JSON body to msgpack.
    assert isinstance(body["references"][0]["audio"], bytes)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
