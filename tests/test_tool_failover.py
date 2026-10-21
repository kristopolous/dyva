"""Mid-stream failover of a TOOL CALL — pytest (`uv run pytest tests/test_tool_failover.py -q`).

A streamed response that drops mid-generation is normally continued on another host:
the partial text is carried forward and a fresh host picks up the thread. That splice
is sound for PROSE (text replays cleanly) but not for a TOOL CALL — a function call is
an atomic operation. One host can't emit half its arguments and have another complete
them: re-racing re-emits the whole call on top of the bytes already streamed to the
client (duplication), or the continuation aside reframes it as chat ("sure, let me
continue where I left off").

So by DEFAULT a dropped tool turn is NOT spliced — the stream ends honestly (the client
renders it as truncated). The TOOL_RESUME opt-in ("YOLO") keeps trying anyway, for the
one case that justifies the gamble: a long/expensive tool output where salvaging beats a
full redo. It is understood to be far from 100%.

Pins the pure decision (`_resume_dropped_stream`) and the settable flag. Pure/no network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ---- _resume_dropped_stream: the splice-or-fail decision ---------------------

def test_prose_drop_is_always_spliced_regardless_of_the_flag():
    # no tool call in flight (0 tool-arg chars) -> text, which replays cleanly.
    assert dyva._resume_dropped_stream(0, tool_resume=False) is True
    assert dyva._resume_dropped_stream(0, tool_resume=True) is True


def test_tool_call_drop_fails_by_default():
    # tool-call argument chars were streamed -> an atomic op; default is to NOT
    # splice (the client gets an honest truncation instead of a duplicated call).
    assert dyva._resume_dropped_stream(50, tool_resume=False) is False


def test_tool_call_drop_is_retried_only_under_the_yolo_flag():
    assert dyva._resume_dropped_stream(50, tool_resume=True) is True


# ---- the settable flag -------------------------------------------------------

def test_tool_resume_defaults_off():
    # the splice is the buggy path, so it must not be the default.
    assert dyva.TOOL_RESUME is False


def test_tool_resume_loads_from_settings(tmp_path):
    saved_file, saved_val = dyva.SETTINGS_FILE, dyva.TOOL_RESUME
    try:
        f = tmp_path / "settings.json"
        f.write_text(json.dumps({"tool_resume": True}))
        dyva.SETTINGS_FILE = str(f)
        dyva.TOOL_RESUME = False
        dyva.load_settings()
        assert dyva.TOOL_RESUME is True
    finally:
        dyva.SETTINGS_FILE, dyva.TOOL_RESUME = saved_file, saved_val


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
