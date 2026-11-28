"""_LazyTierCandidates: the spike that defers per-tier ordering so a whole-pool race only
sorts tiers it actually reaches — pytest.

Pins the contract the racer relies on: len() and [0] work (it needs the count and the single
current-best), iterating yields the same candidates an eager sort would in best-first tier
order, and — the whole point — a tier is NOT sorted until iteration reaches it.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

# (prio/tier, host, ms) — ms[0] is the within-tier sort value for these tests.
ITEMS = [
    (0,  "u1", [3]),   # unknown tier
    (-2, "g1", [2]),   # good
    (-2, "g2", [1]),   # good (better within good)
    (0,  "u2", [0]),   # unknown
    (-1, "m1", [5]),   # maybe
]
ORDER = lambda it: it[2]   # eager _order stand-in: order within a tier by ms


def _eager(items):
    # what the eager path converges to: (segment, tier) then (load=0, order) — load is 0 here
    return sorted(items, key=lambda it: ((0, it[0]), (0, ORDER(it))))


def _lazy():
    return dyva._LazyTierCandidates(list(ITEMS), ORDER, active=set(), inflight={}, seg={})


def test_len_and_best():
    lz = _lazy()
    assert len(lz) == 5
    assert lz[0] == _eager(ITEMS)[0]          # g2: best tier, best within it


def test_iteration_matches_eager_order():
    assert list(_lazy()) == _eager(ITEMS)


def test_tiers_are_nondecreasing_and_set_preserved():
    out = list(_lazy())
    assert [it[0] for it in out] == sorted(it[0] for it in out)   # best tier first
    assert {it[1] for it in out} == {"u1", "g1", "g2", "u2", "m1"}


def test_lazy_fit_drops_nonfitting_per_tier():
    fit = lambda host, m: m < 5                       # only models < 5 fit the prompt
    items = [(-2, "g1", [2, 9]), (-2, "g2", [9]), (0, "u1", [1])]
    out = list(dyva._LazyTierCandidates(list(items), ORDER, set(), {}, {}, fit=fit))
    hosts = {it[1] for it in out}
    assert "g2" not in hosts                          # its only model (9) doesn't fit -> dropped
    assert hosts == {"g1", "u1"}
    g1 = next(it for it in out if it[1] == "g1")
    assert g1[2] == [2]                               # reduced to the fitting model only


def test_lazy_fit_fallback_when_nothing_fits():
    fit = lambda host, m: False                       # nothing fits the prompt anywhere
    items = [(-2, "g1", [9]), (0, "u1", [8])]
    out = list(dyva._LazyTierCandidates(list(items), ORDER, set(), {}, {}, fit=fit))
    assert {it[1] for it in out} == {"g1", "u1"}      # last resort: every host still tried, not empty


def test_untouched_tiers_are_not_sorted():
    seen = []
    def spy(it):
        seen.append(it[1])
        return it[2]
    lz = dyva._LazyTierCandidates(list(ITEMS), spy, active=set(), inflight={}, seg={})
    # draw only the first tier (the two 'good' hosts); the maybe/unknown tiers must stay unsorted
    first_two = list(itertools.islice(iter(lz), 2))
    assert {it[1] for it in first_two} == {"g1", "g2"}
    assert set(seen) <= {"g1", "g2"}          # order_fn never ran on m1/u1/u2
