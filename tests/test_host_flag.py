"""Manual admin host-flagging: general, append-only, reversible WITHOUT erasing
history (`uv run pytest tests/test_host_flag.py -q`).

An admin can flag a host from the dashboard host-detail card. The design the user
pinned, in their words:

  * "honeypot for now, but I'm leaving it intentionally general in case we want to
    revisit it and add fidelity later" -> a `kind` parameter (honeypot today), and a
    generic audit row, so new flag kinds need no schema change. Only kind='honeypot'
    carries a live routing effect right now (the host-wide HONEYPOT_KEY sentinel that
    find_servers drops); other kinds are recorded but don't yet condemn.
  * "unflag needs provenance as well ... we can't just remove the history with an
    unflag" -> flag AND unflag both APPEND a row to host_flag_log. An unflag clears the
    live routing sentinel so the machine can serve again, but it NEVER deletes the
    record that the host was once flagged; the reversal is itself auditable provenance.
  * provenance reason defaults to an auto string (no operator prompt).

In-memory host_status + host_flag_log, dyva._status_db monkeypatched -> no real DB.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

H = "http://1.2.3.4:8080"


def _db():
    conn = sqlite3.connect(":memory:")
    # The columns mark_honeypot writes; production host_status has more (with defaults).
    conn.execute("""CREATE TABLE host_status(host TEXT NOT NULL, model TEXT NOT NULL,
      state TEXT NOT NULL, last_good TEXT, failure_streak INTEGER NOT NULL DEFAULT 0,
      last_fail TEXT, last_fail_reason TEXT, PRIMARY KEY(host, model))""")
    conn.execute("""CREATE TABLE host_flag_log(id INTEGER PRIMARY KEY AUTOINCREMENT,
      host TEXT NOT NULL, kind TEXT NOT NULL, action TEXT NOT NULL,
      reason TEXT, ts TEXT NOT NULL)""")
    conn.commit()
    return conn


def _sentinel(conn, host):
    return conn.execute(
        "SELECT state, last_fail_reason FROM host_status WHERE host=? AND model=?",
        (host, dyva.HONEYPOT_KEY)).fetchall()


def _log(conn):
    return conn.execute(
        "SELECT host, kind, action, reason FROM host_flag_log ORDER BY id").fetchall()


def test_flag_condemns_host_and_logs_the_event(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.flag_host(H, reason="obvious fabricator")
    # live host-wide honeypot sentinel is written (find_servers drops it)
    rows = _sentinel(conn, H)
    assert len(rows) == 1 and rows[0][0] == "bad"
    assert rows[0][1] == "obvious fabricator"          # reason carried as provenance
    # and the manual action is audited
    log = _log(conn)
    assert log == [(H, "honeypot", "flag", "obvious fabricator")]


def test_unflag_clears_sentinel_but_keeps_full_history(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.flag_host(H, reason="bad")
    dyva.unflag_host(H, reason="was a false alarm")
    # the live routing sentinel is gone -> the host can be served again
    assert _sentinel(conn, H) == []
    # but BOTH events survive in the append-only log: the unflag did NOT erase the flag
    log = _log(conn)
    assert len(log) == 2
    assert [r[2] for r in log] == ["flag", "unflag"]
    assert log[1][3] == "was a false alarm"            # the unflag's own provenance


def test_kind_is_general__nonhoneypot_is_audited_without_routing_effect(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.flag_host(H, kind="spam", reason="a future flag kind")
    # a not-yet-wired kind records for audit but does not write the honeypot sentinel
    assert _sentinel(conn, H) == []
    log = _log(conn)
    assert len(log) == 1 and log[0][1] == "spam"


def test_default_reason_is_an_auto_provenance_string(monkeypatch):
    conn = _db()
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    dyva.flag_host(H)                                   # no reason given
    reason = _log(conn)[0][3]
    assert reason and "manual" in reason.lower()       # auto default, no operator prompt


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
