"""account_tokens records a Stats row even when the host reports no usage — pytest.

OpenAI-dialect streams (vLLM/LM Studio/SGLang) often don't emit a usage chunk, so
_usage_of() comes back empty and the request used to vanish from Stats entirely
(the "stats stopped recording" case when traffic concentrates on such a host). The
fallback uses our own prompt estimate, stashed on the worker at request time, so a
completed request always writes a row.
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


def _acct(monkeypatch, obj, worker):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(dyva, "STATS_FILE", os.path.join(d, "stats.jsonl"))
    monkeypatch.setattr(dyva, "STATUS_DB", os.path.join(d, "hs.db"))
    monkeypatch.setattr(dyva, "_status_db", None)
    monkeypatch.setattr(dyva, "_broadcast_workers", lambda: asyncio.sleep(0))
    monkeypatch.setitem(dyva._workers, "w1", worker)
    asyncio.run(dyva.account_tokens("w1", "http://89.21.94.208:8001", "m", obj, None))
    if not os.path.exists(dyva.STATS_FILE):
        return []
    return [json.loads(l) for l in open(dyva.STATS_FILE).read().strip().splitlines()]


def test_no_usage_falls_back_to_prompt_estimate(monkeypatch):
    # vLLM-style done chunk: no usage, no eval_count
    rows = _acct(monkeypatch, {"done": True, "done_reason": "stop"},
                 {"wid": "w1", "prompt_est": 3210, "found": None})
    assert len(rows) == 1
    assert rows[0]["up"] == 3210 and rows[0]["down"] == 0   # input recorded, output unknown


def test_real_usage_wins_over_estimate(monkeypatch):
    # when the host DOES report usage, use it (not the estimate)
    rows = _acct(monkeypatch, {"done": True, "usage": {"prompt_tokens": 500, "completion_tokens": 42}},
                 {"wid": "w1", "prompt_est": 3210, "found": None})
    assert len(rows) == 1
    assert rows[0]["up"] == 500 and rows[0]["down"] == 42


def test_no_usage_and_no_estimate_records_nothing(monkeypatch):
    # nothing to go on -> don't write a bogus row
    rows = _acct(monkeypatch, {"done": True}, {"wid": "w1", "found": None})
    assert rows == []
