"""Semantic index over docs + chats (sqlite-vec) — pytest
(`uv run pytest tests/test_semindex.py -q`).

The weak substring search_chats is replaced by embedding search: on a query we embed it
with ONE pinned model (so every stored vector is comparable), lazily "build up" the index
by embedding any new/changed doc section or chat, then KNN over the vec0 table. Docs and
chats share the one index, separated by `kind`. If no host serves the pinned embedding
model the query can't be embedded, and semantic search returns None so the caller falls
back to the old keyword search — nothing breaks when the pool has no embedder.

These pin: the unified corpus + change detection, the plan diff (new/changed/deleted),
KNN ranking + kind filter, the model-change wipe, and the async search end-to-end with a
fake embedder (including the None-means-fall-back contract).
"""
import asyncio
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


@pytest.fixture
def sem(tmp_path, monkeypatch):
    """A fresh, isolated semantic-index DB per test."""
    monkeypatch.setattr(dyva, "SEMINDEX_DB", str(tmp_path / "sem.db"))
    monkeypatch.setattr(dyva, "_sem_db", None, raising=False)
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    db = dyva._get_sem_db()
    assert db is not None, "sqlite-vec must be available for these tests"
    return db


def _unit(v):
    nv = dyva._sem_normalize(v)
    assert nv is not None
    return nv


# ---- corpus + change detection ---------------------------------------------

def test_corpus_items_covers_docs_and_chats(monkeypatch):
    monkeypatch.setattr(dyva, "_docs_index", lambda: [
        {"path": "README.md", "heading": "Intro", "level": 1, "body": "what dyva is"},
        {"path": "README.md", "heading": "", "level": 0, "body": ""},   # empty -> skipped
    ])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chats(id TEXT PRIMARY KEY, title TEXT, created REAL, data TEXT)")
    conn.execute("INSERT INTO chats VALUES('c1','Garden',1.0,?)",
                 (json.dumps({"messages": [{"role": "user", "content": "grow tomatoes"}]}),))
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)

    items = dyva._corpus_items()
    kinds = {(it["kind"], it["ref"]) for it in items}
    assert ("doc", "README.md#Intro") in kinds
    assert ("chat", "c1") in kinds
    assert all(it["hash"] and it["text"] for it in items)
    # the empty doc section is not indexed
    assert not any(it["kind"] == "doc" and it["ref"].endswith("#") for it in items)


def test_plan_detects_new_changed_deleted(sem):
    a = {"kind": "chat", "ref": "c1", "title": "A", "text": "alpha", "hash": "h1"}
    b = {"kind": "doc", "ref": "R#x", "title": "x", "text": "beta", "hash": "h2"}
    dyva._sem_ensure_vec(sem, 2)
    dyva._sem_store(sem, a, _unit([1.0, 0.0]))
    dyva._sem_store(sem, b, _unit([0.0, 1.0]))
    sem.commit()

    live = [
        dict(a),                                   # unchanged
        {"kind": "doc", "ref": "R#x", "title": "x", "text": "BETA2", "hash": "h2b"},  # changed
        {"kind": "chat", "ref": "c2", "title": "C", "text": "gamma", "hash": "h3"},    # new
    ]
    to_delete, to_embed = dyva._sem_plan(sem, live)
    embed_refs = {(it["kind"], it["ref"]) for it in to_embed}
    assert ("doc", "R#x") in embed_refs and ("chat", "c2") in embed_refs
    assert ("chat", "c1") not in embed_refs          # unchanged -> not re-embedded
    assert to_delete == []                            # nothing dropped yet (c1,R#x still live)

    # now drop c1 from the corpus -> it must be scheduled for deletion
    to_delete2, _ = dyva._sem_plan(sem, live[1:])
    assert len(to_delete2) == 1


def test_knn_ranks_by_cosine_and_filters_kind(sem):
    dyva._sem_ensure_vec(sem, 3)
    dyva._sem_store(sem, {"kind": "chat", "ref": "c1", "title": "red",
                          "text": "tomatoes are red", "hash": "h1"}, _unit([1.0, 0.0, 0.0]))
    dyva._sem_store(sem, {"kind": "chat", "ref": "c2", "title": "tax",
                          "text": "quarterly taxes", "hash": "h2"}, _unit([0.0, 0.0, 1.0]))
    dyva._sem_store(sem, {"kind": "doc", "ref": "R#basil", "title": "basil",
                          "text": "basil guide", "hash": "h3"}, _unit([0.9, 0.1, 0.0]))
    sem.commit()

    q = _unit([1.0, 0.05, 0.0])
    allhits = dyva._sem_knn(sem, q, kinds=None, limit=8)
    assert [h["ref"] for h in allhits][:2] == ["c1", "R#basil"]   # cosine order
    assert allhits[0]["score"] >= allhits[1]["score"]

    chats = dyva._sem_knn(sem, q, kinds=("chat",), limit=8)
    assert {h["kind"] for h in chats} == {"chat"}
    assert [h["ref"] for h in chats] == ["c1", "c2"]


