"""graflex built-in source — pytest (`uv run pytest tests/test_graflex_source.py -q`).

The "graflex" built-in is dyva's OWN survey, published ~3 weeks stale to a plainly
named public file (hosts.json) and shipped as a fourth built-in source so a fresh
install has a populated (mostly-decayed, some-live) pool out of the box as the
GitHub feeds dwindle. The public file is called hosts.json on purpose: it reads as
"the snapshot" and reveals nothing about the internal daily <keyprefix>-DATE.json
naming (a uuid name would have hinted at a hidden scheme).

The snapshot is graflex-shaped — the host endpoint lives in `url`, not `server` —
so the source carries a mapping that renames url->server; every OTHER field
(service / models / checked / geo) passes through under its own name. These pin
that contract plus the backfill posture (parsed last, so it never outranks a
fresher feed). Pure/no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _graflex_src():
    return next((s for s in dyva.BUILTIN_SOURCES if s["name"] == "graflex"), None)


# ---- the source definition ---------------------------------------------------

def test_graflex_is_a_builtin_source():
    s = _graflex_src()
    assert s is not None, "graflex must be a built-in source"
    # public file is plainly named hosts.json (no uuid that hints at a scheme)
    assert s["url"].endswith("/hosts.json")
    # hosting is behind an owned alias so it can move without a code change
    assert "getpostdelete.com" in s["url"]


def test_graflex_is_parsed_last_so_it_only_backfills():
    # Backfill posture: graflex must come AFTER the fresher GitHub feeds in the
    # array, so refresh_cache's fill-if-not-claimed lets them win any shared host.
    names = [s["name"] for s in dyva.BUILTIN_SOURCES]
    assert names.index("graflex") == len(names) - 1
    for feed in ("forrany", "spider", "happyshua"):
        assert names.index(feed) < names.index("graflex")


# ---- the mapping: url->server, everything else passes through -----------------

def _snapshot_row():
    # the real graflex snapshot shape (keys from a live file)
    return {
        "service": "ollama",
        "url": "http://203.0.113.9:11434",
        "models": ["qwen3.8", "llama3:8b"],
        "checked": "2026-09-27T00:00:00+00:00",
        "host": "203.0.113.9:11434",
        "country": "US",
        "asn": 64500,
        "as_org": "Example",
        "bogus_score": 0,
    }


def test_mapping_renames_url_to_server():
    out = dyva._apply_source_mapping(_snapshot_row(), _graflex_src()["mapping"])
    assert out["server"] == "http://203.0.113.9:11434"


def test_mapping_passes_every_other_field_through():
    row = _snapshot_row()
    out = dyva._apply_source_mapping(row, _graflex_src()["mapping"])
    # service/models and the enrichment fields survive untouched for routing + display
    for k in ("service", "models", "checked", "country", "asn", "as_org", "bogus_score"):
        assert out[k] == row[k]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
