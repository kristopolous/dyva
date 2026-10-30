"""The incremental ollama-library size puller — pytest, synthetic HTML (no network).

The whole point is the cheap incremental walk: newest-first, and the moment we hit a
model already on disk we STOP, because everything below it is already captured. These
tests pin that stop, plus the tags-page parse (size/digest/params_b + 'latest' alias).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dyva import refresh_sizes as rs    # noqa: E402


def _tags_html(model, tag="7b", size="4.0GB", digest="aaaabbbbcccc"):
    return '<a href="/library/%s:%s"></a><span>%s</span> &bull; %s' % (model, tag, digest, size)


def test_index_models_ordered_deduped_and_ignores_non_model_links():
    html = ('<a href="/library?sort=newest">sort</a>'
            '<a href="/library/lfm3"></a><a href="/library/lfm3"></a>'   # dup (mobile+desktop)
            '<a href="/library/d3"></a>')
    assert rs.index_models(html) == ["lfm3", "d3"]                        # order kept, deduped, ?link ignored


def test_parse_tags_extracts_size_digest_params_and_latest_alias():
    html = ('<a href="/library/foo:3b"></a>'
            '<span class="border-blue-500">latest</span>'               # the 'latest' pill badges 3b
            '<span>aaaabbbbcccc</span> &bull; 2.0GB'
            '<a href="/library/foo:1b"></a><span>ddddeeeeffff</span> &bull; 1.3GB')
    tags = rs.parse_tags(html, "foo")
    assert tags["3b"] == {"size_str": "2.0GB", "bytes": 2_000_000_000,
                          "digest": "aaaabbbbcccc", "params_b": 3.0}
    assert tags["1b"]["params_b"] == 1.0
    assert tags["latest"] == tags["3b"]                                  # alias resolved to the badged tag


def test_explicit_latest_row_gets_params_b_from_shared_digest():
    # a ':latest' row names no size; it shares a digest with the sized '8b' row, so
    # latest must adopt 8b's params_b (what dyva reads for bare-name size grouping).
    html = ('<a href="/library/bar:latest"></a><span>cafebabecafe</span> &bull; 5.0GB'
            '<a href="/library/bar:8b"></a><span>cafebabecafe</span> &bull; 5.0GB')
    tags = rs.parse_tags(html, "bar")
    assert tags["8b"]["params_b"] == 8.0
    assert tags["latest"]["params_b"] == 8.0        # resolved via the shared digest


def test_refresh_stops_at_first_known_model(tmp_path, monkeypatch):
    sizes = tmp_path / "ollama-latest-sizes.json"
    sizes.write_text(json.dumps({"_count": 1, "models": {"known1": {"tags": {}}}}))
    monkeypatch.setattr(rs, "OLLAMA_SIZES_FILE", str(sizes))

    # newest-first: two new, then one we already have, then another new BELOW it
    index = ('<a href="/library/new1"></a><a href="/library/new2"></a>'
             '<a href="/library/known1"></a><a href="/library/new3"></a>')
    fetched = []

    def stub(name):
        fetched.append(name)
        return _tags_html(name)

    status, detail = rs.refresh(index_html=index, tags_fetcher=stub)

    assert fetched == ["new1", "new2"]                 # stopped AT known1; new3 never fetched
    assert status == "ok" and "2 new" in detail
    out = json.loads(sizes.read_text())
    assert set(out["models"]) == {"known1", "new1", "new2"}   # existing kept, new added
    assert out["models"]["new1"]["tags"]["7b"]["params_b"] == 7.0
    assert out["_count"] == 3


def test_refresh_noop_when_newest_already_known(tmp_path, monkeypatch):
    sizes = tmp_path / "ollama-latest-sizes.json"
    before = {"_count": 1, "models": {"top": {"tags": {}}}}
    sizes.write_text(json.dumps(before))
    monkeypatch.setattr(rs, "OLLAMA_SIZES_FILE", str(sizes))

    fetched = []
    status, detail = rs.refresh(index_html='<a href="/library/top"></a>',
                                tags_fetcher=lambda n: fetched.append(n) or _tags_html(n))

    assert fetched == []                               # first model already known -> fetch nothing
    assert status == "ok" and "no new" in detail
    assert json.loads(sizes.read_text()) == before     # file untouched