def test_model_change_wipes_index(sem):
    dyva._sem_check_model(sem, "model-A")
    dyva._sem_ensure_vec(sem, 2)
    dyva._sem_store(sem, {"kind": "chat", "ref": "c1", "title": "A",
                          "text": "x", "hash": "h1"}, _unit([1.0, 0.0]))
    sem.commit()
    assert sem.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1

    dyva._sem_check_model(sem, "model-B")      # different pinned model -> incomparable
    assert sem.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    assert dyva._sem_meta_get(sem, "dim") is None
    assert dyva._sem_meta_get(sem, "model") == "model-B"


# ---- async search end-to-end -----------------------------------------------

_VECS = {"tomato": [1.0, 0.0, 0.0], "basil": [0.9, 0.1, 0.0], "tax": [0.0, 0.0, 1.0]}


def _fake_embedder(mapping=_VECS, default=(0.0, 1.0, 0.0)):
    async def _embed(session, model, text):
        low = (text or "").lower()
        for k, v in mapping.items():
            if k in low:
                return list(v)
        return list(default)
    return _embed


def _fake_corpus():
    def mk(kind, ref, title, text):
        return {"kind": kind, "ref": ref, "title": title, "text": text,
                "hash": dyva._sem_hash(text)}
    return [
        mk("chat", "c1", "Garden", "tomato plants in the garden"),
        mk("chat", "c2", "Taxes", "quarterly tax estimates"),
        mk("doc", "README.md#Basil", "Basil", "basil growing guide"),
    ]


def test_semantic_search_ranks_chats(sem, monkeypatch):
    monkeypatch.setattr(dyva, "_embed_text", _fake_embedder())
    monkeypatch.setattr(dyva, "_corpus_items", _fake_corpus)
    hits = asyncio.run(dyva._semantic_search(None, "embed-model", "tomato",
                                             kinds=("chat",), limit=8))
    assert hits is not None
    assert [h["ref"] for h in hits] == ["c1", "c2"]        # c1 (tomato) nearest
    assert hits[0]["kind"] == "chat"


def test_semantic_search_ranks_docs(sem, monkeypatch):
    monkeypatch.setattr(dyva, "_embed_text", _fake_embedder())
    monkeypatch.setattr(dyva, "_corpus_items", _fake_corpus)
    hits = asyncio.run(dyva._semantic_search(None, "embed-model", "basil",
                                             kinds=("doc",), limit=8))
    assert hits and hits[0]["ref"] == "README.md#Basil"


def test_semantic_search_none_when_no_embedding_host(sem, monkeypatch):
    async def _no_host(session, model, text):
        return None
    monkeypatch.setattr(dyva, "_embed_text", _no_host)
    monkeypatch.setattr(dyva, "_corpus_items", _fake_corpus)
    hits = asyncio.run(dyva._semantic_search(None, "embed-model", "tomato",
                                             kinds=("chat",), limit=8))
    assert hits is None          # the caller must fall back to keyword search


# ---- endpoints: semantic first, keyword fallback ---------------------------

class _Req:
    def __init__(self, **q):
        self.query = q
        self.app = {"session": None}


def test_chats_endpoint_uses_semantic(sem, monkeypatch):
    monkeypatch.setattr(dyva, "_embed_text", _fake_embedder())
    monkeypatch.setattr(dyva, "_corpus_items", _fake_corpus)

    async def _ok(req):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)

    resp = asyncio.run(dyva.handle_chats_get(_Req(q="tomato")))
    data = json.loads(resp.body)
    assert data["hits"][0]["id"] == "c1"
    assert set(data["hits"][0]) == {"id", "title", "snippet"}


def test_chats_endpoint_falls_back_to_keyword(sem, monkeypatch):
    async def _no_host(session, model, text):
        return None
    monkeypatch.setattr(dyva, "_embed_text", _no_host)

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chats(id TEXT PRIMARY KEY, title TEXT, created REAL, data TEXT NOT NULL)")
    conn.execute("INSERT INTO chats VALUES('k1','Garden',1.0,?)",
                 (json.dumps({"messages": [{"role": "user", "content": "tomato soup"}]}),))
    conn.commit()
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)

    async def _ok(req):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)

    resp = asyncio.run(dyva.handle_chats_get(_Req(q="tomato")))
    data = json.loads(resp.body)
    assert data["hits"][0]["id"] == "k1"      # keyword path still answers


def test_docs_endpoint_uses_semantic(sem, monkeypatch):
    monkeypatch.setattr(dyva, "_embed_text", _fake_embedder())
    monkeypatch.setattr(dyva, "_corpus_items", _fake_corpus)
    monkeypatch.setattr(dyva, "_docs_index", lambda: [])   # keyword path would find nothing

    resp = asyncio.run(dyva.handle_dyva_docs(_Req(q="basil")))
    data = json.loads(resp.body)
    assert data["hits"][0]["path"] == "README.md"
    assert data["hits"][0]["heading"] == "Basil"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
