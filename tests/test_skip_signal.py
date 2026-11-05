"""Operator "skip" on a committed stream — `_pump_stream` returns a distinct "skip" — pytest
(`uv run pytest tests/test_skip_signal.py -q`).

skip != stop. Stop (`cancel`) cancels the whole worker: `_pump_stream` returns "stopped" and
the failover loop ends the client's stream. Skip means "this host has stalled — move on": the
pump abandons THIS host and returns "skip", which the failover loop treats like a mid-stream
truncation (re-race the next host on the SAME open client connection, carrying work forward
under the existing mid-toolcall policy). The connection is never dropped.

This covers the pump-level signal: a set `skip` Event makes the pump return "skip"; a set
`cancel` still returns "stopped"; stop wins if both are set (terminate is the safer default);
and with neither, the stream runs to "done" unchanged.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


class _Out:
    def __init__(self):
        self.chunks = []

    async def write(self, b):
        self.chunks.append(b)


class _Resp:
    def __init__(self, lines):
        self._q = list(lines)
        self.content = self

    async def readline(self):
        return self._q.pop(0) if self._q else b""

    async def release(self):
        pass

    def close(self):
        pass


def _line(obj):
    return (json.dumps(obj) + "\n").encode()


def _event(set_it):
    e = asyncio.Event()
    if set_it:
        e.set()
    return e


def _pump(monkeypatch, first, rest, partial, cancel=None, skip=None):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "account_tokens", _noop)
    monkeypatch.setattr(dyva, "broadcast_activity", _noop)
    out, resp = _Out(), _Resp([_line(o) for o in rest])
    status = asyncio.run(dyva._pump_stream(
        out, resp, _line(first), "http://h:1", "m",
        False, False, None, None, partial, cancel=cancel, skip=skip))
    return status, out


# a stream that keeps going (no terminal done chunk in what we feed before the signal fires)
_FIRST = {"message": {"content": "partial so far"}, "done": False}
_MORE = [{"message": {"content": " and more"}, "done": False},
         {"message": {"content": ""}, "done": True, "done_reason": "stop"}]


def test_pump_returns_skip_when_skip_signal_set(monkeypatch):
    status, _ = _pump(monkeypatch, _FIRST, _MORE, ["", 0], skip=_event(True))
    assert status == "skip"


def test_pump_without_skip_runs_to_done(monkeypatch):
    # backward compat: no skip signal -> the stream completes normally.
    status, _ = _pump(monkeypatch, _FIRST, _MORE, ["", 0], skip=_event(False))
    assert status == "done"


def test_pump_stop_still_returns_stopped(monkeypatch):
    # guard: the existing stop path is untouched.
    status, _ = _pump(monkeypatch, _FIRST, _MORE, ["", 0], cancel=_event(True))
    assert status == "stopped"


def test_pump_stop_wins_over_skip(monkeypatch):
    # if both are set, stop (terminate) takes precedence over skip (continue) — safer default.
    status, _ = _pump(monkeypatch, _FIRST, _MORE, ["", 0], cancel=_event(True), skip=_event(True))
    assert status == "stopped"
