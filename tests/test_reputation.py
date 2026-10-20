"""add_bad tiering — pytest (`uv run pytest tests/test_reputation.py`).

A STRUCTURAL/permanent failure (auth-gated 401/403, missing model, unloadable build) must go
straight to 'bad' from ANY prior state, so load_incompatible() hard-excludes the (host,model)
and it stops being re-probed every request. A TRANSIENT failure keeps the good->maybe_good
leniency. Uses an in-memory host_status table; no real DB.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

H, M = "http://h:1", "m"


def _db(state):
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE host_status(host TEXT, model TEXT, state TEXT, last_good TEXT,
      failure_streak INTEGER DEFAULT 0, failure INTEGER DEFAULT 0, fail_other INTEGER DEFAULT 0,
      last_fail TEXT, last_fail_reason TEXT, is_query INTEGER DEFAULT 0, deleted_at TEXT,
      PRIMARY KEY(host,model))""")
    conn.execute("INSERT INTO host_status(host,model,state) VALUES(?,?,?)", (H, M, state))
    conn.commit()
    return conn


def _state(conn):
    return conn.execute("SELECT state FROM host_status WHERE host=? AND model=?", (H, M)).fetchone()[0]


def test_401_from_good_goes_straight_to_bad(monkeypatch):
    conn = _db("good")
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.add_bad(H, M, reason="smoke: status 401: Unauthorized")
    assert _state(conn) == "bad"


def test_403_from_good_goes_straight_to_bad(monkeypatch):
    conn = _db("good")
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.add_bad(H, M, reason="smoke: status 403: Forbidden")
    assert _state(conn) == "bad"


def test_missing_model_from_good_goes_to_bad(monkeypatch):
    conn = _db("good")
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.add_bad(H, M, reason="smoke: no such model")
    assert _state(conn) == "bad"


def test_transient_from_good_keeps_maybe_good_leniency(monkeypatch):
    conn = _db("good")
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.add_bad(H, M, reason="timed out")          # not structural
    assert _state(conn) == "maybe_good"


def test_any_failure_from_unknown_is_bad(monkeypatch):
    conn = _db("unknown")
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.add_bad(H, M, reason="timed out")
    assert _state(conn) == "bad"


def test_image_http_401_is_structural():
    # The comfy/a1111 image path phrases an auth failure "HTTP 401" (e.g.
    # "/models/checkpoints HTTP 401"), not "status 401" — it must still be
    # recognised as a STRUCTURAL failure so the host is hard-excluded.
    assert dyva._STRUCTURAL_FAIL_RE.search("/models/checkpoints HTTP 401")
    assert dyva._STRUCTURAL_FAIL_RE.search("/models/checkpoints HTTP 403")
    # 404 is NOT auth-structural (wrong path / maybe flaky) and 502 is transient.
    assert not dyva._STRUCTURAL_FAIL_RE.search("/models/checkpoints HTTP 404")
    assert not dyva._STRUCTURAL_FAIL_RE.search("/models/checkpoints HTTP 502")


def test_image_http_401_from_good_goes_straight_to_bad(monkeypatch):
    conn = _db("good")
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.add_bad(H, M, reason="/models/checkpoints HTTP 401")
    assert _state(conn) == "bad"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
