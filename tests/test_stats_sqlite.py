"""Stats SQLite store + aggregation — pytest.

handle_stats now reads from stats.db (indexed GROUP BY) instead of scanning the whole
jsonl. These pin the aggregation output shape and the one-time jsonl backfill, so the
read-cache can't silently diverge from what the page expects.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


def _setup(monkeypatch, tmp_path, recs):
    monkeypatch.setattr(dyva, "STATS_FILE", str(tmp_path / "stats.jsonl"))
    monkeypatch.setattr(dyva, "STATS_DB", str(tmp_path / "stats.db"))
    monkeypatch.setattr(dyva, "_stats_db", None)
    with open(dyva.STATS_FILE, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def test_backfill_and_totals(monkeypatch, tmp_path):
    now = int(time.time())
    _setup(monkeypatch, tmp_path, [
        {"date": now, "host": "1.1.1.1:11434", "model": "qwen3.8-27b", "up": 100, "down": 50, "tps": 20.0},
        {"date": now - 10, "host": "2.2.2.2:8001", "model": "llama3:8b", "up": 200, "down": 10, "tps": None},
        {"date": now - 86400 * 3, "host": "1.1.1.1:11434", "model": "qwen3.8-27b", "up": 5, "down": 0, "tps": None},
    ])
    agg = dyva._stats_aggregate("")        # first call triggers the backfill
    assert agg["totals"] == {"up": 305, "down": 60, "reqs": 3}
    m = {r["name"]: r for r in agg["models"]}
    assert m["qwen3.8-27b"]["up"] == 105 and m["qwen3.8-27b"]["down"] == 50
    assert m["qwen3.8-27b"]["tps"] == 20.0 and m["qwen3.8-27b"]["last"] is not None
    assert m["llama3:8b"]["tps"] is None          # no tps readings -> None, not 0
    h = {r["name"]: r for r in agg["hosts"]}
    assert h["2.2.2.2:8001"]["up"] == 200
    assert len(agg["days"]) == 2                  # today + 3 days ago


def test_family_stack_on_day_buckets(monkeypatch, tmp_path):
    now = int(time.time())
    _setup(monkeypatch, tmp_path, [
        {"date": now, "host": "h1", "model": "qwen3.8-27b", "up": 100, "down": 50, "tps": None},
        {"date": now, "host": "h2", "model": "llama3:8b", "up": 200, "down": 10, "tps": None},
    ])
    day = dyva._stats_aggregate("")["days"][-1]
    assert day["fam"].get("Qwen") == 150 and day["fam"].get("Llama") == 210   # up+down per family


def test_q_filter_matches_model_or_host(monkeypatch, tmp_path):
    now = int(time.time())
    _setup(monkeypatch, tmp_path, [
        {"date": now, "host": "1.1.1.1:11434", "model": "qwen3.8-27b", "up": 100, "down": 50, "tps": None},
        {"date": now, "host": "2.2.2.2:8001", "model": "llama3:8b", "up": 200, "down": 10, "tps": None},
    ])
    assert [r["name"] for r in dyva._stats_aggregate("qwen")["models"]] == ["qwen3.8-27b"]
    assert [r["name"] for r in dyva._stats_aggregate("2.2.2.2")["models"]] == ["llama3:8b"]  # by host
    assert dyva._stats_aggregate("nothingmatches")["totals"] == {"up": 0, "down": 0, "reqs": 0}


def test_dual_write_lands_in_both(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [])
    dyva._write_stat("http://3.3.3.3:11434", "m1", up=7, down=3, tps=5.0)
    # jsonl got a line
    assert "m1" in open(dyva.STATS_FILE).read()
    # and the db aggregates it (scheme stripped on the host)
    agg = dyva._stats_aggregate("")
    assert agg["totals"]["up"] == 7 and agg["totals"]["reqs"] == 1
    assert agg["hosts"][0]["name"] == "3.3.3.3:11434"
