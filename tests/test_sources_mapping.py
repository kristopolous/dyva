"""Additional-source mapping suite — pytest (`uv run pytest tests/test_sources_mapping.py`).

Pins the "Additional sources (third-party formats)" contract from dyva/README.md:
_apply_source_mapping turns a raw third-party row into a dyva host entry.
  * {"field": "x"} renames row["x"] into the target.
  * {"value": c} forces a constant.
  * every other field passes through under its own name (implicit) — no entry needed.
  * a mapped field that's MISSING on the row is left UNSET (not None), so downstream
    setdefault gives service its 'ollama' default instead of a null that never
    classifies as a1111/comfyui.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

asm = dyva._apply_source_mapping


def test_field_renames_and_value_forces_constant():
    out = asm({"url": "http://h:1", "models": ["m"]},
              {"server": {"field": "url"}, "service": {"value": "comfyui"}})
    assert out["server"] == "http://h:1"      # renamed from url
    assert out["service"] == "comfyui"         # forced constant
    assert out["models"] == ["m"]              # untouched passthrough


def test_graflex_style_onetoone_import_needs_no_transforms():
    # graflex already emits server/url, models, service, checked — a straight map.
    row = {"url": "http://h:2", "models": ["a", "b"], "service": "comfyui", "checked": "2026-01-01"}
    out = asm(row, {"server": {"field": "url"}})
    assert out["server"] == "http://h:2"
    assert out["service"] == "comfyui" and out["checked"] == "2026-01-01"


def test_all_fields_pass_through_without_a_mapping_entry():
    row = {"server": "http://h:3", "models": ["m"], "bogus_score": 7, "provider": "aws"}
    out = asm(row, {})
    assert out == row            # implicit passthrough, new fields included


def test_missing_mapped_field_is_left_unset_not_none():
    # service maps to a field the row lacks -> NOT present, so a later setdefault
    # ('ollama') applies rather than a null that never classifies.
    out = asm({"url": "http://h:4"}, {"service": {"field": "svc"}})
    assert "service" not in out


def test_value_constant_wins_even_when_field_absent():
    out = asm({"url": "http://h:5"}, {"service": {"value": "a1111"}})
    assert out["service"] == "a1111"


def test_non_dict_row_yields_empty_entry():
    assert asm(None, {"server": {"field": "url"}}) == {}


def test_non_dict_spec_is_ignored():
    out = asm({"server": "http://h:6"}, {"service": "ollama"})  # spec not a dict
    assert "service" not in out and out["server"] == "http://h:6"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
