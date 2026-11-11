"""A slow FOFA query must drop ITSELF, never the whole run — pytest
(`uv run pytest graflex/tests/test_fofa_timeout.py -q`).

A read timeout on an authenticated FOFA search means FOFA accepted the request but is
slow-walking THIS query (a heavy multi-facet search, or server-side throttling). It is
NOT a bad session: a stale/expired cookie makes FOFA serve the logged-OUT page, which
`_fetch_web` catches deterministically and fast via its `login-button` signature — a dead
cookie does not time out.

The old timeout branch got this backwards: after two timeouts it probed FOFA's public
root with NO cookie and, if that root was up, declared the cookie "almost certainly
stale" and `SystemExit(3)`'d the entire resumable run. That inference is invalid (the
no-cookie landing page says nothing about whether the authenticated search endpoint
answered a heavy query in time) and the failure mode is the worst possible: one slow
query kills a long survey. The branch also otherwise retried forever.

These pin the corrected contract:
  * a persistently-timing-out query returns None — it drops ONLY itself, and because it
    wrote no cache file a later `--id` run re-fetches it;
  * the retrying is bounded by a cumulative-time budget (TIMEOUT_GIVEUP_S), never infinite;
  * a timeout NEVER hard-stops the run (no SystemExit).
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import graflex    # noqa: E402


def _always_timeout(*a, **k):
    raise requests.exceptions.ReadTimeout("read timed out")


class _TooMany(Exception):
    """Raised by the fake sleep if the retry loop runs away — so a regression to the
    old infinite-retry behaviour FAILS the test instead of hanging it."""


def _make_sleep(slept, cap):
    def _sleep(seconds):
        slept.append(seconds)
        if len(slept) > cap:
            raise _TooMany(f"retry loop unbounded: slept {len(slept)} times")
    return _sleep


def test_persistent_timeout_drops_query_and_is_bounded(monkeypatch):
    slept = []
    monkeypatch.setattr(requests, "get", _always_timeout)
    monkeypatch.setattr(graflex.time, "sleep", _make_sleep(slept, cap=200))

    result = graflex._fetch_web(False, "ollama", 'app="ollama"',
                                run_ts="20260101000000", pname="ollama")

    assert result is None                      # dropped THIS query; the run moves on
    assert len(slept) > 1                       # but it did retry the slow query first
    # bounded by the budget — a single overshooting sleep is at most MAX_BACKOFF
    assert sum(slept) <= graflex.TIMEOUT_GIVEUP_S + graflex.MAX_BACKOFF


def test_persistent_timeout_never_hard_stops_the_run(monkeypatch):
    # The specific bug: a timeout tripped a no-cookie root probe and SystemExit(3)'d the
    # whole run, blaming a "stale cookie". A dead cookie returns the logged-out page; it
    # does not time out. A timeout may only ever drop its own query.
    monkeypatch.setattr(requests, "get", _always_timeout)
    monkeypatch.setattr(graflex.time, "sleep", _make_sleep([], cap=200))

    try:
        result = graflex._fetch_web(False, "ollama", 'app="ollama"',
                                    run_ts="20260101000000", pname="ollama")
    except SystemExit as e:
        raise AssertionError(f"timeout hard-stopped the run with SystemExit({e.code})")

    assert result is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
