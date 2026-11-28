"""The find_servers reputation set-builders are memoised — pytest
(`uv run pytest tests/test_reputation_cache.py`).

find_servers rebuilds load_bad()/load_good()/load_maybe()/load_incompatible()/
load_unreachable() on EVERY request: each is a full host_status scan that allocates a
~19k-element set of f"{host} {model}" strings. Under a burst (200 sequential requests)
that synchronous rebuild pegged the event-loop thread. The sets are pure functions of
host_status content, so they're cached (short TTL via cachetools) and reused between
rebuilds instead of re-derived per request.

These tests pin the behaviour we rely on: a loader returns the SAME object on a second
call (it was cached, not rebuilt), different states don't collide, and once the cache is
cleared the rebuild reflects the current table. The library's TTL expiry is cachetools'
own concern — here we only prove OUR wiring.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _db(rows):
    """In-memory host_status with the columns _LIVE_REAL needs; `rows` = (host, model, state)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE host_status(host TEXT, model TEXT, state TEXT, last_good TEXT, "
        "failure_streak INTEGER DEFAULT 0, last_fail TEXT, last_fail_reason TEXT, "
        "is_query INTEGER DEFAULT 0, deleted_at TEXT, PRIMARY KEY(host, model))")
    conn.executemany(
        "INSERT INTO host_status(host, model, state) VALUES(?,?,?)", rows)
    conn.commit()
    return conn


def _reset_caches():
    # the loaders carry their own module-level caches; start each test clean so a cached
    # result from a previous test's DB can't leak in.
    for fn in (dyva._keys_with_state, dyva.load_incompatible, dyva.load_unreachable):
        c = getattr(fn, "cache", None)
        if c is not None:
            c.clear()


def test_load_bad_is_memoized_between_calls(monkeypatch):
    conn = _db([("http://h1:1", "m", "bad"), ("http://h2:1", "m", "bad")])
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    _reset_caches()

    s1 = dyva.load_bad()
    s2 = dyva.load_bad()
    assert s1 == {"http://h1:1 m", "http://h2:1 m"}       # correct contents
    assert s1 is s2                                        # cached: the SAME object, not rebuilt


def test_states_do_not_collide_in_the_cache(monkeypatch):
    conn = _db([("http://h1:1", "m", "bad"), ("http://h2:1", "m", "good")])
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    _reset_caches()

    bad = dyva.load_bad()
    good = dyva.load_good()
    assert bad == {"http://h1:1 m"}
    assert good == {"http://h2:1 m"}
    assert bad is not good                                 # keyed by state, not one shared entry


def test_cache_rebuild_reflects_current_table(monkeypatch):
    conn = _db([("http://h1:1", "m", "bad")])
    monkeypatch.setattr(dyva, "_status_db", conn, raising=False)
    _reset_caches()

    assert dyva.load_bad() == {"http://h1:1 m"}
    conn.execute("INSERT INTO host_status(host, model, state) VALUES(?,?,?)",
                 ("http://h2:1", "m", "bad"))
    conn.commit()
    _reset_caches()                                        # simulate the TTL window elapsing
    assert dyva.load_bad() == {"http://h1:1 m", "http://h2:1 m"}
