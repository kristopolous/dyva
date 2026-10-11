"""check-working: real-time soft-delete + 1-hour collision cutoff + audit trail.

Mocks the probe and the DB so no network or real cache is touched. Verifies: hosts checked
within the last hour are left alone; the rest are probed oldest-first; alive hosts are
committed to `working` and dead ones soft-demoted to `notworking` IN REAL TIME (as each
probe finishes); every demotion is appended to the date-stamped audit log.
"""
import os
from datetime import datetime, timezone, timedelta

import graflex


def _iso(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def test_check_working_cutoff_order_realtime_commit_and_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(graflex, "CACHE_DIR", str(tmp_path))

    # Working pool: two stale hosts (>1h) and one fresh (<1h). Stored newest-first on
    # purpose, to prove the function re-sorts oldest-first.
    rows = [
        ("h2:11434", {"host": "h2:11434", "models": {"m": {}}}, _iso(hours=2)),    # stale, alive
        ("hf:11434", {"host": "hf:11434", "models": {"m": {}}}, _iso(minutes=10)),  # FRESH -> skipped
        ("h1:11434", {"host": "h1:11434", "models": {"m": {}}}, _iso(hours=5)),     # oldest, dead
    ]
    monkeypatch.setattr(graflex, "_store", lambda: None)
    monkeypatch.setattr(graflex._hoststore, "rows", lambda conn, service, status=None: list(rows))

    async def fake_doorknock(session, entry, service, check_timeout):
        host = entry["host"]
        if host.startswith("h1"):           # the oldest one is dead
            return {"error": "timeout"}, None
        return {"host": host, "models": {"m": {"size": 1}}, "server": host}, "ip"
    monkeypatch.setattr(graflex, "_doorknock", fake_doorknock)

    committed = []   # (host, status) in commit order
    monkeypatch.setattr(graflex, "_store_record",
                        lambda svc, host, status, rec: committed.append((host, status)))
    monkeypatch.setattr(graflex, "export", lambda service=None, force=False: None)

    # workers=1 -> probes run sequentially, so commit order == probe order == oldest-first.
    graflex.check_working("ollama", workers=1, check_timeout=1)

    # fresh host left for next pass
    assert "hf:11434" not in [h for h, _s in committed]
    # oldest-first: h1 (5h, dead) before h2 (2h, alive)
    assert committed == [("h1:11434", "notworking"), ("h2:11434", "working")]

    # the dead host was appended to the date-stamped audit log, in real time
    day = datetime.now().strftime("%Y%m%d")
    log_path = os.path.join(str(tmp_path), "logs", f"prune-{day}.log")
    assert os.path.exists(log_path)
    audit = open(log_path).read()
    assert "h1:11434" in audit and "working->notworking" in audit
    assert "h2:11434" not in audit          # alive host is never in the prune audit
