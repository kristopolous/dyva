"""Chat search behind the search_chats tool — pytest
(`uv run pytest tests/test_chats_search.py -q`).

search_chats lets the user say "I think I talked about X, find it" and get back matching
past conversations, each with a short context snippet and a link to open it. It searches
the SAME server-side chat store the sidebar already lists (same _check_local gate), so it
exposes nothing new. These pin the search over an in-memory chats DB:
  * all query words must appear (title + message text), newest-first, bounded snippet;
  * a chat touched by only some of the words is not a hit.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chats(id TEXT PRIMARY KEY, title TEXT, created REAL, data TEXT NOT NULL)")
    for cid, title, created, msgs in rows:
        data = json.dumps({"id": cid, "title": title, "messages": msgs})
        conn.execute("INSERT INTO chats(id,title,created,data) VALUES(?,?,?,?)",
                     (cid, title, created, data))
    conn.commit()
    return conn


def _msgs(*texts):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": t}
            for i, t in enumerate(texts)]


def test_search_matches_body_text_newest_first(monkeypatch):
    conn = _db([
        ("c1", "Garden plans", 1.0, _msgs("how do I grow tomatoes", "water them daily")),
        ("c2", "Tomato soup recipe", 3.0, _msgs("blend the tomatoes", "add basil")),
        ("c3", "Taxes", 2.0, _msgs("quarterly estimates", "nothing about produce")),
    ])
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)
    hits = dyva._chats_search("tomato", limit=8)
    ids = [h["id"] for h in hits]
    assert ids == ["c2", "c1"]                 # both match, newest (created DESC) first
    assert all(set(h) == {"id", "title", "snippet"} for h in hits)
    assert "tomato" in hits[0]["snippet"].lower()


def test_search_requires_all_words(monkeypatch):
    conn = _db([
        ("c1", "", 2.0, _msgs("basil and tomatoes in the garden")),
        ("c2", "", 1.0, _msgs("tomatoes only, no herbs here")),
    ])
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)
    hits = dyva._chats_search("tomato basil", limit=8)
    assert [h["id"] for h in hits] == ["c1"]    # c2 lacks "basil"


def test_search_empty_query_and_limit(monkeypatch):
    conn = _db([("c%d" % i, "t", float(i), _msgs("widget")) for i in range(5)])
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)
    assert dyva._chats_search("", limit=8) == []
    assert len(dyva._chats_search("widget", limit=2)) == 2   # capped


# ---- endpoint: ?q= on /api/chats --------------------------------------------

import asyncio


class _Req:
    # No embedding host in-test: the ?q= handler's semantic path returns None and falls
    # back to the keyword _chats_search these assertions pin.
    def __init__(self, **q):
        self.query = q
        self.app = {"session": None}


def test_endpoint_q_returns_hits(monkeypatch):
    conn = _db([("c1", "Garden", 1.0, _msgs("tomatoes are red"))])
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)

    async def _no_embed(session, model, text):   # no embedding host -> keyword fallback
        return None
    monkeypatch.setattr(dyva, "_embed_text", _no_embed)

    async def _ok(req):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)

    resp = asyncio.run(dyva.handle_chats_get(_Req(q="tomatoes")))
    data = json.loads(resp.body)
    assert data["hits"] and data["hits"][0]["id"] == "c1"


def test_endpoint_no_q_still_lists(monkeypatch):
    conn = _db([("c1", "Garden", 1.0, _msgs("x"))])
    monkeypatch.setattr(dyva, "_chats_db", conn, raising=False)

    async def _ok(req):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)

    resp = asyncio.run(dyva.handle_chats_get(_Req()))
    data = json.loads(resp.body)
    assert "chats" in data and "hits" not in data


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
