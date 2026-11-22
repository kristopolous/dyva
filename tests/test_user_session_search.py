"""Per-user semantic search over the session store, partitioned by userhash
(`uv run pytest tests/test_user_session_search.py -q`).

Reuses the sem-index machinery (SEMINDEX_DB, the pinned embedder, cosine-via-L2) but in a
SEPARATE, vec0 partition-keyed index (`session_vec`/`session_items`) fed from the durable
user_sessions store — so a user's search only ever scans their own shard (verified:
sqlite-vec v0.1.9 supports partition keys). Built LAZILY at query time (embedding is an
on-demand cost). None is returned when semantic search is unavailable (no embed host /
no sqlite-vec) so a caller can fall back. See brainstorm/user-account.md.

The embedder is mocked with a tiny deterministic bag-of-vocab so the test is offline and the
ranking is predictable.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402


VOCAB = ["routing", "vision", "banjo", "cat"]


async def _fake_embed(session, model, text):
    """Deterministic vector: count of each vocab word. Non-zero fallback so normalize != None."""
    t = (text or "").lower()
    v = [float(t.count(w)) for w in VOCAB]
    if sum(v) == 0.0:
        v = [1e-6, 0.0, 0.0, 0.0]
    return v


@pytest.fixture
def indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(dyva, "_sessions_db", None)
    monkeypatch.setattr(dyva, "SEMINDEX_DB", str(tmp_path / "sem.db"))
    monkeypatch.setattr(dyva, "_sem_db", None)
    monkeypatch.setattr(dyva, "_embed_text", _fake_embed)
    yield


def _seed(userhash, affinity_id, text):
    dyva._session_upsert(userhash, affinity_id, text,
                         {"title": text, "messages": [{"role": "user", "content": text}]})


def test_search_finds_the_relevant_session(indexes):
    _seed("uh1", "aff-route", "how to configure routing and routing fallback")
    _seed("uh1", "aff-vis", "setting up a vision model")
    hits = asyncio.run(dyva._session_search(None, "uh1", "routing"))
    assert hits                                   # not None, not empty
    assert hits[0]["affinity_id"] == "aff-route"  # routing session ranks first


def test_search_is_scoped_to_the_user_shard(indexes):
    _seed("uh1", "aff-a", "routing routing routing")
    _seed("uh2", "aff-b", "routing routing routing")     # another user, same topic
    hits = asyncio.run(dyva._session_search(None, "uh1", "routing"))
    assert [h["affinity_id"] for h in hits] == ["aff-a"]  # only uh1's row, never uh2's


def test_soft_deleted_session_drops_out_of_results(indexes):
    _seed("uh1", "aff-x", "banjo banjo tutorial")
    assert asyncio.run(dyva._session_search(None, "uh1", "banjo"))   # present first
    dyva._session_soft_delete("uh1", "aff-x")
    assert asyncio.run(dyva._session_search(None, "uh1", "banjo")) == []  # pruned on rebuild


def test_empty_query_and_no_user_return_empty(indexes):
    _seed("uh1", "aff-a", "cat cat")
    assert asyncio.run(dyva._session_search(None, "uh1", "")) == []
    assert asyncio.run(dyva._session_search(None, "", "cat")) == []


def test_no_embed_host_returns_none(indexes, monkeypatch):
    async def _no_embed(session, model, text):
        return None
    monkeypatch.setattr(dyva, "_embed_text", _no_embed)
    _seed("uh1", "aff-a", "cat cat")
    assert asyncio.run(dyva._session_search(None, "uh1", "cat")) is None   # fall-back signal


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
