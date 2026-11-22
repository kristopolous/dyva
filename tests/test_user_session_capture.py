"""Ingress capture: snapshot the inbound conversation into the user's session store, OFF the
critical path (`uv run pytest tests/test_user_session_capture.py -q`).

Capture happens at request ingress (not mid-stream — the generation/race path is never
touched) and is FIRE-AND-FORGET: the decision is cheap and synchronous, but the blocking
sqlite write is dispatched to a thread executor so it adds no latency to the user's response
(no Amdahl hit — it's only feeding search). Keyed by the routing affinity id, so once the
affinity prefix (AFFINITY_PREFIX_MSGS user messages — a per-host SETTING, 4 on Benjamin)
saturates, further turns dedup onto the same row (brainstorm/user-account.md). The dedup
granularity therefore tracks that setting, not a hardcoded count. Anonymous / blank-key
requests are never captured.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402
from multidict import CIMultiDict   # noqa: E402


class _Req:
    def __init__(self, **headers):
        self.headers = CIMultiDict(headers)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(dyva, "_sessions_db", None)
    yield


MSGS = [{"role": "user", "content": "how do I route qwen"}]


# ---- the decision: pure, cheap, critical-path-safe -------------------------

def test_anonymous_request_is_not_captured():
    assert dyva._session_capture_target(_Req(), {"messages": MSGS}) is None


def test_opted_in_request_targets_affinity_key_and_carries_title():
    t = dyva._session_capture_target(
        _Req(Authorization="Bearer user=chris;pass=x"), {"messages": MSGS})
    assert t is not None
    assert t["userhash"] == dyva._userhash_from_bag({"user": "chris", "pass": "x"})
    assert t["affinity_id"] == str(dyva._affinity_key(MSGS))
    assert t["obj"]["title"] == "how do I route qwen"
    assert t["obj"]["messages"] == MSGS


def test_no_user_messages_is_not_captured():
    # nothing to key on (no affinity id) -> skip
    assert dyva._session_capture_target(
        _Req(Authorization="Bearer user=chris"),
        {"messages": [{"role": "system", "content": "x"}]}) is None


def test_empty_and_junk_bodies_are_not_captured():
    r = _Req(Authorization="Bearer user=chris")
    assert dyva._session_capture_target(r, {}) is None
    assert dyva._session_capture_target(r, None) is None


# ---- the fire-and-forget write: off the critical path ----------------------

def test_capture_dispatches_a_task_and_stores(store):
    async def go():
        uh = dyva._capture_session(_Req(Authorization="Bearer user=chris"), {"messages": MSGS})
        assert uh == dyva._userhash_from_bag({"user": "chris"})   # returns the decision immediately
        assert dyva._capture_tasks                                # write is IN FLIGHT, not done inline
        await asyncio.gather(*list(dyva._capture_tasks))          # let the executor finish
        return uh
    uh = asyncio.run(go())
    stored = dyva._session_get(uh, str(dyva._affinity_key(MSGS)))
    assert stored["messages"] == MSGS


def test_anonymous_capture_is_a_noop(store):
    async def go():
        assert dyva._capture_session(_Req(), {"messages": MSGS}) is None
        assert not dyva._capture_tasks
    asyncio.run(go())
    assert dyva._session_list(dyva._userhash_from_bag({"user": "chris"})) == []


def test_saturated_prefix_dedups_onto_one_row(store):
    n = dyva.AFFINITY_PREFIX_MSGS                                    # per-host setting (4 on Benjamin)
    base = [{"role": "user", "content": f"q{i}"} for i in range(n)]  # fill the prefix exactly

    async def go():
        r = _Req(Authorization="Bearer user=chris")
        dyva._capture_session(r, {"messages": base})
        await asyncio.gather(*list(dyva._capture_tasks))
        more = base + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "qN"}]
        dyva._capture_session(r, {"messages": more})
        await asyncio.gather(*list(dyva._capture_tasks))
        return more
    more = asyncio.run(go())
    uh = dyva._userhash_from_bag({"user": "chris"})
    assert len(dyva._session_list(uh)) == 1                          # same saturated prefix -> one row
    assert dyva._session_get(uh, str(dyva._affinity_key(more)))["messages"] == more   # updated


def test_distinct_prefixes_are_distinct_rows(store):
    async def go():
        r = _Req(Authorization="Bearer user=chris")
        dyva._capture_session(r, {"messages": [{"role": "user", "content": "alpha"}]})
        dyva._capture_session(r, {"messages": [{"role": "user", "content": "beta"}]})
        await asyncio.gather(*list(dyva._capture_tasks))
    asyncio.run(go())
    assert len(dyva._session_list(dyva._userhash_from_bag({"user": "chris"}))) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
