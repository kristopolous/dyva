"""Per-user session store: a separate table keyed by (userhash, affinity_id)
(`uv run pytest tests/test_user_sessions_store.py -q`).

Mirrors the chats-vs-semindex split (brainstorm/user-account.md): the durable session rows
live in their OWN sqlite DB (SESSIONS_DB), independent of whether the sqlite-vec extension is
available — a vector index over them reuses the sem-index scaffolding separately. Keyed by
(userhash, affinity_id) so repeated turns of one context dedup to a single row updated in
place (no per-turn duplicate flood). Scoped strictly per userhash. UNCAPPED (the 1.0
retention call) — explicitly NOT subject to the global CHATS_KEEP eviction. Delete is a soft
delete (additive flag, row preserved). The global demo chats store is untouched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(dyva, "_sessions_db", None)
    yield


# ---- dedup by (userhash, affinity_id) --------------------------------------

def test_upsert_then_get_roundtrips(store):
    dyva._session_upsert("uh1", "crc-A", "First", {"messages": [{"role": "user", "content": "hi"}]})
    got = dyva._session_get("uh1", "crc-A")
    assert got == {"messages": [{"role": "user", "content": "hi"}]}


def test_same_context_updates_in_place(store):
    # the per-turn flood case: the SAME (userhash, affinity) key written twice is ONE row,
    # updated — not two.
    dyva._session_upsert("uh1", "crc-A", "turn1", {"n": 1})
    dyva._session_upsert("uh1", "crc-A", "turn2", {"n": 2})
    assert len(dyva._session_list("uh1")) == 1
    assert dyva._session_get("uh1", "crc-A") == {"n": 2}


def test_distinct_contexts_are_distinct_rows(store):
    dyva._session_upsert("uh1", "crc-A", "a", {"n": 1})
    dyva._session_upsert("uh1", "crc-B", "b", {"n": 2})
    assert len(dyva._session_list("uh1")) == 2


# ---- per-user scoping ------------------------------------------------------

def test_list_is_scoped_to_one_userhash(store):
    dyva._session_upsert("uh1", "crc-A", "mine", {"n": 1})
    dyva._session_upsert("uh2", "crc-A", "theirs", {"n": 2})
    assert [r["title"] for r in dyva._session_list("uh1")] == ["mine"]
    assert dyva._session_get("uh2", "crc-A") == {"n": 2}       # same affinity, different owner


# ---- soft delete -----------------------------------------------------------

def test_soft_delete_hides_but_preserves(store):
    dyva._session_upsert("uh1", "crc-A", "a", {"n": 1})
    dyva._session_soft_delete("uh1", "crc-A")
    assert dyva._session_list("uh1") == []                     # hidden from list
    assert dyva._session_get("uh1", "crc-A") is None           # and from a normal get
    assert dyva._session_get("uh1", "crc-A", include_deleted=True) == {"n": 1}   # row preserved


# ---- uncapped (NOT the global CHATS_KEEP cap) ------------------------------

def test_store_is_uncapped(store):
    # 250 contexts > CHATS_KEEP (200): if the sessions store wrongly inherited the demo cap,
    # the oldest would be evicted. 1.0 is uncapped, so all 250 survive.
    n = 250
    assert n > dyva.CHATS_KEEP
    for i in range(n):
        dyva._session_upsert("uh1", f"crc-{i}", f"s{i}", {"i": i})
    assert len(dyva._session_list("uh1")) == n


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
