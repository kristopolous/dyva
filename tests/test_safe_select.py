"""Read-only SQL executor behind do_action('query') — pytest
(`uv run pytest tests/test_safe_select.py -q`).

The chat's do_action lets an ADMIN run a SELECT against a whitelisted dyva DB (stats,
host_status, chats). "No update" is the rule, and it is enforced structurally, not by
string-matching: the DB is opened mode=ro (any write raises at the engine) AND an
authorizer denies ATTACH (so a query can't reach another file) and PRAGMA; execute() runs
exactly one statement. These pin all of that — a write, a second statement, an ATTACH, or
an unknown DB must FAIL, and the data must be untouched.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


def _mkdb(tmp_path):
    p = str(tmp_path / "stats.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE stats(id INTEGER PRIMARY KEY, model TEXT, down INTEGER)")
    c.executemany("INSERT INTO stats(model,down) VALUES(?,?)",
                  [("a", 10), ("b", 20), ("a", 5)])
    c.commit()
    c.close()
    return p


def test_select_returns_columns_and_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "STATS_DB", _mkdb(tmp_path))
    r = dyva._safe_select("stats",
                          "SELECT model, SUM(down) AS tot FROM stats GROUP BY model ORDER BY tot DESC")
    assert r["columns"] == ["model", "tot"]
    assert r["rows"][0] == ["b", 20]


def test_writes_are_rejected_and_data_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "STATS_DB", _mkdb(tmp_path))
    for bad in ("UPDATE stats SET down=0",
                "DELETE FROM stats",
                "INSERT INTO stats(model,down) VALUES('x',1)",
                "DROP TABLE stats"):
        with pytest.raises(Exception):
            dyva._safe_select("stats", bad)
    assert dyva._safe_select("stats", "SELECT COUNT(*) FROM stats")["rows"][0][0] == 3


def test_second_statement_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "STATS_DB", _mkdb(tmp_path))
    with pytest.raises(Exception):
        dyva._safe_select("stats", "SELECT 1; DROP TABLE stats")


def test_attach_is_denied(tmp_path, monkeypatch):
    # No reaching another file on disk, even read-only.
    monkeypatch.setattr(dyva, "STATS_DB", _mkdb(tmp_path))
    other = str(tmp_path / "other.db")
    sqlite3.connect(other).close()
    with pytest.raises(Exception):
        dyva._safe_select("stats", "ATTACH DATABASE '%s' AS x" % other)


def test_unknown_db_raises(tmp_path):
    with pytest.raises(ValueError):
        dyva._safe_select("secrets", "SELECT 1")


def test_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "STATS_DB", _mkdb(tmp_path))
    assert len(dyva._safe_select("stats", "SELECT * FROM stats", limit=2)["rows"]) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
