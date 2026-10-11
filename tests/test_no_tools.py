"""Tests for the no-tools capability mark — pytest (`uv run pytest dyva/test_no_tools.py`).

When a host 400s with "does not support tools", dyva records the (host, model) as
tools-incapable (mirroring mark_no_vision) so tool requests stop doorknocking it,
while plain chat still uses it. These pin the pure marking + capability-match logic;
the write is stubbed so no DB is touched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _stub(monkeypatch, caps):
    """Make load_knowns report `caps` for host H / model M and capture _mark_known."""
    monkeypatch.setattr(dyva, "load_knowns", lambda: {"H": {"models": {"M": list(caps)}}})
    seen = {}
    monkeypatch.setattr(dyva, "_mark_known", lambda h, m, c: seen.setdefault("call", (h, m, c)))
    return seen


def test_no_tools_drops_tools_keeps_the_rest(monkeypatch):
    seen = _stub(monkeypatch, ["tools", "vision", "completion"])
    dyva.mark_no_tools("H", "M")
    assert seen["call"] == ("H", "M", ["completion", "vision"])   # vision survives, tools gone


def test_no_tools_empty_defaults_to_completion(monkeypatch):
    # a model with no recorded caps becomes KNOWN-incapable (["completion"]), not unknown,
    # so find_servers treats it as incapable for a tools request rather than racing it
    seen = _stub(monkeypatch, [])
    dyva.mark_no_tools("H", "M")
    assert seen["call"] == ("H", "M", ["completion"])


def test_no_tools_only_tools_defaults_to_completion(monkeypatch):
    seen = _stub(monkeypatch, ["tools"])
    dyva.mark_no_tools("H", "M")
    assert seen["call"] == ("H", "M", ["completion"])


def test_no_tools_idempotent_when_already_lacking(monkeypatch):
    # already tools-incapable -> no write (nothing changed)
    seen = _stub(monkeypatch, ["completion"])
    dyva.mark_no_tools("H", "M")
    assert "call" not in seen


def test_model_capable_tools_matching():
    assert dyva._model_capable(["tools", "completion"], ["tools"]) is True
    assert dyva._model_capable(["completion"], ["tools"]) is False   # the no-tools model
    assert dyva._model_capable([], ["tools"]) is False               # unknown caps != capable


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
