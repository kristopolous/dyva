"""tps is a rolling EWMA, not most-recent (`uv run pytest tests/test_tps_ewma.py -q`).

A single request's tps can be badly under-measured — an agentic/stalling turn makes the
wall-clock fallback report a rate far below the host's true one (it can only UNDER-report,
never over, after the TPS_CEILING clip). Overwriting host_status.tps with that one reading
craters the host's rating on a single stall. So record_perf blends it:

    new_stored = 0.9*old + 0.1*new          (seed raw on the first reading)

One stall is a small nudge that recovers over the next few clean readings, and a host that
genuinely changed speed still converges. ttft is unchanged (most-recent overwrite) — it is a
first-token latency, not a per-token rate, and isn't the number a mid-stream stall distorts.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

H, M = "http://h:1", "m"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE host_status(host TEXT NOT NULL, model TEXT NOT NULL,
      state TEXT NOT NULL, failure_streak INTEGER NOT NULL DEFAULT 0,
      tps REAL, ttft REAL, PRIMARY KEY(host,model))""")
    conn.commit()
    return conn


def _tps(conn):
    return conn.execute("SELECT tps FROM host_status WHERE host=? AND model=?", (H, M)).fetchone()[0]


def _ttft(conn):
    return conn.execute("SELECT ttft FROM host_status WHERE host=? AND model=?", (H, M)).fetchone()[0]


def test_first_tps_reading_is_seeded_raw(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.record_perf(H, M, tps=50.0)
    assert _tps(conn) == 50.0          # NULL -> seed, no blend


def test_second_tps_reading_is_ewma_blended(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.record_perf(H, M, tps=50.0)   # seed
    dyva.record_perf(H, M, tps=10.0)   # a stalled, under-measured reading
    assert _tps(conn) == 46.0          # 0.9*50 + 0.1*10, NOT 10


def test_one_stall_only_nudges_never_replaces(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    for _ in range(5):
        dyva.record_perf(H, M, tps=54.0)   # a steady host
    before = _tps(conn)
    dyva.record_perf(H, M, tps=2.0)        # one agentic-stall reading
    after = _tps(conn)
    assert after > 48.0                    # a nudge, not a crater
    assert after < before                  # but it did move down


def test_ttft_is_still_most_recent_not_blended(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.record_perf(H, M, ttft=1.0)
    dyva.record_perf(H, M, ttft=2.0)
    assert _ttft(conn) == 2.0              # overwrite, not 0.9*1+0.1*2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
