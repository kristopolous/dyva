"""Decision-model (/v1/systemone) token accounting — pytest.

Decision hosts are inconsistent about reporting token usage: some return a
standard `usage`, some an ollama-native `prompt_eval_count`, some a `usage` dict
with keys we don't recognise, some nothing at all. `_decision_acct` must yield a
payload `_usage_of` can always read, so a successful decision ALWAYS produces a
Stats row (the "number never moves" bug was gating on a usage key being present
rather than on counts being extractable).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


def _readable(acct):
    p, c = dyva._usage_of(acct)
    return not (p is None and c is None)


def test_standard_usage_passes_through():
    data = {"answers": {}, "usage": {"prompt_tokens": 512, "completion_tokens": 3}}
    assert dyva._decision_acct(data, 999) is data          # keep host's own counts + timing
    assert dyva._usage_of(dyva._decision_acct(data, 999)) == (512, 3)


def test_ollama_native_counts_pass_through():
    data = {"prompt_eval_count": 512, "eval_count": 0}
    assert dyva._decision_acct(data, 999) is data
    assert dyva._usage_of(dyva._decision_acct(data, 999)) == (512, 0)


def test_unrecognized_usage_keys_fall_back_to_estimate():
    # the bug: a usage dict present but unreadable used to bail (no stat written)
    data = {"answers": {}, "usage": {"tokens": 50, "input": 40}}
    acct = dyva._decision_acct(data, 611)
    assert _readable(acct)
    assert dyva._usage_of(acct)[0] == 611                  # our input estimate as `up`


def test_empty_usage_dict_falls_back():
    acct = dyva._decision_acct({"answers": {}, "usage": {}}, 611)
    assert _readable(acct) and dyva._usage_of(acct)[0] == 611


def test_no_usage_at_all_falls_back():
    acct = dyva._decision_acct({"answers": {"q": {"choice": "yes"}}}, 611)
    assert _readable(acct) and dyva._usage_of(acct)[0] == 611


def test_fallback_counts_the_answers_as_down():
    acct = dyva._decision_acct({"answers": {"q": {"choice": "yes"}}}, 611)
    assert dyva._usage_of(acct)[1] > 0                     # answer payload -> completion


def test_zero_estimate_still_readable():
    # even a 0 estimate must be a number, not None, so account_tokens doesn't bail
    acct = dyva._decision_acct({"answers": {}}, 0)
    p, c = dyva._usage_of(acct)
    assert p == 0 and c is not None
