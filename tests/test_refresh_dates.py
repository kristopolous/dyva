"""The release-date puller — pytest, synthetic page (no network).

Pins the two things that actually broke before: (1) the Next.js payload is decoded
with a JSON scanner, so a `])` inside the data can't truncate the chunk (a plain
non-greedy regex did exactly that, yielding zero dates); (2) keys are normalised
with dyva._norm_model, the same rule the runtime lookup uses.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva                              # noqa: E402
from dyva import refresh_dates as rd     # noqa: E402


def _page(models):
    """A minimal page carrying `models` the way Next.js ships them: an escaped-JSON
    string, chunk-prefixed `8:`, inside self.__next_f.push([1,"…"])."""
    inner = "8:" + json.dumps(models)
    chunk = "self.__next_f.push([1," + json.dumps(inner) + "])"
    return "<!doctype html><script>" + chunk + "</script>noise"


def test_extract_and_build_normalizes_like_the_lookup():
    models = [
        {"slug": "foo-1", "releaseDate": "2026-01-02", "name": "Foo 1"},
        {"slug": "bar-2b", "releaseDate": "2025-11-05", "name": "Bar 2B", "shortName": "bar2"},
        {"slug": "no-date"},                     # no releaseDate -> ignored
    ]
    found = rd.extract(_page(models))
    assert set(found) == {"foo-1", "bar-2b"}     # the date-less object is dropped

    out = rd.build(found)
    assert out["_count"] == 2
    assert out["_source"] == rd.SOURCE
    # keys are dyva._norm_model(...) of slug/name/shortName
    assert out["dates"][dyva._norm_model("foo-1")] == "2026-01-02"      # 'foo1'
    assert out["dates"][dyva._norm_model("bar-2b")] == "2025-11-05"     # 'bar2b'
    assert out["dates"]["bar2"] == "2025-11-05"                         # from shortName


def test_bracket_paren_inside_payload_does_not_truncate():
    # a literal '])' inside a field would end a naive regex capture early; the JSON
    # scanner must still read the whole chunk and find the LATER model's date.
    models = [
        {"slug": "early-9", "releaseDate": "2026-02-02", "name": "weird]) name"},
        {"slug": "late-9", "releaseDate": "2026-09-09", "name": "Late 9"},
    ]
    out = rd.build(rd.extract(_page(models)))
    assert out["dates"][dyva._norm_model("late-9")] == "2026-09-09"     # survived the ']' ')'


def test_refresh_writes_file_and_fails_loudly_when_empty(tmp_path, monkeypatch):
    target = tmp_path / "model-release-dates.json"
    monkeypatch.setattr(rd, "REL_DATES_FILE", str(target))

    status, _ = rd.refresh(html=_page([{"slug": "qwen-9", "releaseDate": "2026-04-04"}]))
    assert status == "ok"
    assert json.loads(target.read_text())["dates"][dyva._norm_model("qwen-9")] == "2026-04-04"

    # a page with no {slug, releaseDate} objects must FAIL, not write an empty file
    status, detail = rd.refresh(html="<html>nothing here</html>", write=True)
    assert status == "fail" and "shape" in detail
