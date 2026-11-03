"""Music routing goes through the shared host race, not a bespoke capped loop
— pytest (`uv run pytest tests/test_music_race.py -q`).

Music was the one generative capability that never moved onto `_race_hosts` (the
scaffolding chat/image/video/speech all share). It hand-rolled a sequential
`for cand in hosts[:8]` loop, so a submit tried 8 reputation-top hosts and quit —
which produced a real "100% failure" when those 8 were offline while ~90 live
music hosts sat untried below the cap.

Two units are pinned:
  - `_music_attempt(session, host, job)`: the per-host callback the race runs.
    It returns an Outcome verdict the engine reads (accepted / unreachable /
    unsuitable / …) — the ONLY music-specific part of the race. Reputation
    recording is the engine's job, so this just returns a verdict.
  - `handle_music_post`: the candidate list handed to the job is the WHOLE pool,
    never capped to 8.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva                                        # noqa: E402


class _FakeReq:
    def __init__(self, body, session=None):
        self._body = body
        self.app = {"session": session}
        self.query = {}

    async def json(self):
        return self._body


def _job():
    return {"style": "lofi beats", "lyrics": "", "seconds": 8, "seed": 1, "params": {}}


def _run(coro):
    return asyncio.run(coro)


# ---- _music_attempt: verdict mapping -------------------------------------

def test_attempt_unreachable_when_object_info_is_none(monkeypatch):
    async def _oi(session, host):
        return None
    monkeypatch.setattr(dyva, "_get_object_info", _oi)
    out = _run(dyva._music_attempt(object(), "http://h:8188", _job()))
    assert out.verdict == dyva.V_UNREACHABLE


def test_attempt_unsuitable_when_no_music_family(monkeypatch):
    async def _oi(session, host):
        return {"SomeNode": {}}
    monkeypatch.setattr(dyva, "_get_object_info", _oi)
    monkeypatch.setattr(dyva, "_music_family_on_host", lambda info: (None, None, None))
    out = _run(dyva._music_attempt(object(), "http://h:8188", _job()))
    assert out.verdict == dyva.V_UNSUITABLE


def test_attempt_unsuitable_when_build_raises_musicerror(monkeypatch):
    async def _oi(session, host):
        return {"X": {}}
    monkeypatch.setattr(dyva, "_get_object_info", _oi)
    monkeypatch.setattr(dyva, "_music_family_on_host",
                        lambda info: ({"name": "AceStep"}, "enc", "lat"))
    def _build(*a, **k):
        raise dyva._MusicError("no field for the style")
    monkeypatch.setattr(dyva, "_build_music_workflow", _build)
    out = _run(dyva._music_attempt(object(), "http://h:8188", _job()))
    assert out.verdict == dyva.V_UNSUITABLE


def test_attempt_unreachable_when_submit_connection_dies(monkeypatch):
    async def _oi(session, host):
        return {"X": {}}
    monkeypatch.setattr(dyva, "_get_object_info", _oi)
    monkeypatch.setattr(dyva, "_music_family_on_host",
                        lambda info: ({"name": "AceStep"}, "enc", "lat"))
    monkeypatch.setattr(dyva, "_build_music_workflow",
                        lambda *a, **k: ({"1": {}}, "ace_step.safetensors"))
    async def _submit(session, host, graph, timeout=None):
        raise dyva.ComfyUnreachable("connection refused")
    monkeypatch.setattr(dyva, "comfy_submit", _submit)
    out = _run(dyva._music_attempt(object(), "http://h:8188", _job()))
    assert out.verdict == dyva.V_UNREACHABLE


def test_attempt_accepted_carries_host_pid_family_model(monkeypatch):
    async def _oi(session, host):
        return {"X": {}}
    monkeypatch.setattr(dyva, "_get_object_info", _oi)
    monkeypatch.setattr(dyva, "_music_family_on_host",
                        lambda info: ({"name": "AceStep1.5"}, "enc", "lat"))
    monkeypatch.setattr(dyva, "_build_music_workflow",
                        lambda *a, **k: ({"1": {}}, "ace_step_1.5.safetensors"))
    async def _submit(session, host, graph, timeout=None):
        return "pid-123"
    monkeypatch.setattr(dyva, "comfy_submit", _submit)
    out = _run(dyva._music_attempt(object(), "http://h:8188", _job()))
    assert out.verdict == dyva.V_ACCEPTED
    assert out.result == ("http://h:8188", "pid-123", "AceStep1.5", "ace_step_1.5.safetensors")


# ---- handle_music_post: the candidate list is NOT capped -----------------

def test_submit_hands_the_whole_pool_to_the_job_not_eight(monkeypatch):
    pool = [f"http://h{i}:8188" for i in range(20)]
    monkeypatch.setattr(dyva, "_find_music_hosts", lambda target=None: list(pool))
    monkeypatch.setattr(dyva, "_check_local", lambda req: _async_none())
    monkeypatch.setattr(dyva, "_music_job_save", lambda: None)
    monkeypatch.setattr(dyva, "_load_music_jobs", lambda: None)
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "_run_music_job", _noop)
    monkeypatch.setattr(dyva, "_MUSIC_JOBS", {}, raising=False)

    resp = _run(dyva.handle_music_post(_FakeReq({"prompt": "lofi"}, session=object())))
    jid = json.loads(resp.body.decode())["id"]
    assert len(dyva._MUSIC_JOBS[jid]["hosts"]) == 20   # was capped to 8


async def _async_none():
    return None
