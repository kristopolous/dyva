"""SQLite store for graflex's per-service host records.

One row per (service, host); `status` is 'working' | 'notworking' | NULL (discovered,
unchecked); `payload` is the exact JSON record the <service>-*.json files hold. The JSON
files are regenerated from this table (see export_files); the table is the working store,
so a per-host write is an O(1) upsert instead of a whole-file load-mutate-rewrite.

Keyed on (service, host) — NOT host alone: the same host:port legitimately appears under
two services (e.g. gradio+ollama, ollama+vllm), and a host-only key would merge them.
"""
import glob as _glob
import json
import logging as _logging
import os as _os
import sqlite3

_log = _logging.getLogger("graflex")

SCHEMA = """
CREATE TABLE IF NOT EXISTS host (
  service TEXT NOT NULL,
  host    TEXT NOT NULL,
  status  TEXT,
  payload TEXT NOT NULL,
  checked TEXT,
  PRIMARY KEY (service, host)
);
CREATE INDEX IF NOT EXISTS host_svc_status ON host(service, status);

-- A bad probe writes ONLY here, never to `host`, so a failed probe can never overwrite a
-- good record's payload. Identical shape to `host`; status is always 'notworking'. Keyed
-- (service, host): the latest bad observation per host. The export reduce resolves
-- working-vs-bad by `checked` recency across both tables.
CREATE TABLE IF NOT EXISTS bad_hosts (
  service TEXT NOT NULL,
  host    TEXT NOT NULL,
  status  TEXT,
  payload TEXT NOT NULL,
  checked TEXT,
  PRIMARY KEY (service, host)
);
"""

_UNSET = object()


