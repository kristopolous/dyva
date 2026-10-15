"""Gossiper CLIENT logic (dyva side of brainstorm/gossiper.md). The SERVER + its
resolution SQL are proven separately in dyva/gossiper/test_resolution.py; this proves
the dyva client that extracts outgoing smoke verdicts, applies peer attestations into
the quarantined hosts_attested table, and folds them into find_servers:

  * outgoing = our pass/honeypot verdicts, windowed, scoped to a source's host set
  * apply    = peer rows -> hosts_attested (attester = the server's src_ip)
  * router   = attested honeypot -> host-wide skip; attested pass -> fill-blank lead;
               our own measured verdict always wins (a peer can't un-bad our burn)
  * sync     = one POST per source channel, leak-bounded grouping, cursor advance

Offline: the transport is stubbed, so nothing hits the network.
Run: `.venv/bin/python -m pytest tests/test_gossiper_client.py -q`
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh, isolated host_status DB (with the gossiper tables) per test."""
    monkeypatch.setattr(dyva, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(dyva, "STATUS_DB", str(tmp_path / "host_status.db"))
    monkeypatch.setattr(dyva, "_status_db", None)
    conn = dyva._get_db()
    yield conn
    try:
        conn.close()
    except Exception:
        pass
    monkeypatch.setattr(dyva, "_status_db", None)


def _measure_pass(host, model, ts="2026-10-13T10:00:00Z"):
    # a real endpoint that answered the canned smoke probe straight
    dyva._get_db().execute(
        "INSERT INTO host_status(host,model,state,smoke_date,fail_smoke,updated_at) "
        "VALUES(?,?,?,?,0,?)", (host, model, "good", ts, ts))
    dyva._get_db().commit()


def _measure_honeypot(host, ts="2026-10-13T10:00:00Z"):
    dyva._get_db().execute(
        "INSERT INTO host_status(host,model,state,last_fail,updated_at) "
        "VALUES(?,?,?,?,?)", (host, dyva.HONEYPOT_KEY, "bad", ts, ts))
    dyva._get_db().commit()


# --------------------------------------------------------------------------- channel

def test_channel_is_sha1_of_normalized_url():
    import hashlib
    url = "https://raw.githubusercontent.com/x/y/data.json"
    assert dyva._gossiper_channel(url) == hashlib.sha1(url.encode()).hexdigest()
    # scheme is implied before hashing, so bare == https form collide on one channel
    assert dyva._gossiper_channel("9ol.es/feed.json") == dyva._gossiper_channel("https://9ol.es/feed.json")


def test_source_urls_includes_builtins(db):
    urls = dyva._source_urls()
    assert "spider" in urls and urls["spider"].startswith("http")


# ----------------------------------------------------------------- outgoing extraction

def test_outgoing_emits_pass_and_hostwide_honeypot(db):
    _measure_pass("http://a:11434", "qwen3.8:27b")
    _measure_honeypot("http://b:11434")
    hosts = {"http://a:11434", "http://b:11434"}
    out = dyva._gossiper_outgoing(hosts, None)
    verd = {(h, m, v) for h, m, v, _ts in out}
    assert ("http://a:11434", "qwen3.8:27b", "pass") in verd
    assert ("http://b:11434", "", "honeypot") in verd          # honeypot is host-wide (model='')


def test_outgoing_excludes_hosts_outside_the_source_set(db):
    # the leak boundary: a host of another source is never posted to this channel
    _measure_pass("http://a:11434", "m")
    _measure_pass("http://other:11434", "m")
    out = dyva._gossiper_outgoing({"http://a:11434"}, None)
    assert {h for h, _m, _v, _ts in out} == {"http://a:11434"}


def _measure_canned(host, model="m", ts="2026-10-13T10:00:00Z"):
    # fail_smoke=1 "smoked:canned" — a form of honeypot (older per-model recording)
    dyva._get_db().execute(
        "INSERT INTO host_status(host,model,state,smoke_date,fail_smoke,updated_at) "
        "VALUES(?,?,?,?,1,?)", (host, model, "bad", ts, ts))
    dyva._get_db().commit()


def test_outgoing_emits_canned_as_hostwide_honeypot(db):
    # a canned reply IS a honeypot: shared host-wide, not skipped (user: "it's a form of honeypot")
    _measure_canned("http://c:11434", "llama3:latest")
    out = dyva._gossiper_outgoing({"http://c:11434"}, None)
    assert {(h, m, v) for h, m, v, _ts in out} == {("http://c:11434", "", "honeypot")}


def test_outgoing_canned_host_is_never_advertised_pass(db):
    # same host: one model answered straight, another was canned -> the host is unsafe,
    # so it is a honeypot host-wide and NO pass row escapes for it.
    _measure_pass("http://x:11434", "good-model")
    _measure_canned("http://x:11434", "bad-model")
    out = dyva._gossiper_outgoing({"http://x:11434"}, None)
    assert {(h, m, v) for h, m, v, _ts in out} == {("http://x:11434", "", "honeypot")}


def test_outgoing_window_excludes_rows_at_or_before_since(db):
    _measure_pass("http://a:11434", "m", ts="2026-10-13T09:00:00Z")
    # a since AFTER the row's updated_at -> nothing new to send
    assert dyva._gossiper_outgoing({"http://a:11434"}, "2026-10-13T12:00:00Z") == []
    # a since BEFORE it -> it's in the window
    assert len(dyva._gossiper_outgoing({"http://a:11434"}, "2026-10-13T06:00:00Z")) == 1


# ----------------------------------------------------------------------------- apply

def test_apply_inserts_attested_with_attester_from_src_ip(db):
    rows = [{"host": "http://p:11434", "model": "m", "smoke": "pass",
             "client_ts": "2026-10-13T10:00:00Z", "src_ip": "9.9.9.9"},
            {"host": "http://h:11434", "model": "", "smoke": "honeypot",
             "client_ts": "2026-10-13T10:00:00Z", "src_ip": "9.9.9.9"}]
    assert dyva._gossiper_apply(rows, "chan") == 2
    assert dyva.load_attested_honeypots() == {"http://h:11434"}
    assert dyva.load_attested_pass() == {"http://p:11434 m"}
    (att,) = dyva._get_db().execute(
        "SELECT DISTINCT attester FROM hosts_attested").fetchone()
    assert att == "9.9.9.9"


def test_apply_rejects_malformed_rows(db):
    bad = [{"model": "m", "smoke": "pass"},            # no host
           {"host": "x", "smoke": "bogus"},            # not a known verdict
           "not a dict"]
    assert dyva._gossiper_apply(bad, "chan") == 0
    assert dyva.load_attested_pass() == set()


def test_attester_invalidation_removes_the_poisoned_report(db):
    dyva._gossiper_apply([{"host": "http://h:11434", "model": "", "smoke": "honeypot",
                           "client_ts": "t", "src_ip": "6.6.6.6"}], "chan")
    assert dyva.load_attested_honeypots() == {"http://h:11434"}
    dyva._get_db().execute("DELETE FROM hosts_attested WHERE attester=?", ("6.6.6.6",))
    dyva._get_db().commit()
    assert dyva.load_attested_honeypots() == set()   # host recovers in the view


# ---------------------------------------------------------------------------- cursor

def test_cursor_roundtrip(db):
    assert dyva._gossiper_cursor("chan") == (None, 0)
    dyva._gossiper_set_cursor("chan", "2026-10-13T10:00:00Z", 42)
    assert dyva._gossiper_cursor("chan") == ("2026-10-13T10:00:00Z", 42)
    dyva._gossiper_set_cursor("chan", "2026-10-13T11:00:00Z", 99)   # upsert
    assert dyva._gossiper_cursor("chan") == ("2026-10-13T11:00:00Z", 99)


# ----------------------------------------------------------------------- find_servers

def _pool(monkeypatch, *hosts):
    recs = [{"server": h, "source": "spider", "service": "ollama", "models": [m]}
            for h, m in hosts]
    monkeypatch.setattr(dyva, "load_servers", lambda: recs)
    monkeypatch.setattr(dyva, "_last_cache", {})
    monkeypatch.setattr(dyva, "EXPLORE_MODE", False)


def test_find_servers_skips_attested_honeypot(db, monkeypatch):
    _pool(monkeypatch, ("http://trap:11434", "qwen3.8:27b"))
    dyva._gossiper_apply([{"host": "http://trap:11434", "model": "", "smoke": "honeypot",
                           "client_ts": "t", "src_ip": "9.9.9.9"}], "chan")
    res = dyva.find_servers("qwen3.8")
    prio = {h: p for p, h, _m in res}
    assert prio["http://trap:11434"] == dyva.TIER_BAD   # host-wide skip, raced dead-last


def test_find_servers_attested_pass_lifts_unknown_above_cold(db, monkeypatch):
    _pool(monkeypatch,
          ("http://lead:11434", "qwen3.8:27b"),      # peer says pass
          ("http://cold:11434", "qwen3.8:27b"))      # nobody knows anything
    dyva._gossiper_apply([{"host": "http://lead:11434", "model": "qwen3.8:27b",
                           "smoke": "pass", "client_ts": "t", "src_ip": "9.9.9.9"}], "chan")
    prio = {h: p for p, h, _m in dyva.find_servers("qwen3.8")}
    assert prio["http://lead:11434"] == dyva.TIER_MAYBE   # fill-blank positive lead
    assert prio["http://cold:11434"] == dyva.TIER_UNKNOWN
    assert prio["http://lead:11434"] < prio["http://cold:11434"]   # lead races first


def test_find_servers_attested_pass_does_not_revive_our_measured_bad(db, monkeypatch):
    _pool(monkeypatch, ("http://known-bad:11434", "qwen3.8:27b"))
    dyva.add_bad("http://known-bad:11434", "qwen3.8:27b")        # OUR measurement: bad
    dyva._gossiper_apply([{"host": "http://known-bad:11434", "model": "qwen3.8:27b",
                           "smoke": "pass", "client_ts": "t", "src_ip": "9.9.9.9"}], "chan")
    prio = {h: p for p, h, _m in dyva.find_servers("qwen3.8")}
    assert prio["http://known-bad:11434"] == dyva.TIER_BAD   # fill-blanks only; measured wins


# ------------------------------------------------------------------- sync orchestration

def test_sync_is_gated_by_the_enabled_flag(db, monkeypatch):
    monkeypatch.setattr(dyva, "GOSSIPER_ENABLED", False)
    called = []
    monkeypatch.setattr(dyva.requests, "post", lambda *a, **k: called.append(1))
    dyva.gossiper_sync()
    assert called == []   # disabled: never touches the wire


def test_sync_roundtrip_sends_delta_applies_peer_and_advances_cursor(db, monkeypatch):
    monkeypatch.setattr(dyva, "GOSSIPER_ENABLED", True)
    monkeypatch.setattr(dyva, "GOSSIPER_URL", "https://example/dyva/gossiper/")
    _measure_pass("http://mine:11434", "qwen3.8:27b")
    recs = [{"server": "http://mine:11434", "source": "spider", "models": ["qwen3.8:27b"]}]
    monkeypatch.setattr(dyva, "load_servers", lambda: recs)

    chan = dyva._gossiper_channel(dyva._source_urls()["spider"])
    sent = {}

    class FakeResp:
        status_code = 200

        def json(self):
            # ONE batched reply, keyed by channel: a peer honeypot for a different host
            return {"ok": True, "batch": {chan: {"ok": True, "now": 7,
                    "rows": [{"host": "http://peer-trap:11434", "model": "", "smoke": "honeypot",
                              "client_ts": "2026-10-13T10:00:00Z", "src_ip": "9.9.9.9"}]}}}

    def fake_post(url, params=None, json=None, timeout=None):
        sent["body"] = json
        return FakeResp()

    monkeypatch.setattr(dyva.requests, "post", fake_post)
    dyva.gossiper_sync()

    # ONE batched POST: body.batch keyed by channel, carrying our pass for the spider channel
    assert set(sent["body"]["batch"].keys()) == {chan}
    assert any(r["smoke"] == "pass" and r["host"] == "http://mine:11434"
               for r in sent["body"]["batch"][chan]["rows"])
    # ...applied the peer's honeypot, and advanced that channel's cursor to the server's `now`.
    assert dyva.load_attested_honeypots() == {"http://peer-trap:11434"}
    assert dyva._gossiper_cursor(chan)[1] == 7


def test_sync_batches_all_sources_into_ONE_post(db, monkeypatch):
    monkeypatch.setattr(dyva, "GOSSIPER_ENABLED", True)
    monkeypatch.setattr(dyva, "GOSSIPER_URL", "https://example/dyva/gossiper/")
    recs = [{"server": "http://h1:11434", "source": "spider", "models": ["m"]},
            {"server": "http://h2:11434", "source": "forrany", "models": ["m"]}]
    monkeypatch.setattr(dyva, "load_servers", lambda: recs)
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {"ok": True, "batch": {}}

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append(json)
        return R()

    monkeypatch.setattr(dyva.requests, "post", fake_post)
    dyva.gossiper_sync()
    assert len(calls) == 1          # ONE round trip for ALL sources (the whole point)
    assert set(calls[0]["batch"].keys()) == {
        dyva._gossiper_channel(dyva._source_urls()["spider"]),
        dyva._gossiper_channel(dyva._source_urls()["forrany"])}


def test_sync_leaves_cursor_on_transport_failure(db, monkeypatch):
    monkeypatch.setattr(dyva, "GOSSIPER_ENABLED", True)
    monkeypatch.setattr(dyva, "GOSSIPER_URL", "https://example/dyva/gossiper/")
    recs = [{"server": "http://mine:11434", "source": "spider", "models": ["m"]}]
    monkeypatch.setattr(dyva, "load_servers", lambda: recs)

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(dyva.requests, "post", boom)
    dyva.gossiper_sync()   # must not raise
    chan = dyva._gossiper_channel(dyva._source_urls()["spider"])
    assert dyva._gossiper_cursor(chan) == (None, 0)   # untouched; retried next refresh


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
