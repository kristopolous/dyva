"""Size-aware band ordering of race candidates — pytest
(`uv run pytest tests/test_ctx_size_routing.py`).

A big request should prefer hosts PROVEN to handle its size: band 0 (max_ok >= need)
before band 1 (>= frac*need) before band 2 (unknown/smaller), falling back to the
incoming reputation order within a band. A bad/unreachable host is never promoted by a
good max_ok, and a small/unknown request is untouched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

U = dyva.TIER_UNKNOWN


def test_size_rank_bands():
    assert dyva._size_rank(100000, 100000, 0.6) == 0
    assert dyva._size_rank(100000, 70000, 0.6) == 1    # 70k >= 60k
    assert dyva._size_rank(100000, 40000, 0.6) == 2    # 40k < 60k
    assert dyva._size_rank(100000, None, 0.6) == 2     # never measured


def test_order_band0_before_band1_before_band2():
    servers = [(U, "unknown", ["m"]), (U, "proven100", ["m"]), (U, "proven70", ["m"])]
    max_ok = {("proven100", "m"): 120000, ("proven70", "m"): 70000}
    order = [h for _, h, _ in dyva._order_by_size(servers, 100000, max_ok, 0.6)]
    assert order == ["proven100", "proven70", "unknown"]


def test_within_band_order_preserved():
    servers = [(U, "a", ["m"]), (U, "b", ["m"]), (U, "z", ["m"])]
    max_ok = {("a", "m"): 120000, ("b", "m"): 120000}          # a,b band 0; z unknown
    order = [h for _, h, _ in dyva._order_by_size(servers, 100000, max_ok, 0.6)]
    assert order[:2] == ["a", "b"]                              # stable within band 0
    assert order[-1] == "z"


def test_bad_host_never_promoted():
    servers = [(dyva.TIER_BAD, "bad", ["m"]), (dyva.TIER_GOOD, "good", ["m"])]
    max_ok = {("bad", "m"): 200000}                            # bad would be band 0
    order = [h for _, h, _ in dyva._order_by_size(servers, 100000, max_ok, 0.6)]
    assert order == ["good", "bad"]                            # bad sinks despite max_ok


def test_small_or_unknown_need_is_noop():
    servers = [(U, "a", ["m"]), (U, "b", ["m"])]
    assert dyva._order_by_size(servers, 0, {("a", "m"): 1}, 0.6) == servers
    assert dyva._order_by_size(servers, 100000, {}, 0.6) == servers
