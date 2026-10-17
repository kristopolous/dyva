"""A stalling host must stop being PREFERRED — pytest.

The bug: a host that became the FIRST sticky ("last-good") host, then failed/
stalled, KEPT the sticky — and the sticky is checked ahead of the reputation
tier, so it stayed first in line and got the exclusive hedge lead every request,
stalling ~TIMEOUT s again each time. The demote to maybe_good is fine and correct;
the fix is that record_verdict now CLEARS the sticky on any non-success verdict so
the host stops being tried first.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402

H, M = "http://216.158.72.165:8000", "qwen3.8-27b"


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(dyva, "STATUS_DB", os.path.join(d, "host-status.db"))
    monkeypatch.setattr(dyva, "_status_db", None)
    monkeypatch.setattr(dyva, "LAST_FILE", os.path.join(d, "last.json"))
    monkeypatch.setattr(dyva, "_last_cache", None)


def test_stall_soft_demotes_and_clears_the_sticky(monkeypatch):
    _fresh(monkeypatch)
    dyva.add_good(H, M)                         # the first sticky won on a real success
    dyva.set_last(M, H, M)
    assert dyva._state_of(H, M) == "good" and dyva.get_last(M) == (H, M)
    dyva.record_verdict(H, M, dyva.timed_out("stream stalled"))
    assert dyva._state_of(H, M) == "maybe_good"   # sometimes-good, re-testable (fine)
    assert dyva.get_last(M) is None               # but NO LONGER first in line


def test_timeout_clears_only_that_pairs_sticky(monkeypatch):
    _fresh(monkeypatch)
    dyva.set_last(M, H, M)
    dyva.set_last("other-model", H, "other-model")   # same host, different model
    dyva.record_verdict(H, M, dyva.timed_out("read timeout"))
    assert dyva.get_last(M) is None
    assert dyva.get_last("other-model") == (H, "other-model")   # unrelated pair untouched


def test_failed_verdict_also_clears_sticky(monkeypatch):
    _fresh(monkeypatch)
    dyva.set_last(M, H, M)
    dyva.record_verdict(H, M, dyva.failed("some hard fault"))
    assert dyva._state_of(H, M) == "bad"
    assert dyva.get_last(M) is None              # the sticky outranks the bad tier; clear it


def test_unreachable_clears_all_sticky_for_host(monkeypatch):
    _fresh(monkeypatch)
    dyva.set_last(M, H, M)
    dyva.set_last("other-model", H, "other-model")
    dyva.record_verdict(H, M, dyva.unreachable_host("conn refused"))
    assert dyva.get_last(M) is None
    assert dyva.get_last("other-model") is None      # host is down -> drop every lead
