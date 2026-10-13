#!/usr/bin/env python3
"""Prove the gossiper's host_resolved view (resolution.sql) resolves the hard conflict cases
correctly, BEFORE any of this is wired into dyva's live reputation DB.

Run: python3 dyva/gossiper/test_resolution.py   (stdlib only; needs sqlite >= 3.25 for window fns)
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SQL = open(os.path.join(HERE, "resolution.sql")).read()


def fresh():
    db = sqlite3.connect(":memory:")
    db.executescript(SQL)
    return db


def measured(db, host, verdict, ts, model="m"):
    db.execute("INSERT OR REPLACE INTO host_measured(host,model,verdict,ts) VALUES(?,?,?,?)",
               (host, model, verdict, ts))


def attested(db, host, verdict, ts, attester, model="m"):
    db.execute("INSERT INTO hosts_attested(host,model,verdict,attested_at,attester,source)"
               " VALUES(?,?,?,?,?,?)", (host, model, verdict, ts, attester, "src"))


def resolved(db, host, model="m"):
    r = db.execute("SELECT verdict,provenance,attester FROM host_resolved WHERE host=? AND model=?",
                   (host, model)).fetchone()
    return r   # (verdict, provenance, attester) or None


CASES = []   # (name, setup(db), host, expect (verdict, provenance))


def case(name, host, expect):
    def deco(fn):
        CASES.append((name, fn, host, expect))
        return fn
    return deco


@case("A: attested-pass only -> fills the blank", "A", ("pass", "attested"))
def _a(db): attested(db, "A", "pass", "2026-10-13T10:00Z", "1.1.1.1")


@case("B: own pass NEWER than attested pass -> yours wins", "B", ("pass", "measured"))
def _b(db):
    attested(db, "B", "pass", "2026-10-13T09:00Z", "1.1.1.1")
    measured(db, "B", "pass", "2026-10-13T10:00Z")


@case("C: attested pass NEWER than own pass -> fresh peer updates stale own", "C", ("pass", "attested"))
def _c(db):
    measured(db, "C", "pass", "2026-10-13T09:00Z")
    attested(db, "C", "pass", "2026-10-13T10:00Z", "1.1.1.1")


@case("D: attested honeypot, no measured -> trusted skip", "D", ("honeypot", "attested"))
def _d(db): attested(db, "D", "honeypot", "2026-10-13T10:00Z", "1.1.1.1")


@case("E: own pass + LATER... no, EARLIER own pass vs attested honeypot -> honeypot wins (skip, any recency)", "E", ("honeypot", "attested"))
def _e(db):
    measured(db, "E", "pass", "2026-10-13T11:00Z")          # even though own pass is NEWER
    attested(db, "E", "honeypot", "2026-10-13T09:00Z", "9.9.9.9")


@case("F: own HONEYPOT + attested pass (peer tries to un-bad) -> own honeypot sticky", "F", ("honeypot", "measured"))
def _f(db):
    measured(db, "F", "honeypot", "2026-10-13T09:00Z")
    attested(db, "F", "pass", "2026-10-13T12:00Z", "1.1.1.1")   # newer, but can't un-bad your burn


@case("G: two attesters pass, one honeypot -> honeypot wins", "G", ("honeypot", "attested"))
def _g(db):
    attested(db, "G", "pass", "2026-10-13T10:00Z", "1.1.1.1")
    attested(db, "G", "honeypot", "2026-10-13T10:30Z", "9.9.9.9")


def main():
    db = fresh()
    for _n, fn, _h, _e in CASES:
        fn(db)
    db.commit()

    ok = True
    print(f"{'case':<72} got            expect")
    print("-" * 104)
    for name, _fn, host, expect in CASES:
        got = resolved(db, host)
        gp = (got[0], got[1]) if got else (None, None)
        passed = gp == expect
        ok = ok and passed
        print(f"{('PASS' if passed else 'FAIL')} {name:<67} {str(gp):<14} {expect}")

    # --- invalidation: drop the poisoning attester's reports, host recovers -----------------
    print("\n-- invalidation by attester --")
    before = resolved(db, "E")
    db.execute("DELETE FROM hosts_attested WHERE attester=?", ("9.9.9.9",))   # the honeypot reporter
    db.commit()
    after = resolved(db, "E")
    inv_ok = before[:2] == ("honeypot", "attested") and after[:2] == ("pass", "measured")
    ok = ok and inv_ok
    print(f"{'PASS' if inv_ok else 'FAIL'} E before={before[:2]}  -> delete attester 9.9.9.9 -> after={after[:2]} (own pass resurfaces)")
    # G keeps its honeypot because a DIFFERENT attester reported it... invalidate that one too:
    db.execute("DELETE FROM hosts_attested WHERE attester=?", ("9.9.9.9",))
    db.commit()
    g_after = resolved(db, "G")
    g_ok = g_after[:2] == ("pass", "attested")
    ok = ok and g_ok
    print(f"{'PASS' if g_ok else 'FAIL'} G after invalidating 9.9.9.9 -> {g_after[:2]} (the remaining attested pass wins)")

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
