"""Recording the two size signals — pytest (`uv run pytest tests/test_ctx_size_record.py`).

  - account_tokens records max_ok on success (incl. the OpenAI prompt_est fallback);
  - a 0-char length drop lowers ctx_limit toward the failed size (mark_overflow), and
    only ever LOWERS it;
  - a later success at that size clears the overflow-derived ceiling (existing
    clear_ctx_limit covers it).

In-memory host_status; account_tokens' tps/broadcast tail is stubbed to no-ops.
"""
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

H, M, WID = "http://h:1", "m", "w1"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE host_status(host TEXT, model TEXT, state TEXT,
      max_ok INTEGER, ctx_limit INTEGER, is_query INTEGER DEFAULT 0, deleted_at TEXT,
      PRIMARY KEY(host,model))""")
    conn.commit()
    return conn


def _ctx_limit(conn):
    return conn.execute("SELECT ctx_limit FROM host_status WHERE host=? AND model=?", (H, M)).fetchone()


def _quiet_tail(monkeypatch, usage, openai, workers):
    monkeypatch.setattr(dyva, "_usage_of", lambda obj: usage)
    monkeypatch.setattr(dyva, "speaks_openai", lambda host: openai)
    monkeypatch.setattr(dyva, "mark_worker_tokens", lambda *a, **k: None)
    monkeypatch.setattr(dyva, "record_perf", lambda *a, **k: None)
    monkeypatch.setattr(dyva, "_write_stat", lambda *a, **k: None)
    monkeypatch.setattr(dyva, "_workers", workers, raising=False)
    async def _anoop(*a, **k):
        return None
    monkeypatch.setattr(dyva, "_broadcast_workers", _anoop)
    monkeypatch.setattr(dyva, "broadcast_activity", _anoop)


def test_account_success_records_max_ok(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    _quiet_tail(monkeypatch, usage=(70000, 100), openai=False, workers={WID: {}})
    asyncio.run(dyva.account_tokens(WID, H, M, {}, None))
    assert dyva._load_max_ok()[(H, M)] == 70000


def test_openai_dialect_uses_prompt_est(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    _quiet_tail(monkeypatch, usage=(None, None), openai=True, workers={WID: {"prompt_est": 70000}})
    asyncio.run(dyva.account_tokens(WID, H, M, {}, None))
    assert dyva._load_max_ok()[(H, M)] == 70000


def test_overflow_lowers_ctx_limit(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.mark_overflow(H, M, 100000)
    assert _ctx_limit(conn)[0] == 100000


def test_overflow_never_raises_a_lower_ceiling(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.mark_ctx_limit(H, M, 32768)
    dyva.mark_overflow(H, M, 100000)
    assert _ctx_limit(conn)[0] == 32768


def test_success_clears_overflow_ceiling(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.mark_overflow(H, M, 100000)
    _quiet_tail(monkeypatch, usage=(100000, 10), openai=False, workers={WID: {}})
    asyncio.run(dyva.account_tokens(WID, H, M, {}, None))
    assert _ctx_limit(conn)[0] is None
