"""Size-aware race ordering — pytest (`uv run pytest tests/test_ctx_size_routing.py`).

A big request should prefer hosts PROVEN to handle its size. The banding feeds the race
sort KEY (not a throwaway pre-sort), where it sits ABOVE warmth/favour but BELOW tier — so
a proven host beats a warm-but-unproven one, yet a bad/unreachable host is never promoted.

  - _size_rank: band one (need, max_ok) pair.
  - _sizeband: band a host's best model, a no-op (constant 0) for small/unknown traffic.
  - _race_sort_key: the race key; pins the precedence (size above warmth, tier primary) that
    a bare pre-sort got wrong — the final _order sort would otherwise override it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def test_size_rank_bands():
    assert dyva._size_rank(100000, 100000, 0.6) == 0
    assert dyva._size_rank(100000, 70000, 0.6) == 1    # 70k >= 60k
    assert dyva._size_rank(100000, 40000, 0.6) == 2    # 40k < 60k
    assert dyva._size_rank(100000, None, 0.6) == 2     # never measured


def test_sizeband_bands_min_over_models_and_noop():
    assert dyva._sizeband(100000, {("h", "m"): 120000}, 0.6, "h", ["m"]) == 0
    assert dyva._sizeband(100000, {("h", "m"): 70000}, 0.6, "h", ["m"]) == 1
    assert dyva._sizeband(100000, {("h", "m"): 40000}, 0.6, "h", ["m"]) == 2
    # min over models: one proven model bands the host best
    assert dyva._sizeband(100000, {("h", "a"): 120000}, 0.6, "h", ["a", "b"]) == 0
    # no-op (constant 0, no reordering) when need<=0 or no sizes known
    assert dyva._sizeband(0, {("h", "m"): 1}, 0.6, "h", ["m"]) == 0
    assert dyva._sizeband(100000, {}, 0.6, "h", ["m"]) == 0


def test_race_key_proven_size_beats_warmth():
    # Finding-1 regression: proven (band 0) but NOT warm must sort before unknown (band 2)
    # but warm. Size is key position 3, warmth position 4 — so size wins.
    proven_cold = dyva._race_sort_key(dyva.TIER_GOOD, 0, 0, 1, 0, 0.0)   # warm0=1 (cold)
    warm_unknown = dyva._race_sort_key(dyva.TIER_GOOD, 0, 2, 0, 0, 0.0)  # warm0=0 (warm)
    assert proven_cold < warm_unknown


def test_race_key_tier_stays_primary():
    # tier outranks size: a bad host proven at size must NOT beat a good host of unknown size
    bad_proven = dyva._race_sort_key(dyva.TIER_BAD, 0, 0, 0, 0, 0.0)
    good_unknown = dyva._race_sort_key(dyva.TIER_GOOD, 0, 2, 1, 0, 0.0)
    assert good_unknown < bad_proven
