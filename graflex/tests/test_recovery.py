"""Re-sighting-triggered recovery: re-probe hosts that a fresh survey sees again AND
that we currently hold as `unreachable` (offline/timeout) — not hard-errored, not
honeypots, not already recovered. This is the cheap middle ground between check-new
(skips every recorded host) and check-everything (re-probes the whole pool for weeks).

Two units are pinned here:
  - hoststore.recoverable_hosts: the store's current `unreachable` set for a service,
    resolved through the same last-observation-wins reduce the export uses (so a host
    that has since come back working is NOT offered as a candidate);
  - _survey_recovery_targets: the intersection of a survey's sighted hosts with that set.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import graflex                    # noqa: E402
from graflex import hoststore    # noqa: E402


def _bad(conn, service, host, result, checked):
    hoststore.upsert_bad(conn, service, host, {"host": host, "result": result}, checked)


def test_recoverable_hosts_is_only_the_unreachable_class(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    _bad(conn, "ollama", "1.1.1.1:11434", "unreachable", "2026-01-01T00:00:00Z")  # offline -> candidate
    _bad(conn, "ollama", "2.2.2.2:11434", "error", "2026-01-01T00:00:00Z")        # 404/auth -> never
    _bad(conn, "ollama", "3.3.3.3:11434", "honeypot", "2026-01-01T00:00:00Z")     # trap -> never
    assert hoststore.recoverable_hosts(conn, "ollama") == {"1.1.1.1:11434"}


def test_recovered_host_is_not_a_candidate(tmp_path):
    # unreachable at T0, then a newer WORKING probe at T1 — it already came back, so the
    # reduce resolves it working and it must not be offered for re-probe again.
    conn = hoststore.connect(str(tmp_path / "g.db"))
    _bad(conn, "ollama", "9.9.9.9:11434", "unreachable", "2026-01-01T00:00:00Z")
    hoststore.upsert(conn, "ollama", "9.9.9.9:11434", "working",
                     {"host": "9.9.9.9:11434", "models": ["x"]}, "2026-02-01T00:00:00Z")
    assert hoststore.recoverable_hosts(conn, "ollama") == set()


def test_recoverable_is_per_service(tmp_path):
    conn = hoststore.connect(str(tmp_path / "g.db"))
    _bad(conn, "ollama", "4.4.4.4:11434", "unreachable", "2026-01-01T00:00:00Z")
    _bad(conn, "vllm", "4.4.4.4:8000", "unreachable", "2026-01-01T00:00:00Z")
    assert hoststore.recoverable_hosts(conn, "ollama") == {"4.4.4.4:11434"}


def test_survey_recovery_targets_keeps_only_sighted_and_recoverable():
    # a fresh survey sighted three hosts; only the ones ALSO in the recoverable set are
    # returned, as their original survey entries (so the probe has the full record).
    sighted = [{"host": "1.1.1.1:11434"}, {"host": "5.5.5.5:11434"}, {"host": "7.7.7.7:11434"}]
    recoverable = {"1.1.1.1:11434", "7.7.7.7:11434", "8.8.8.8:11434"}  # 8.8 wasn't re-sighted
    got = graflex._survey_recovery_targets(sighted, recoverable)
    assert got == [{"host": "1.1.1.1:11434"}, {"host": "7.7.7.7:11434"}]


def test_survey_recovery_targets_empty_when_no_overlap():
    sighted = [{"host": "1.1.1.1:11434"}]
    assert graflex._survey_recovery_targets(sighted, {"2.2.2.2:11434"}) == []


def test_run_survey_recovery_probes_only_sighted_recoverable(tmp_path, monkeypatch):
    import asyncio
    conn = hoststore.connect(str(tmp_path / "g.db"))
    monkeypatch.setattr(graflex, "_HOSTSTORE", conn, raising=False)
    monkeypatch.setattr(graflex, "export", lambda *a, **k: None)   # isolate: no file I/O
    _bad(conn, "ollama", "A:1", "unreachable", "2026-01-01T00:00:00Z")   # offline + sighted -> probe
    _bad(conn, "ollama", "B:1", "error", "2026-01-01T00:00:00Z")         # hard error + sighted -> skip
    hoststore.upsert(conn, "ollama", "C:1", "working", {"host": "C:1"}, "2026-01-01T00:00:00Z")  # working

    sighted = [{"host": "A:1"}, {"host": "B:1"}, {"host": "D:1"}]        # D never recorded
    probed = {}

    async def fake_probe(targets):
        probed["hosts"] = [graflex._entry_host(h) for h in targets]

    asyncio.run(graflex._run_survey_recovery("ollama", session="s", site=None,
                                             check_timeout=5, workers=2,
                                             sighted=sighted, probe=fake_probe))
    assert probed["hosts"] == ["A:1"]


def test_run_survey_recovery_no_targets_skips_probe(tmp_path, monkeypatch):
    import asyncio
    conn = hoststore.connect(str(tmp_path / "g.db"))
    monkeypatch.setattr(graflex, "_HOSTSTORE", conn, raising=False)
    exported = []
    monkeypatch.setattr(graflex, "export", lambda *a, **k: exported.append(a))
    _bad(conn, "ollama", "A:1", "unreachable", "2026-01-01T00:00:00Z")

    called = {"probe": False}

    async def fake_probe(targets):
        called["probe"] = True

    # survey sighted nothing recoverable (A not among sighted)
    asyncio.run(graflex._run_survey_recovery("ollama", session="s", site=None,
                                             check_timeout=5, workers=2,
                                             sighted=[{"host": "Z:1"}], probe=fake_probe))
    assert called["probe"] is False           # nothing to do -> no probe
    assert exported                            # but the service's files are still regenerated
