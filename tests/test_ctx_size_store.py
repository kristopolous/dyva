"""max_ok persistence: largest prompt a (host,model) has SUCCESSFULLY processed —
pytest (`uv run pytest tests/test_ctx_size_store.py`).

Companion to ctx_limit (the error-derived ceiling). max_ok is the positive,
hard evidence used to band candidates for big requests: it only ever GROWS, and
a falsy value is ignored. In-memory host_status table; no real DB.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

H, M = "http://h:1", "m"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE host_status(host TEXT, model TEXT, state TEXT,
      max_ok INTEGER, ctx_limit INTEGER, is_query INTEGER DEFAULT 0, deleted_at TEXT,
      PRIMARY KEY(host,model))""")
    conn.commit()
    return conn


def _use(monkeypatch, conn):
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)


def test_mark_then_load_roundtrips(monkeypatch):
    _use(monkeypatch, _db())
    dyva.mark_max_ok(H, M, 70000)
    assert dyva._load_max_ok()[(H, M)] == 70000


def test_max_ok_only_grows(monkeypatch):
    _use(monkeypatch, _db())
    dyva.mark_max_ok(H, M, 70000)
    dyva.mark_max_ok(H, M, 40000)
    assert dyva._load_max_ok()[(H, M)] == 70000


def test_falsy_tokens_is_noop(monkeypatch):
    _use(monkeypatch, _db())
    dyva.mark_max_ok(H, M, 0)
    assert (H, M) not in dyva._load_max_ok()