def connect(path):
    """Open (creating if needed) the host DB in WAL mode with a busy timeout so a
    concurrent writer (a second scan, or dyva) waits briefly instead of erroring."""
    conn = sqlite3.connect(path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert(conn, service, host, status, payload, checked):
    """Insert or replace one (service, host) row. O(1); the whole file is never rewritten."""
    conn.execute(
        "INSERT INTO host(service, host, status, payload, checked) VALUES(?,?,?,?,?) "
        "ON CONFLICT(service, host) DO UPDATE SET "
        "status=excluded.status, payload=excluded.payload, checked=excluded.checked",
        (service, host, status, json.dumps(payload, ensure_ascii=False), checked),
    )
    conn.commit()


def upsert_bad(conn, service, host, payload, checked):
    """Record a BAD probe result. Writes ONLY to bad_hosts (status 'notworking') — never to
    `host` — so a failed probe can never smash a good record's payload. Keyed (service, host),
    holding the latest bad observation. The export reduce resolves working-vs-bad by `checked`
    recency, so a stale bad row never suppresses a host that has since come back."""
    conn.execute(
        "INSERT INTO bad_hosts(service, host, status, payload, checked) VALUES(?,?,?,?,?) "
        "ON CONFLICT(service, host) DO UPDATE SET "
        "status=excluded.status, payload=excluded.payload, checked=excluded.checked",
        (service, host, "notworking", json.dumps(payload, ensure_ascii=False), checked),
    )
    conn.commit()


def bad_rows(conn, service):
    """(host, payload_dict, checked) for every bad_hosts row of a service."""
    cur = conn.execute("SELECT host, payload, checked FROM bad_hosts WHERE service=?", (service,))
    return [(h, json.loads(p), c) for h, p, c in cur.fetchall()]


def discover(conn, service, host, payload):
    """Record a DISCOVERED host (status NULL) without ever downgrading one already
    checked: INSERT OR IGNORE, so a host present as working/notworking keeps its status."""
    conn.execute(
        "INSERT OR IGNORE INTO host(service, host, status, payload, checked) VALUES(?,?,?,?,?)",
        (service, host, None, json.dumps(payload, ensure_ascii=False), None))
    conn.commit()


def rows(conn, service, status=_UNSET):
    """(host, payload_dict, checked) for a service. status=_UNSET -> all rows;
    status=None -> only NULL-status (discovered) rows; a string -> that status."""
    if status is _UNSET:
        cur = conn.execute("SELECT host, payload, checked FROM host WHERE service=?", (service,))
    elif status is None:
        cur = conn.execute("SELECT host, payload, checked FROM host WHERE service=? AND status IS NULL", (service,))
    else:
        cur = conn.execute("SELECT host, payload, checked FROM host WHERE service=? AND status=?", (service, status))
    return [(h, json.loads(p), c) for h, p, c in cur.fetchall()]


def services(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT service FROM host ORDER BY service").fetchall()]


def export_files(conn, save_fn, cache_file_fn, service=None, force=False):
    """Regenerate the per-service JSON files from the DB, in the exact shapes consumers
    expect: working = list (sorted by checked,host); notworking = dict keyed by host;
    hosts = list of {service, host}. `save_fn(path, data)` / `cache_file_fn(service, suffix)`
    are injected so the real caller passes graflex's _save_json_atomic (keeping the LOBOTOMY
    guard) and _cache_file. Returns the paths written.

    service=None regenerates every service (migration / standalone export); a service name
    regenerates ONLY that service's three files — the periodic flush during a one-service
    sweep must not re-serialize every other service's whole (up to 32MB) file."""
    written = []
    for svc in ([service] if service is not None else services(conn)):
        final = _reduce_service(conn, svc)
        # working = LIST, sorted by (checked, host) — identical shape to before.
        work = [p for _c, _h, p in sorted(
            ((c, h, p) for h, (st, p, c) in final.items() if st == "working"),
            key=lambda r: (r[0] or "", r[1]))]
        save_fn(cache_file_fn(svc, "working"), work, force=force)
        notwork = {h: p for h, (st, p, _c) in final.items() if st == "notworking"}
        save_fn(cache_file_fn(svc, "notworking"), notwork, force=force)
        hosts = [{"service": svc, "host": h} for h in all_hosts(conn, svc)]
        save_fn(cache_file_fn(svc, "hosts"), hosts, force=force)
        written += [cache_file_fn(svc, s) for s in ("working", "notworking", "hosts")]
    return written


def _reduce_service(conn, service):
    """Final per-host status for a service by LAST-OBSERVATION-WINS across host + bad_hosts,
    ordered by `checked` ascending — the chronological, serial reduce. `host` carries
    working / notworking(legacy) / NULL(discovery); `bad_hosts` carries notworking. A working
    row sets the host working; a notworking row (either table) sets it notworking; the latest
    `checked` decides. Returns {host: (status, payload_dict, checked)}, excluding hosts whose
    only observation is a NULL discovery row. A stale bad row can never suppress a host that
    has a newer working row, and a bad probe can never erase the good payload — it lives in a
    separate table and is only out-dated, never overwritten."""
    seq = []
    for st, h, p, c in conn.execute(
            "SELECT status, host, payload, checked FROM host WHERE service=?", (service,)):
        seq.append((c or "", 0, st, h, p))
    for h, p, c in conn.execute(
            "SELECT host, payload, checked FROM bad_hosts WHERE service=?", (service,)):
        seq.append((c or "", 1, "notworking", h, p))
    # Chronological, stable. On an exact `checked` tie the bad_hosts row (tiebreak 1) is applied
    # after the host row (0), so a same-instant bad observation wins — conservative: a host that
    # just failed is kept out of the routing export.
    seq.sort(key=lambda r: (r[0], r[1]))
    final = {}
    for c, _o, st, h, p in seq:
        if st == "working":
            final[h] = ("working", json.loads(p), c)
        elif st == "notworking":
            final[h] = ("notworking", json.loads(p), c)
        # NULL (discovery) contributes nothing to working/notworking.
    return final


def all_hosts(conn, service):
    """Every host of a service across both tables (deduped). Discovery always inserts a `host`
    row before any probe, so this is normally just `host`; bad_hosts-only hosts (defensive, and
    the case where a demote precedes any discovery row) are unioned in so neither hosts.json nor
    the re-probe candidate pool ever drops one."""
    hs = [r[0] for r in conn.execute("SELECT host FROM host WHERE service=?", (service,))]
    extra = [r[0] for r in conn.execute(
        "SELECT host FROM bad_hosts WHERE service=? AND host NOT IN "
        "(SELECT host FROM host WHERE service=?)", (service, service))]
    return hs + extra


def _import_one(conn, service, status, record, host):
    if not host:
        return 0
    payload = json.dumps(record, ensure_ascii=False)
    checked = record.get("checked") if isinstance(record, dict) else None
    if status is None:
        # discovery row: never overwrite an existing working/notworking status
        conn.execute(
            "INSERT OR IGNORE INTO host(service, host, status, payload, checked) VALUES(?,?,?,?,?)",
            (service, host, None, payload, checked))
    else:
        # A host can appear in both working and notworking (worked once, failed later or
        # vice-versa). On conflict the most-recent probe must win, not whichever file is
        # imported last — so only overwrite when the incoming `checked` is strictly newer
        # (an untimestamped record never displaces a timestamped one).
        conn.execute(
            "INSERT INTO host(service, host, status, payload, checked) VALUES(?,?,?,?,?) "
            "ON CONFLICT(service, host) DO UPDATE SET status=excluded.status, payload=excluded.payload, checked=excluded.checked "
            "WHERE excluded.checked IS NOT NULL AND (host.checked IS NULL OR excluded.checked > host.checked)",
            (service, host, status, payload, checked))
    return 1


def import_files(conn, cache_dir, load_fn, entry_host_fn, valid_services=None):
    """One-time load of the existing <service>-{working,notworking,hosts}.json into the DB.
    Order matters: working then notworking (real status + full payload), then hosts via
    INSERT OR IGNORE so a discovery row never downgrades a host already checked. Bad
    records are logged and skipped, never fatal. Returns the count imported.

    valid_services, when given, is the allowlist of real service names: a file whose parsed
    service is not in it is skipped. This keeps same-suffix-but-not-per-service files —
    known-hosts.json, image-gen-hosts.json — out of the DB, so a later export() can never
    invent a bogus service or clobber the real known-hosts.json."""
    n = 0
    for suffix, status in (("working", "working"), ("notworking", "notworking"), ("hosts", None)):
        for path in sorted(_glob.glob(_os.path.join(cache_dir, f"*-{suffix}.json"))):
            service = _os.path.basename(path)[: -len(f"-{suffix}.json")]
            if valid_services is not None and service not in valid_services:
                continue
            try:
                data = load_fn(path, silent=True)
            except Exception as e:
                _log.warning(f"hoststore import: skip {path}: {e}")
                continue
            items = data.values() if isinstance(data, dict) else data
            for rec in items:
                if not isinstance(rec, dict):
                    continue
                try:
                    n += _import_one(conn, service, status, rec, entry_host_fn(rec))
                except Exception as e:
                    _log.warning(f"hoststore import: skip record in {path}: {e}")
    conn.commit()
    return n
