"""graflex `-i latest` resolution — pytest (`uv run pytest tests/test_graflex_latest_session.py -q`).

A session is a /tmp/graflex/{run_ts} directory, run_ts = %Y%m%d%H%M%S (14 digits, so a lexical
max is the chronological latest). `-i latest` is shorthand for "resume the most recent session"
so you don't have to copy the run_ts out of the log. _latest_session finds it; anything that
isn't a 14-digit dir (stray files, other tooling's scratch) is ignored.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import graflex   # noqa: E402


def _mk(base, *names):
    for n in names:
        os.makedirs(os.path.join(base, n), exist_ok=True)


def test_picks_lexically_max_runts(tmp_path):
    base = str(tmp_path)
    _mk(base, "20260818143637", "20261115053325", "20260927162157")
    assert graflex._latest_session(base=base) == "20261115053325"


def test_ignores_non_session_entries(tmp_path):
    base = str(tmp_path)
    _mk(base, "20260818143637", "notatimestamp", "2026", "20261115053325extra")
    # a stray file that looks like a run_ts must not count either
    open(os.path.join(base, "20269999999999"), "w").close()
    assert graflex._latest_session(base=base) == "20260818143637"


def test_none_when_no_sessions(tmp_path):
    base = str(tmp_path)
    _mk(base, "notatimestamp")
    assert graflex._latest_session(base=base) is None


def test_none_when_base_missing(tmp_path):
    assert graflex._latest_session(base=os.path.join(str(tmp_path), "nope")) is None
