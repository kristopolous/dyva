"""The one-shot reference-data driver — pytest.

The driver's job is to run every pullable source in one go and be fault-isolated:
one source raising or failing must NOT stop the others, and only a real FAILURE
(not a 'skip') makes the command exit non-zero.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dyva import refresh    # noqa: E402


def _stub(monkeypatch):
    def ok():
        return "ok", "did it"

    def boom():
        raise RuntimeError("upstream down")

    def skip():
        return "skip", "needs a mirror"

    monkeypatch.setattr(refresh, "SOURCES",
                        [("alpha", "a.json", ok),
                         ("boom", "b.json", boom),
                         ("skipme", "c.json", skip)])


def test_one_source_failing_does_not_stop_the_rest(monkeypatch):
    _stub(monkeypatch)
    buf = io.StringIO()
    rc = refresh.run(out=buf)
    text = buf.getvalue()
    assert "alpha   ok" in text                     # ran before the failing one
    assert "boom    fail" in text                    # raised -> reported as fail, not crash
    assert "skipme  skip" in text                    # STILL ran after the exception
    assert rc == 1                                    # a real failure -> non-zero exit


def test_skip_alone_is_success(monkeypatch):
    monkeypatch.setattr(refresh, "SOURCES", [("sizes", "x.json", lambda: ("skip", "manual"))])
    rc = refresh.run(out=io.StringIO())
    assert rc == 0                                    # skip is not a failure


def test_only_filters_sources(monkeypatch):
    _stub(monkeypatch)
    buf = io.StringIO()
    rc = refresh.run(only=["alpha"], out=buf)
    text = buf.getvalue()
    assert "alpha" in text and "boom" not in text     # just the one requested
    assert rc == 0


def test_unknown_source_is_rejected(monkeypatch):
    _stub(monkeypatch)
    buf = io.StringIO()
    rc = refresh.run(only=["nope"], out=buf)
    assert rc == 2
    assert "unknown source" in buf.getvalue()
