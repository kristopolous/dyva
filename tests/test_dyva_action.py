"""Admin do_action endpoint — pytest (`uv run pytest tests/test_dyva_action.py -q`).

do_action is the operator's chat lever: update_sources, read_logs, analyze_workers, and a
read-only query. Every call is admin-gated server-side (X-Admin-Key), so the tool can't do
any of it unless the user is authenticated to the settings panel. These pin the gate, the
dispatch, and the log tail; the SQL safety itself is in test_safe_select.py.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


class _Req:
    def __init__(self, body=None, headers=None):
        self._body = body if body is not None else {}
        self.headers = headers or {}

    async def json(self):
        return self._body


def _run(req):
    import asyncio
    return asyncio.run(dyva.handle_dyva_action(req))


def test_action_requires_admin_when_password_set(monkeypatch):
    monkeypatch.setattr(dyva, "ADMIN_PW", dyva._hash_pw("secret"))
    resp = _run(_Req({"action": "analyze_workers"}))          # no X-Admin-Key
    assert resp.status == 403
    # with the right key it is allowed through the gate
    monkeypatch.setattr(dyva, "_worker_snapshot", lambda: [], raising=False)
    monkeypatch.setattr(dyva, "_conn_count", lambda: 0, raising=False)
    ok = _run(_Req({"action": "analyze_workers"}, {"X-Admin-Key": "secret"}))
    assert ok.status == 200


def test_unknown_action_is_400(monkeypatch):
    monkeypatch.setattr(dyva, "ADMIN_PW", "")                 # open mode -> gate passes
    resp = _run(_Req({"action": "frobnicate"}))
    assert resp.status == 400
    assert "unknown action" in json.loads(resp.body)["error"]


def test_query_action_runs_safe_select(tmp_path, monkeypatch):
    p = str(tmp_path / "stats.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE stats(model TEXT, down INTEGER)")
    c.execute("INSERT INTO stats VALUES('a', 7)")
    c.commit(); c.close()
    monkeypatch.setattr(dyva, "ADMIN_PW", "")
    monkeypatch.setattr(dyva, "STATS_DB", p)
    resp = _run(_Req({"action": "query", "db": "stats", "sql": "SELECT model, down FROM stats"}))
    data = json.loads(resp.body)
    assert data["ok"] and data["rows"] == [["a", 7]]


def test_query_action_rejects_write(tmp_path, monkeypatch):
    p = str(tmp_path / "stats.db")
    c = sqlite3.connect(p); c.execute("CREATE TABLE stats(model TEXT)"); c.commit(); c.close()
    monkeypatch.setattr(dyva, "ADMIN_PW", "")
    monkeypatch.setattr(dyva, "STATS_DB", p)
    resp = _run(_Req({"action": "query", "db": "stats", "sql": "DELETE FROM stats"}))
    assert resp.status == 400
    assert not json.loads(resp.body)["ok"]


def test_read_logs_action_tails(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    (d / "dyva-2026-11-09.log").write_text("".join("line %d\n" % i for i in range(100)))
    monkeypatch.setattr(dyva, "ADMIN_PW", "")
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    resp = _run(_Req({"action": "read_logs", "lines": 5}))
    data = json.loads(resp.body)
    assert data["ok"] and data["lines"] == ["line 95", "line 96", "line 97", "line 98", "line 99"]


def test_read_log_tail_empty_when_no_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    assert dyva._read_log_tail(10) == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
