import json
import os

from graflex import hoststore


def test_upsert_inserts_then_updates(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "1.2.3.4:11434", "working",
                     {"host": "1.2.3.4:11434", "models": ["a"]}, "2026-01-01T00:00:00Z")
    row = conn.execute("SELECT service, host, status, payload, checked FROM host").fetchone()
    assert row[0] == "ollama" and row[1] == "1.2.3.4:11434" and row[2] == "working"
    assert json.loads(row[3]) == {"host": "1.2.3.4:11434", "models": ["a"]}
    # update in place, no second row
    hoststore.upsert(conn, "ollama", "1.2.3.4:11434", "notworking",
                     {"host": "1.2.3.4:11434", "reason": "timeout"}, "2026-01-02T00:00:00Z")
    assert conn.execute("SELECT COUNT(*) FROM host").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM host").fetchone()[0] == "notworking"


def test_same_host_two_services_are_two_rows(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "5.6.7.8:80", "working", {"host": "5.6.7.8:80"}, None)
    hoststore.upsert(conn, "vllm", "5.6.7.8:80", "working", {"host": "5.6.7.8:80"}, None)
    assert conn.execute("SELECT COUNT(*) FROM host").fetchone()[0] == 2


def test_concurrent_writers_both_land(tmp_path):
    p = str(tmp_path / "g.db")
    c1, c2 = hoststore.connect(p), hoststore.connect(p)
    hoststore.upsert(c1, "ollama", "a:1", "working", {"host": "a:1"}, None)
    hoststore.upsert(c2, "ollama", "b:2", "working", {"host": "b:2"}, None)  # must not raise 'database is locked'
    assert c1.execute("SELECT COUNT(*) FROM host").fetchone()[0] == 2


def test_rows_and_services(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "a:1", "working", {"host": "a:1"}, "t1")
    hoststore.upsert(conn, "ollama", "b:2", "notworking", {"host": "b:2"}, "t2")
    hoststore.upsert(conn, "ollama", "c:3", None, {"service": "ollama", "host": "c:3"}, None)
    hoststore.upsert(conn, "vllm", "d:4", "working", {"host": "d:4"}, "t3")
    assert hoststore.services(conn) == ["ollama", "vllm"]
    assert hoststore.rows(conn, "ollama", "working") == [("a:1", {"host": "a:1"}, "t1")]
    assert {h for h, _, _ in hoststore.rows(conn, "ollama")} == {"a:1", "b:2", "c:3"}   # _UNSET => all
    assert [h for h, _, _ in hoststore.rows(conn, "ollama", None)] == ["c:3"]           # NULL only


def test_export_shapes_match_current_files(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "b:2", "working", {"host": "b:2", "models": ["m"]}, "2026-01-02")
    hoststore.upsert(conn, "ollama", "a:1", "working", {"host": "a:1", "models": []}, "2026-01-01")
    hoststore.upsert(conn, "ollama", "x:9", "notworking", {"host": "x:9", "reason": "timeout"}, "2026-01-03")
    hoststore.upsert(conn, "ollama", "u:0", None, {"service": "ollama", "host": "u:0"}, None)

    written = {}
    hoststore.export_files(conn,
                           lambda path, data, force=False: written.__setitem__(os.path.basename(path), data),
                           lambda svc, suffix: f"/x/{svc}-{suffix}.json")
    # working = LIST, sorted by (checked, host)
    assert written["ollama-working.json"] == [{"host": "a:1", "models": []}, {"host": "b:2", "models": ["m"]}]
    # notworking = DICT keyed by host
    assert written["ollama-notworking.json"] == {"x:9": {"host": "x:9", "reason": "timeout"}}
    # hosts = LIST of {service, host}, every row for the service
    assert sorted(written["ollama-hosts.json"], key=lambda e: e["host"]) == [
        {"service": "ollama", "host": "a:1"}, {"service": "ollama", "host": "b:2"},
        {"service": "ollama", "host": "u:0"}, {"service": "ollama", "host": "x:9"}]


def test_export_emits_full_set_so_guard_sees_true_count(tmp_path):
    # Export must hand save_fn the FULL current set (so the real _save_json_atomic LOBOTOMY
    # guard compares against the true count, not a partial), never a subset.
    conn = hoststore.connect(str(tmp_path / "g.db"))
    for i in range(10):
        hoststore.upsert(conn, "ollama", f"h:{i}", "working", {"host": f"h:{i}"}, "t")
    seen = {}
    hoststore.export_files(conn,
                           lambda p, d, force=False: seen.__setitem__(p.rsplit("/", 1)[-1], len(d)),
                           lambda s, x: f"{s}-{x}.json")
    assert seen["ollama-working.json"] == 10


