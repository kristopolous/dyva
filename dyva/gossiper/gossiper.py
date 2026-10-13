#!/usr/bin/env python3
"""gossiper.py — dyva gossiper server (portable, zero-dependency Python reference).

Identical contract to gossiper.php (deploy THAT on PHP/Apache, e.g. 9ol.es/dyva/gossiper).
This exists so the server isn't PHP-locked: anyone can `python3 gossiper.py` to run their
own, and it's the thing to test the client against locally. Design: brainstorm/gossiper.md.

CONTRACT
  GET|POST  /...?source=<sha1 hex>&since=<int cursor>
    POST body (JSON): {"since": <int>, "rows": [{"host","model","smoke","ts"}, ...]}
      - POST ingests `rows`; GET ingests nothing. Both return the delta.
    -> {"ok": true, "now": <cursor to persist>, "rows": [ this source's rows with id>since ]}

  `source` = sha1 of the source URL — the ISOLATION key (a client only sees/writes rows for a
  source whose URL it holds, hence can hash; a non-sha1 source is a 400). The delta cursor is
  the autoincrement `id` (monotonic, skew-free, no "repeats in waves" a windowed LIMIT causes);
  `now` is the max id ACTUALLY returned, never the table max, so a concurrent insert is picked
  up next sync rather than skipped. Store is UNBOUNDED append-only sqlite — no row cap (see
  doc). `src_ip` is recorded per row so a bad contributor can be invalidated later.

  Env: GOSSIPER_DB (sqlite path, default beside this file), GOSSIPER_PORT (default 8787).
"""
import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB = os.environ.get("GOSSIPER_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gossiper.sqlite")
PORT = int(os.environ.get("GOSSIPER_PORT", "8787"))
MAX_BODY = 8 * 1024 * 1024          # defensive request cap (NOT a store cap)
SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.I)
_COLS = ("id", "host", "model", "smoke", "client_ts", "server_ts")


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _db():
    db = sqlite3.connect(DB)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("""CREATE TABLE IF NOT EXISTS gossip (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        source    TEXT NOT NULL,
        host      TEXT NOT NULL,
        model     TEXT,
        smoke     TEXT,
        client_ts TEXT,
        src_ip    TEXT,
        server_ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS gossip_src_id ON gossip (source, id)")
    return db


def exchange(source, since, rows, src_ip):
    """Ingest `rows` (if any) then return this source's rows with id>since. Returns the
    response dict. `now` = max id returned (safe cursor)."""
    db = _db()
    try:
        good = [r for r in rows if isinstance(r, dict) and r.get("host")]
        if good:
            db.executemany(
                "INSERT INTO gossip (source,host,model,smoke,client_ts,src_ip)"
                " VALUES (?,?,?,?,?,?)",
                [(source, str(r["host"]),
                  None if r.get("model") is None else str(r["model"]),
                  None if r.get("smoke") is None else str(r["smoke"]),
                  None if r.get("ts") is None else str(r["ts"]),
                  src_ip) for r in good])
            db.commit()
        cur = db.execute(
            "SELECT id,host,model,smoke,client_ts,server_ts FROM gossip"
            " WHERE source=? AND id>? ORDER BY id ASC", (source, since))
        out = [dict(zip(_COLS, row)) for row in cur.fetchall()]
        now = since
        for r in out:
            if r["id"] > now:
                now = r["id"]
        return {"ok": True, "now": now, "rows": out}
    finally:
        db.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "dyva-gossiper/1"

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self):
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0] if self.client_address else ""

    def _serve(self):
        q = parse_qs(urlparse(self.path).query)
        source = (q.get("source", [""])[0] or "").strip()
        if not SHA1_RE.match(source):
            return self._reply(400, {"ok": False, "error": "source must be a sha1 hex"})
        since = _to_int(q.get("since", ["0"])[0])
        rows = []
        if self.command == "POST":
            n = _to_int(self.headers.get("Content-Length"))
            if n > MAX_BODY:
                return self._reply(413, {"ok": False, "error": "payload too large"})
            raw = self.rfile.read(n) if n > 0 else b""
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._reply(400, {"ok": False, "error": "bad JSON body"})
            if isinstance(body, dict):
                if "since" in body:
                    since = _to_int(body["since"])
                rows = body.get("rows") or []
                if not isinstance(rows, list):
                    rows = []
        try:
            res = exchange(source, since, rows, self._client_ip())
        except Exception as e:
            return self._reply(500, {"ok": False, "error": type(e).__name__})
        self._reply(200, res)

    do_GET = _serve
    do_POST = _serve

    def log_message(self, format, *args):  # noqa: A002 - match BaseHTTPRequestHandler
        pass   # quiet by default


if __name__ == "__main__":
    print(f"gossiper listening on :{PORT}  db={DB}", file=sys.stderr)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
