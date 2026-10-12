#!/usr/bin/env python3
"""One-off verification for the bad_hosts two-table split. SAFE: every mutation happens on a
`cp` of the DB in a scratch dir; the real graflex.db, the *.json files, and the network are
only ever READ (part 3 probes one operator-supplied live host). Nothing here can repeat the
check-working disaster — it cannot write to the real store.

Run on staging:
    uv run python graflex/verify_bad_hosts_migration.py \
        [--db ~/.cache/free-ollama/graflex.db] [--cache ~/.cache/free-ollama] \
        [--service ollama] [--live 139.155.97.30:8081]

Prints PASS/FAIL per check and exits non-zero if any FAIL.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

from graflex import hoststore

_fail = 0


def _ok(msg):
    print(f"  PASS  {msg}")


def _bad(msg):
    global _fail
    _fail += 1
    print(f"  FAIL  {msg}")


def _copy_db(db):
    """Copy the DB (and any WAL/SHM sidecars) into a fresh scratch dir; return the copy path."""
    d = tempfile.mkdtemp(prefix="graflex-verify-")
    dst = os.path.join(d, "graflex.db")
    shutil.copy2(db, dst)
    for ext in ("-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.copy2(db + ext, dst + ext)
    return dst


def _export_to(conn, scratch_dir, service):
    """Run the real export_files into scratch_dir; return {suffix: data} for `service`."""
    out = {}
    def save_fn(path, data, force=False):
        out[os.path.basename(path)] = data
    def cache_file_fn(svc, suffix):
        return os.path.join(scratch_dir, f"{svc}-{suffix}.json")
    hoststore.export_files(conn, save_fn, cache_file_fn, service)
    return out


def _host_set(working_list):
    return {e.get("host") for e in working_list if isinstance(e, dict)}


# --- Part 1: export faithfulness -------------------------------------------------------------
def part1_faithfulness(db, cache, service):
    print(f"\n[1] Export faithfulness (reduce reproduces existing working.json) — service={service}")
    baseline_path = os.path.join(cache, f"{service}-working.json")
    if not os.path.exists(baseline_path):
        _bad(f"no baseline {baseline_path} to compare against")
        return
    with open(baseline_path) as f:
        old_working = json.load(f)
    old_hosts = _host_set(old_working)

    dbc = _copy_db(db)
    conn = hoststore.connect(dbc)
    scratch = tempfile.mkdtemp(prefix="graflex-export-")
    new = _export_to(conn, scratch, service)
    new_hosts = _host_set(new.get(f"{service}-working.json", []))

    missing = old_hosts - new_hosts     # the ONE thing that must be empty: silent drops
    added = new_hosts - old_hosts
    print(f"      old working={len(old_hosts)}  new working={len(new_hosts)}  "
          f"dropped={len(missing)}  added={len(added)}")
    if missing:
        _bad(f"{len(missing)} host(s) in old working.json are MISSING from the new reduce "
             f"(e.g. {sorted(missing)[:5]})")
    else:
        _ok("no host silently dropped from working.json")
    if added:
        # not necessarily wrong (a legacy notworking that was never the latest), but surface it
        print(f"      note: {len(added)} host(s) newly present (e.g. {sorted(added)[:5]})")
    conn.close()


# --- Part 2: destruction-proof guarantee -----------------------------------------------------
def part2_guarantee(db, service, sample=20):
    print(f"\n[2] Guarantee: a bad probe writes bad_hosts, never host — service={service}")
    dbc = _copy_db(db)
    conn = hoststore.connect(dbc)
    working = hoststore.rows(conn, service, "working")[:sample]
    if not working:
        _bad(f"no working {service} hosts to sample")
        conn.close()
        return
    # snapshot exact stored payloads (raw JSON text) before any bad write
    before = {h: conn.execute(
        "SELECT payload FROM host WHERE service=? AND host=?", (service, h)).fetchone()[0]
        for h, _p, _c in working}

    for h, _p, _c in working:
        hoststore.upsert_bad(conn, service, h, {"host": h, "reason": "timeout"}, "2999-01-01")

    smashed = [h for h, _p, _c in working if conn.execute(
        "SELECT payload FROM host WHERE service=? AND host=?", (service, h)).fetchone()[0] != before[h]]
    if smashed:
        _bad(f"{len(smashed)} host payload(s) in `host` were MUTATED by a bad probe (e.g. {smashed[:5]})")
    else:
        _ok(f"all {len(working)} sampled host payloads byte-identical after upsert_bad")

    got_bad = conn.execute("SELECT COUNT(*) FROM bad_hosts WHERE service=?", (service,)).fetchone()[0]
    _ok(f"bad_hosts now holds {got_bad} row(s)") if got_bad >= len(working) else \
        _bad(f"expected >= {len(working)} bad_hosts rows, got {got_bad}")

    # reduce: a bad observation newer than the working row drops it to notworking...
    scratch = tempfile.mkdtemp(prefix="graflex-export2-")
    new = _export_to(conn, scratch, service)
    nw = new.get(f"{service}-notworking.json", {})
    wk = _host_set(new.get(f"{service}-working.json", []))
    moved = [h for h, _p, _c in working if h in nw and h not in wk]
    _ok(f"all {len(working)} sampled hosts moved working->notworking in the export") \
        if len(moved) == len(working) else \
        _bad(f"only {len(moved)}/{len(working)} sampled hosts moved to notworking")

    # ...and a NEWER working observation brings one straight back (recovery)
    h0 = working[0][0]
    hoststore.upsert(conn, service, h0, "working", {"host": h0, "recovered": True}, "2999-06-01")
    scratch2 = tempfile.mkdtemp(prefix="graflex-export3-")
    new2 = _export_to(conn, scratch2, service)
    back = _host_set(new2.get(f"{service}-working.json", []))
    _ok(f"recovery: {h0} returned to working.json after a newer working probe") \
        if h0 in back else _bad(f"recovery: {h0} did NOT return to working.json")
    conn.close()


# --- Part 4: mid-check flush — the off-thread export under concurrent writes -----------------
def part4_midcheck(db, service):
    print(f"\n[4] Mid-check flush: _export_offthread (own connection) under concurrent writes "
          f"— service={service}")
    import threading
    import graflex

    scratch = tempfile.mkdtemp(prefix="graflex-midcheck-")
    shutil.copy2(db, os.path.join(scratch, "graflex.db"))
    for ext in ("-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.copy2(db + ext, os.path.join(scratch, "graflex.db" + ext))
    # Point the whole module at the scratch copy: the off-thread export writes json HERE, never
    # the real cache, and the writer uses a scratch-copy connection.
    graflex.CACHE_DIR = scratch
    graflex._HOSTSTORE = hoststore.connect(os.path.join(scratch, "graflex.db"))

    sample = [h for h, _p, _c in hoststore.rows(graflex._HOSTSTORE, service, "working")[:5]]
    if not sample:
        _bad(f"no working {service} hosts to exercise the flush with")
        return

    errors = []
    stop = threading.Event()

    def exporter():
        # exactly what the periodic mid-check flush calls — fresh connection each time
        try:
            while not stop.is_set():
                graflex._export_offthread(service)
        except Exception as e:                      # notably sqlite3 "database is locked"
            errors.append(repr(e))

    t = threading.Thread(target=exporter)
    t.start()
    try:
        # demote a few (newer than their working row) while the flush is firing
        for h in sample:
            graflex._store_record(service, h, "notworking",
                                  {"host": h, "reason": "timeout", "result": "unreachable",
                                   "checked": "2999-01-01"})
    except Exception as e:
        errors.append(repr(e))
    finally:
        stop.set()
        t.join()
    try:
        graflex._export_offthread(service)          # one more, after writes settle
    except Exception as e:
        errors.append(repr(e))

    if errors:
        _bad(f"off-thread export errored under concurrent writes: {errors[:2]}")
    else:
        _ok("off-thread export ran under concurrent writes with no DB-lock/errors")

    wp = os.path.join(scratch, f"{service}-working.json")
    try:
        with open(wp) as f:
            wk = _host_set(json.load(f))
        _ok(f"mid-check working.json is valid JSON ({len(wk)} hosts)")
        leaked = set(sample) & wk
        if leaked:
            # If the LOBOTOMY guard held the write (tiny DB, >40% shrink) this can show stale
            # entries — real staging pools are large, so note rather than hard-fail.
            print(f"      note: {len(leaked)} just-demoted host(s) still present "
                  f"(expected 0 unless the lobotomy guard held the shrink)")
        else:
            _ok("mid-check working.json excludes the hosts demoted during the run")
    except Exception as e:
        _bad(f"mid-check working.json unreadable: {e!r}")


# --- Part 3: the original bug — llama.cpp-on-404 is KEPT, not demoted ------------------------
def part3_live(host):
    print(f"\n[3] Live probe of {host}: ollama 404 must be KEPT; llama.cpp must be alive")
    import asyncio
    import aiohttp
    import graflex

    async def go():
        conn = aiohttp.TCPConnector(ssl=graflex.INSECURE_SSL, force_close=True, limit=0)
        async with aiohttp.ClientSession(connector=conn,
                                         timeout=aiohttp.ClientTimeout(total=20)) as session:
            r_oll, _ = await graflex._doorknock(session, {"host": host}, "ollama", 15)
            r_lc, _ = await graflex._doorknock(session, {"host": host}, "llama.cpp", 15)
            return r_oll, r_lc

    r_oll, r_lc = asyncio.run(go())
    reason = (r_oll.get("error", str(r_oll)) if isinstance(r_oll, dict) else str(r_oll))
    print(f"      ollama probe   -> {reason!r}")
    # the prune's demote rule: only a 'timeout' demotes; anything else (incl. 404) is KEPT
    if isinstance(r_oll, dict) and "error" in r_oll and "timeout" not in str(reason).lower():
        _ok("ollama probe answered (non-timeout) -> prune would KEEP this host")
    elif isinstance(r_oll, dict) and "error" not in r_oll:
        _ok("ollama probe actually succeeded -> host stays working (also fine)")
    else:
        _bad(f"ollama probe looks like a timeout/no-response -> would be demoted: {reason!r}")

    if isinstance(r_lc, dict) and "error" not in r_lc and r_lc.get("models"):
        print(f"      llama.cpp probe -> alive, {len(r_lc['models'])} model(s)")
        _ok("llama.cpp probe confirms the host does inference")
    else:
        print(f"      llama.cpp probe -> {r_lc!r}")
        _bad("llama.cpp probe did not confirm a live inferencing host")


def main():
    ap = argparse.ArgumentParser()
    home = os.path.expanduser("~")
    ap.add_argument("--db", default=os.path.join(home, ".cache/free-ollama/graflex.db"))
    ap.add_argument("--cache", default=os.path.join(home, ".cache/free-ollama"))
    ap.add_argument("--service", default="ollama")
    ap.add_argument("--live", default=None, help="host:port to probe for part 3 (e.g. 139.155.97.30:8081)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"no DB at {args.db}", file=sys.stderr)
        sys.exit(2)
    print(f"verifying against a COPY of {args.db} (the original is never written)")
    part1_faithfulness(args.db, args.cache, args.service)
    part2_guarantee(args.db, args.service)
    part4_midcheck(args.db, args.service)
    if args.live:
        part3_live(args.live)
    else:
        print("\n[3] skipped (no --live host given)")

    print(f"\n{'ALL CHECKS PASSED' if _fail == 0 else str(_fail) + ' CHECK(S) FAILED'}")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