def _entry_host(e):
    return e.get("host") or e.get("url", "").split("://")[-1].rstrip("/")


def _load(p, silent=False):
    with open(p) as f:
        return json.load(f)


def test_import_order_preserves_status_and_two_services(tmp_path):
    d = tmp_path
    (d / "ollama-working.json").write_text(json.dumps([{"host": "a:1", "models": ["m"], "checked": "t1"}]))
    (d / "ollama-notworking.json").write_text(json.dumps({"b:2": {"host": "b:2", "reason": "timeout", "checked": "t2"}}))
    # a:1 ALSO appears in hosts (discovery) — must NOT downgrade it to NULL
    (d / "ollama-hosts.json").write_text(json.dumps([{"service": "ollama", "host": "a:1"}, {"service": "ollama", "host": "c:3"}]))
    # same host:port under a second service
    (d / "vllm-working.json").write_text(json.dumps([{"host": "a:1", "models": ["v"], "checked": "t3"}]))

    conn = hoststore.connect(str(d / "g.db"))
    hoststore.import_files(conn, str(d), _load, _entry_host)

    got = dict(conn.execute("SELECT host, status FROM host WHERE service='ollama'").fetchall())
    assert got == {"a:1": "working", "b:2": "notworking", "c:3": None}
    assert conn.execute("SELECT status FROM host WHERE service='vllm' AND host='a:1'").fetchone()[0] == "working"


def test_roundtrip_fidelity(tmp_path):
    d = tmp_path
    working = [{"host": "a:1", "models": ["m"], "checked": "2026-01-01"},
               {"host": "b:2", "models": [], "checked": "2026-01-02"}]
    notworking = {"x:9": {"host": "x:9", "reason": "timeout", "result": "unreachable", "checked": "2026-01-03"}}
    (d / "ollama-working.json").write_text(json.dumps(working))
    (d / "ollama-notworking.json").write_text(json.dumps(notworking))
    (d / "ollama-hosts.json").write_text(json.dumps([{"service": "ollama", "host": "a:1"}]))

    conn = hoststore.connect(str(d / "g.db"))
    hoststore.import_files(conn, str(d), _load, _entry_host)

    out = {}
    hoststore.export_files(conn,
                           lambda p, data, force=False: out.__setitem__(p.rsplit("/", 1)[-1], data),
                           lambda s, x: f"/{s}-{x}.json")
    assert out["ollama-working.json"] == working          # same list, sorted by (checked, host)
    assert out["ollama-notworking.json"] == notworking


def test_import_skips_non_service_hosts_files(tmp_path):
    # known-hosts.json / image-gen-hosts.json live in the same cache dir but are NOT
    # per-service files; importing them (and later re-exporting) would invent bogus
    # services and could clobber the real known-hosts.json. An allowlist excludes them.
    d = tmp_path
    (d / "ollama-working.json").write_text(json.dumps([{"host": "a:1", "checked": "t"}]))
    (d / "known-hosts.json").write_text(json.dumps([{"service": "ollama", "host": "x:9"}]))
    (d / "image-gen-hosts.json").write_text(json.dumps([{"service": "comfyui", "host": "y:8"}]))

    conn = hoststore.connect(str(d / "g.db"))
    hoststore.import_files(conn, str(d), _load, _entry_host, valid_services={"ollama", "comfyui"})

    assert hoststore.services(conn) == ["ollama"]
    assert {h for h, _, _ in hoststore.rows(conn, "ollama")} == {"a:1"}


def test_import_conflict_most_recent_checked_wins(tmp_path):
    # A host legitimately appears in BOTH working and notworking (it worked once, failed
    # later, or vice-versa). The import replays files in a fixed order, but the winner must
    # be the most-recent probe, not whichever file is read last — otherwise ~half the
    # overlap gets the wrong status.
    d = tmp_path
    (d / "ollama-working.json").write_text(json.dumps([
        {"host": "stale-ok:1", "checked": "2026-01-01"},   # older working -> notworking wins
        {"host": "fresh-ok:2", "checked": "2026-02-01"},   # newer working -> stays working
    ]))
    (d / "ollama-notworking.json").write_text(json.dumps({
        "stale-ok:1": {"host": "stale-ok:1", "reason": "timeout", "checked": "2026-02-02"},
        "fresh-ok:2": {"host": "fresh-ok:2", "reason": "timeout", "checked": "2026-01-02"},
    }))
    conn = hoststore.connect(str(d / "g.db"))
    hoststore.import_files(conn, str(d), _load, _entry_host, valid_services={"ollama"})
    got = dict(conn.execute("SELECT host, status FROM host WHERE service='ollama'").fetchall())
    assert got == {"stale-ok:1": "notworking", "fresh-ok:2": "working"}


