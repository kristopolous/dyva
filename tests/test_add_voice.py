"""Adding a voice by upload: the audio drops into the writable voices catalog and
becomes a fish-cloneable reference (`uv run pytest tests/test_add_voice.py -q`).

A user uploads an audio clip on the Speech pane; dyva writes it into the runtime
drop-in (CACHE_DIR/voices) and appends a catalog.json entry. From then on it's an
ordinary catalog voice: listed by _catalog_voice_rows and cloned by fish through the
existing reference-audio path (_voice_reference_audio -> _fish_reference_body). These
tests cover the pure id helpers and the on-disk _add_catalog_voice orchestration.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# --- id sanitization --------------------------------------------------------

def test_safe_voice_id_keeps_piper_style_ids():
    assert dyva._safe_voice_id("en-US_f_jodi") == "en-US_f_jodi"


def test_safe_voice_id_replaces_spaces_and_punctuation():
    out = dyva._safe_voice_id("My Cool Voice!")
    assert " " not in out and "!" not in out
    assert out == "My_Cool_Voice"


def test_safe_voice_id_is_traversal_safe():
    out = dyva._safe_voice_id("../../etc/passwd")
    assert "/" not in out and ".." not in out and out


def test_safe_voice_id_empty_falls_back():
    assert dyva._safe_voice_id("") == "voice"
    assert dyva._safe_voice_id("***") == "voice"


def test_safe_voice_id_capped_length():
    assert len(dyva._safe_voice_id("a" * 500)) <= 64


# --- uniqueness -------------------------------------------------------------

def test_unique_voice_id_passthrough_when_free():
    assert dyva._unique_voice_id("dan", {"jodi"}) == "dan"


def test_unique_voice_id_suffixes_on_collision():
    assert dyva._unique_voice_id("dan", {"dan"}) == "dan-2"
    assert dyva._unique_voice_id("dan", {"dan", "dan-2", "dan-3"}) == "dan-4"


# --- add to catalog (on disk) ----------------------------------------------

def test_add_catalog_voice_writes_file_and_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    entry = dyva._add_catalog_voice("Aunt May", b"RIFFfake-wav-bytes", ".wav")
    assert entry["id"] == "Aunt_May"
    assert entry["source"] == "upload"
    # file landed in the drop-in dir
    fpath = os.path.join(str(tmp_path), "voices", entry["file"])
    assert os.path.isfile(fpath)
    with open(fpath, "rb") as fh:
        assert fh.read() == b"RIFFfake-wav-bytes"


def test_added_voice_appears_in_catalog_and_resolves(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    dyva._add_catalog_voice("Aunt May", b"RIFFfake-wav-bytes", ".wav")
    cat = dyva.load_voice_catalog()
    assert any(e["id"] == "Aunt_May" for e in cat)
    # fish reads the reference bytes back through the normal path
    assert dyva._voice_reference_audio("Aunt_May") == b"RIFFfake-wav-bytes"


def test_add_catalog_voice_unique_id_on_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    a = dyva._add_catalog_voice("May", b"one", ".wav")
    b = dyva._add_catalog_voice("May", b"two", ".wav")
    assert a["id"] == "May"
    assert b["id"] == "May-2"          # does not clobber the first upload
    assert dyva._voice_reference_audio("May") == b"one"
    assert dyva._voice_reference_audio("May-2") == b"two"


def test_add_catalog_voice_does_not_override_shipped(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    # en-US_f_jodi ships in the package catalog; an upload of the same name must
    # get a distinct id rather than shadow the curated voice.
    entry = dyva._add_catalog_voice("en-US_f_jodi", b"xxxx", ".wav")
    assert entry["id"] != "en-US_f_jodi"


def test_add_catalog_voice_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    try:
        dyva._add_catalog_voice("nope", b"", ".wav")
    except ValueError:
        return
    assert False, "expected ValueError on empty audio"
