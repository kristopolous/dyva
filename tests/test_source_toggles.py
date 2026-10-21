"""Source on/off toggles for the Settings UI — pytest
(`uv run pytest tests/test_source_toggles.py -q`).

The backend verbs (disabled_sources / set_sources_enabled / known_source_names)
already existed; this pins `_source_toggles()`, the view the Settings UI renders:
one row per switchable source (built-in + configured) with its enabled flag, a
built-in marker, and dedup so a configured source that shadows a built-in name
shows once. Pure/no network; drives SETTINGS_FILE at a temp path.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

BUILTIN = {s["name"] for s in dyva.BUILTIN_SOURCES}


def _with_settings(tmp_path, cfg):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(cfg))
    dyva.SETTINGS_FILE = str(f)
    return f


def test_toggles_list_every_builtin_enabled_by_default(tmp_path):
    saved = dyva.SETTINGS_FILE
    try:
        _with_settings(tmp_path, {})
        toggles = dyva._source_toggles()
        names = {t["name"] for t in toggles}
        assert BUILTIN <= names
        for t in toggles:
            if t["name"] in BUILTIN:
                assert t["builtin"] is True
                assert t["enabled"] is True
    finally:
        dyva.SETTINGS_FILE = saved


def test_disabled_source_shows_as_off(tmp_path):
    saved = dyva.SETTINGS_FILE
    try:
        _with_settings(tmp_path, {"disabled_sources": ["forrany"]})
        by = {t["slug"]: t for t in dyva._source_toggles()}
        assert by["forrany"]["enabled"] is False
        assert by["spider"]["enabled"] is True
    finally:
        dyva.SETTINGS_FILE = saved


def test_configured_source_shadowing_a_builtin_name_shows_once(tmp_path):
    # a user 'graflex' additional source must not double the built-in 'graflex' row
    saved = dyva.SETTINGS_FILE
    try:
        _with_settings(tmp_path, {"sources": [{"name": "graflex", "url": "http://x/y.json"}]})
        toggles = dyva._source_toggles()
        gf = [t for t in toggles if t["slug"] == "graflex"]
        assert len(gf) == 1
        assert gf[0]["builtin"] is True   # the built-in wins the dedup
    finally:
        dyva.SETTINGS_FILE = saved


def test_set_sources_enabled_round_trips_into_toggles(tmp_path):
    saved = dyva.SETTINGS_FILE
    try:
        _with_settings(tmp_path, {})
        dyva.set_sources_enabled(["spider"], False)
        assert "spider" in dyva.disabled_sources()
        assert {t["slug"]: t["enabled"] for t in dyva._source_toggles()}["spider"] is False
        dyva.set_sources_enabled(["all"], True)
        assert dyva.disabled_sources() == set()
        assert all(t["enabled"] for t in dyva._source_toggles())
    finally:
        dyva.SETTINGS_FILE = saved


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
