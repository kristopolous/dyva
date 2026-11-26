"""Host-affinity cache — keep a large context resident on the host that already has its KV
warm (`uv run pytest tests/test_host_affinity.py -q`). See brainstorm/host-affinity.md.

A soft, expiring, in-memory reorder ON TOP of the reputation tiers (like the reranker), applied
BEFORE the reranker fold so a 👍/👎 or a live busy signal still overrides it. Units under test:

- the KEY: `_affinity_key(messages)` = crc of the first N user-message contents — stable across
  a thread's rounds (assistant/tool turns and later user turns don't change it).
- the STORE: `affinity_touch(crc, host, size)` records crc -> warm host, size-GATED (small
  prompts never create entries), sliding TTL, LRU-bounded; `affinity_view(crc)` returns the
  match host + foreign reservations over ACTIVE rows only.
- the PLANNER `_affinity_plan(match, reserved, cand_hosts, floor)` (pure): match -> front,
  freshest foreign reservations -> back, but never below `floor` non-demoted candidates.
- `find_servers` integration: a matching crc fronts its host; a foreign reservation sinks one
  (respecting the floor); a 👎/busy on the warm host still wins (voting applied last).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402

NH = dyva._norm_host


@pytest.fixture
def store(monkeypatch):
    # Throwaway in-memory cache + a low size gate so touch() fires on small test prompts,
    # and reset the request-scoped affinity context around each test.
    monkeypatch.setattr(dyva, "_affinity", {})
    monkeypatch.setattr(dyva, "_affinity_derank", {})
    monkeypatch.setattr(dyva, "AFFINITY_MIN_TOKENS", 10)
    monkeypatch.setattr(dyva, "AFFINITY_TTL_SECS", 300)
    monkeypatch.setattr(dyva, "AFFINITY_MAX_ENTRIES", 40)
    tok = dyva._affinity_ctx.set(None)
    yield
    dyva._affinity_ctx.reset(tok)


# ---- the key --------------------------------------------------------------

def _u(txt):
    return {"role": "user", "content": txt}


def _a(txt):
    return {"role": "assistant", "content": txt}


def test_key_none_without_user_messages():
    assert dyva._affinity_key([{"role": "system", "content": "x"}]) is None
    assert dyva._affinity_key([]) is None


def test_key_stable_across_later_turns():
    # The first N user messages are the whole key — appended assistant/tool/user turns beyond
    # N don't move it. This is the property that makes a thread stick to its host round to round.
    base = [_u("one"), _a("r1"), _u("two"), _u("three"), _u("four")]
    grown = base + [_a("r2"), _u("five"), _a("r3"), _u("six")]
    assert dyva._affinity_key(base) == dyva._affinity_key(grown)


def test_key_differs_on_different_prefix():
    assert dyva._affinity_key([_u("alpha")]) != dyva._affinity_key([_u("beta")])


def test_key_uses_only_first_n(monkeypatch):
    monkeypatch.setattr(dyva, "AFFINITY_PREFIX_MSGS", 2)
    # messages 3+ differ but the first 2 user messages match -> same key.
    a = [_u("one"), _u("two"), _u("THREE")]
    b = [_u("one"), _u("two"), _u("different")]
    assert dyva._affinity_key(a) == dyva._affinity_key(b)


# ---- size gate + store ----------------------------------------------------

def test_size_gate():
    assert dyva._affinity_size_ok(dyva.AFFINITY_MIN_TOKENS) is True
    assert dyva._affinity_size_ok(dyva.AFFINITY_MIN_TOKENS - 1) is False
    assert dyva._affinity_size_ok(None) is False


def test_touch_is_size_gated(store):
    dyva.affinity_touch(1234, "http://h:1", 1)       # below the (monkeypatched) 10 gate
    assert dyva.affinity_view(1234) == ("", {})
    dyva.affinity_touch(1234, "http://h:1", 50)      # above -> creates
    m, res = dyva.affinity_view(1234)
    assert m == NH("http://h:1") and res == {}


def test_view_match_vs_foreign(store):
    dyva.affinity_touch(1, "http://a:1", 50)
    dyva.affinity_touch(2, "http://b:1", 50)
    m, res = dyva.affinity_view(1)
    assert m == NH("http://a:1")                      # crc 1's own warm host
    assert res == {NH("http://b:1"): pytest.approx(res[NH("http://b:1")])}  # crc 2 is foreign
    assert NH("http://a:1") not in res                # a host isn't reserved against itself


def test_sliding_ttl_extends(store):
    dyva.affinity_touch(1, "http://a:1", 50)
    _, first = dyva._affinity[1]
    time.sleep(0.01)
    dyva.affinity_touch(1, "http://a:1", 50)
    _, second = dyva._affinity[1]
    assert second > first                              # refreshed, not fixed


def test_expired_ignored_not_counted(store):
    dyva._affinity[9] = (NH("http://a:1"), time.time() - 1)   # already expired
    m, res = dyva.affinity_view(9)
    assert m == "" and res == {}


def test_lru_eviction(store, monkeypatch):
    monkeypatch.setattr(dyva, "AFFINITY_MAX_ENTRIES", 3)
    for i in range(3):
        dyva.affinity_touch(i, f"http://h{i}:1", 50)
    dyva.affinity_touch(0, "http://h0:1", 50)          # re-touch 0 -> now most-recent
    dyva.affinity_touch(99, "http://h99:1", 50)        # over cap -> evict the oldest (crc 1)
    assert 1 not in dyva._affinity
    assert 0 in dyva._affinity and 99 in dyva._affinity


# ---- pure planner ---------------------------------------------------------

def test_plan_match_fronts():
    m, demote = dyva._affinity_plan("ha", {}, ["ha", "hb"], floor=0)
    assert m == "ha" and demote == set()


def test_plan_match_absent_from_candidates():
    m, demote = dyva._affinity_plan("gone", {}, ["ha", "hb"], floor=0)
    assert m == ""


def test_plan_demotes_foreign_reservation():
    m, demote = dyva._affinity_plan("", {"hb": time.time() + 10}, ["ha", "hb"], floor=0)
    assert m == "" and demote == {"hb"}


def test_plan_floor_blocks_demotion():
    # two candidates both reserved, floor 2 -> nothing may be demoted (2 must stay available).
    now = time.time()
    m, demote = dyva._affinity_plan("", {"ha": now + 5, "hb": now + 9}, ["ha", "hb"], floor=2)
    assert demote == set()


def test_plan_keeps_freshest_when_cap_binds():
    # three reserved, floor 2 -> only one may be demoted; keep (demote) the FRESHEST TTL.
    now = time.time()
    reserved = {"ha": now + 1, "hb": now + 100, "hc": now + 50}
    m, demote = dyva._affinity_plan("", reserved, ["ha", "hb", "hc"], floor=2)
    assert demote == {"hb"}                            # hb freshest -> most worth protecting


# ---- find_servers integration --------------------------------------------

HA = "http://10.0.0.1:11434"
HB = "http://10.0.0.2:11434"


@pytest.fixture
def routed(store, monkeypatch):
    monkeypatch.setattr(dyva, "_servers_cache", [
        {"server": HA, "models": ["qwen3.8:27b"], "service": "ollama"},
        {"server": HB, "models": ["qwen3.8:27b"], "service": "ollama"},
    ])
    monkeypatch.setattr(dyva, "_load_votes", lambda: {})
    monkeypatch.setattr(dyva, "rerank_scores", lambda: {})
    monkeypatch.setattr(dyva, "AFFINITY_FLOOR_HOSTS", 0)   # let demotion bite with a 2-host pool


def _order(q="qwen3.8"):
    return [h for _p, h, _m in dyva.find_servers(q, ["completion"])]


def test_find_servers_no_context_is_baseline(routed):
    # no affinity context set -> ordering is untouched (both present).
    order = _order()
    assert HA in order and HB in order


def test_find_servers_match_fronts(routed):
    crc = 777
    dyva._affinity[crc] = (NH(HB), time.time() + 300)
    dyva._affinity_ctx.set((crc, 50_000))
    assert _order()[0] == HB                            # warm host floated to the front


def test_find_servers_foreign_reservation_sinks(routed):
    # HB is warm for SOME OTHER thread; this request (different crc) must sink it to the back,
    # not use it — protecting HB's KV for its own thread.
    dyva._affinity[111] = (NH(HB), time.time() + 300)
    dyva._affinity_ctx.set((222, 50_000))
    order = _order()
    assert HB in order and order[-1] == HB


def test_voting_overrides_affinity(routed, monkeypatch):
    # HB is this thread's warm match (would front) but it's been 👎'd / is busy (rerank -1):
    # voting is applied AFTER affinity, so HB still sinks.
    monkeypatch.setattr(dyva, "rerank_scores", lambda: {NH(HB): -1})
    crc = 777
    dyva._affinity[crc] = (NH(HB), time.time() + 300)
    dyva._affinity_ctx.set((crc, 50_000))
    order = _order()
    assert order[-1] == HB                              # 👎/busy beats the cache guess


# ---- activity-feed match source (_affinity_match_host) --------------------
# The feed's handout/stick lines read this to name the warm host; it must reflect the
# PRE-touch match (the host we actually led with), and be '' when there was no handout.

def test_match_host_none_without_context(store):
    assert dyva._affinity_match_host() == ""


def test_match_host_reads_context_and_store(store):
    dyva._affinity[55] = (NH("http://a:1"), time.time() + 300)
    dyva._affinity_ctx.set((55, 50_000))
    assert dyva._affinity_match_host() == NH("http://a:1")


def test_match_host_empty_when_no_entry(store):
    # context set, but nothing in the store for this crc -> not a handout.
    dyva._affinity_ctx.set((404, 50_000))
    assert dyva._affinity_match_host() == ""


# ---- forget on host failure (the affinity analogue of clear_last) ---------
# When the pinned host stalls/fails, its pin must be dropped so the thread stops LEADING with a
# stuck host every round; the pin re-forms on whichever host next serves (affinity_touch on win).

def test_forget_drops_every_pin_to_host(store):
    dyva.affinity_touch(1, "http://dead:1", 50)        # two threads pinned to the stuck host
    dyva.affinity_touch(2, "http://dead:1", 50)
    dyva.affinity_touch(3, "http://live:1", 50)
    dyva.affinity_forget("http://dead:1")
    assert dyva.affinity_view(1)[0] == ""              # both the stuck host's own pins are gone
    assert dyva.affinity_view(2)[0] == ""
    assert dyva.affinity_view(3)[0] == NH("http://live:1")   # other hosts' pins untouched
    assert NH("http://dead:1") not in dyva.affinity_view(3)[1]   # dead host no longer reserved


def test_forget_ctx_busts_this_requests_pin(store):
    # An affinity-matched request whose host returned no usable response busts ITS OWN pin
    # (this crc), so the next turn of this exact context doesn't get handed back to that host.
    # Other threads' pins to the same host are untouched — the host is fine for everyone else.
    dyva.affinity_touch(1, "http://h:1", 50)           # this request's context, pinned to h
    dyva.affinity_touch(2, "http://h:1", 50)           # another thread, same host
    dyva._affinity_ctx.set((1, 50_000))
    dyva._affinity_forget_ctx()
    assert dyva.affinity_view(1)[0] == ""              # this context's pin is busted
    assert dyva.affinity_view(2)[0] == NH("http://h:1")  # the other thread keeps its pin


def test_forget_ctx_without_context_is_noop(store):
    dyva.affinity_touch(1, "http://h:1", 50)
    dyva._affinity_ctx.set(None)
    dyva._affinity_forget_ctx()                         # no request context -> nothing to bust
    assert dyva.affinity_view(1)[0] == NH("http://h:1")


# ---- per-context derank (the 600s cooldown so the warm banker can't snap back) ------------
# After a context gets no usable answer from a host, that host is sunk for THIS context for a
# cooldown, giving the context time to settle on a new home. Strictly per-crc, self-expiring,
# never a reputation write — the host keeps its (98%!) global standing and its warmth elsewhere.

def test_derank_ctx_records_for_this_context(store):
    dyva._affinity_ctx.set((777, 50_000))
    dyva._affinity_derank_ctx("http://h:1")
    assert dyva._affinity_deranked(777) == {NH("http://h:1")}


def test_derank_scoped_to_crc(store):
    dyva._affinity_ctx.set((777, 50_000))
    dyva._affinity_derank_ctx("http://h:1")
    assert dyva._affinity_deranked(888) == set()       # a different context sees nothing


def test_derank_expires_and_reaps(store):
    dyva._affinity_derank[777] = {NH("http://h:1"): time.time() - 1}   # already expired
    assert dyva._affinity_deranked(777) == set()
    assert 777 not in dyva._affinity_derank             # empty bucket self-heals


def test_derank_ctx_without_context_is_noop(store):
    dyva._affinity_ctx.set(None)
    dyva._affinity_derank_ctx("http://h:1")
    assert dyva._affinity_derank == {}


# ---- operator/auto abandon (stop a committed host) does BOTH halves ------------------------
# When a host is abandoned for a context — the operator hits Stop on its live stream, or an
# auto mid-toolcall drop won't resume — the context must (1) lose its warm pin to that host AND
# (2) sink the host for the cooldown, so the warm banker can't snap the same context back onto
# it next turn. _affinity_abandon_ctx() is the one action both callers use.

def test_abandon_ctx_busts_pin_and_deranks(store):
    dyva.affinity_touch(777, "http://h:1", 50)        # this context pinned to its warm host
    dyva._affinity_ctx.set((777, 50_000))
    dyva._affinity_abandon_ctx("http://h:1")
    assert dyva.affinity_view(777)[0] == ""           # pin busted — stop LEADING with the stopped host
    assert dyva._affinity_deranked(777) == {NH("http://h:1")}   # and sunk for THIS context


def test_abandon_ctx_without_context_is_noop(store):
    dyva._affinity_ctx.set(None)
    dyva._affinity_abandon_ctx("http://h:1")
    assert dyva._affinity == {} and dyva._affinity_derank == {}


def test_find_servers_deranked_host_sinks_to_back(routed):
    # HB just failed THIS context; even though it's a live candidate it must sink to the back for
    # this crc for the cooldown, so the retry tries the other host first (banker warm can't rescue
    # it: find_servers sinks it below the tier, which leads the race sort above warm0).
    crc = 777
    dyva._affinity_ctx.set((crc, 50_000))
    dyva._affinity_derank_ctx(HB)
    order = _order()
    assert HB in order and order[-1] == HB


def test_forget_unknown_host_is_noop(store):
    dyva.affinity_touch(1, "http://live:1", 50)
    dyva.affinity_forget("http://never:1")
    assert dyva.affinity_view(1)[0] == NH("http://live:1")


def test_forget_blank_host_is_noop(store):
    dyva.affinity_touch(1, "http://live:1", 50)
    dyva.affinity_forget("")
    assert dyva.affinity_view(1)[0] == NH("http://live:1")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