def test_upsert_bad_writes_bad_hosts_and_never_touches_host(tmp_path):
    # THE guarantee: a bad probe can only ever write to bad_hosts. The good record in
    # `host` (with its model payload) is structurally untouchable by a failed probe.
    conn = hoststore.connect(str(tmp_path / "g.db"))
    good = {"host": "a:1", "models": ["qwen"], "checked": "2026-01-01"}
    hoststore.upsert(conn, "ollama", "a:1", "working", good, "2026-01-01")
    hoststore.upsert_bad(conn, "ollama", "a:1", {"host": "a:1", "reason": "timeout"}, "2026-01-02")
    # host row is byte-for-byte what it was — payload NOT smashed
    hrow = conn.execute("SELECT status, payload FROM host WHERE service='ollama' AND host='a:1'").fetchone()
    assert hrow[0] == "working"
    assert json.loads(hrow[1]) == good
    # the bad observation lives in its own table, keyed (service, host)
    brow = conn.execute("SELECT status, payload, checked FROM bad_hosts WHERE service='ollama' AND host='a:1'").fetchone()
    assert brow[0] == "notworking" and json.loads(brow[1])["reason"] == "timeout" and brow[2] == "2026-01-02"
    # a second bad probe updates in place (latest bad observation), no second row
    hoststore.upsert_bad(conn, "ollama", "a:1", {"host": "a:1", "reason": "refused"}, "2026-01-03")
    assert conn.execute("SELECT COUNT(*) FROM bad_hosts").fetchone()[0] == 1


def test_export_reduce_newer_bad_excludes_host_from_working(tmp_path):
    # host says working (older) but bad_hosts says notworking (newer) -> the latest
    # observation wins: dropped from working.json, listed in notworking.json.
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "a:1", "working", {"host": "a:1", "models": ["m"]}, "2026-01-01")
    hoststore.upsert_bad(conn, "ollama", "a:1", {"host": "a:1", "reason": "timeout"}, "2026-02-01")
    out = {}
    hoststore.export_files(conn, lambda p, d, force=False: out.__setitem__(p.rsplit("/", 1)[-1], d),
                           lambda s, x: f"/{s}-{x}.json")
    assert out["ollama-working.json"] == []
    assert out["ollama-notworking.json"] == {"a:1": {"host": "a:1", "reason": "timeout"}}


def test_export_reduce_newer_working_beats_bad(tmp_path):
    # Recovery: a host went bad (older) then came back working (newer) -> in working.json,
    # dropped from notworking.json. Proves a stale bad row can never suppress a live host.
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert_bad(conn, "ollama", "a:1", {"host": "a:1", "reason": "timeout"}, "2026-01-01")
    hoststore.upsert(conn, "ollama", "a:1", "working", {"host": "a:1", "models": ["m"]}, "2026-02-01")
    out = {}
    hoststore.export_files(conn, lambda p, d, force=False: out.__setitem__(p.rsplit("/", 1)[-1], d),
                           lambda s, x: f"/{s}-{x}.json")
    assert out["ollama-working.json"] == [{"host": "a:1", "models": ["m"]}]
    assert out["ollama-notworking.json"] == {}


def test_stats_counts_derived_view_and_raw_tables(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "w:1", "working", {"host": "w:1"}, "2026-01-01")
    hoststore.upsert(conn, "ollama", "w:2", "working", {"host": "w:2"}, "2026-01-01")
    hoststore.discover(conn, "ollama", "d:3", {"service": "ollama", "host": "d:3"})  # NULL
    # w:2 is demoted NEWER -> reduce drops it from working, into notworking
    hoststore.upsert_bad(conn, "ollama", "w:2", {"host": "w:2", "reason": "timeout"}, "2026-02-01")
    s = hoststore.stats(conn, "ollama")
    assert s == {"working": 1, "notworking": 1, "hosts": 3, "host_rows": 3, "bad_rows": 1}


def test_discover_never_downgrades(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    hoststore.upsert(conn, "ollama", "a:1", "working", {"host": "a:1"}, "t")
    hoststore.discover(conn, "ollama", "a:1", {"service": "ollama", "host": "a:1"})   # must NOT downgrade
    hoststore.discover(conn, "ollama", "z:9", {"service": "ollama", "host": "z:9"})   # new -> NULL
    assert conn.execute("SELECT status FROM host WHERE host='a:1'").fetchone()[0] == "working"
    assert conn.execute("SELECT status FROM host WHERE host='z:9'").fetchone()[0] is None
