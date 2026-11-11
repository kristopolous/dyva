"""Sub-agent file sandbox (`uv run pytest tests/test_agent_fs.py -q`).

Sub-agents run UNTRUSTED model output on a public demo (9ol.es), so each gets an
isolated on-disk directory under CACHE_DIR/agent-fs/<batch>/<agent>/, capped at
AGENT_FS_MAX bytes. Every model-supplied name is reduced to a safe basename and the
resolved path is asserted to stay inside the agent's own dir — a name like
"../../etc/passwd", "/etc/passwd" or "a/b" can never escape the sandbox. These pin:
the traversal guard, the per-agent byte cap (write + append), read/edit/list, and
cross-agent isolation.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _tmp_cache(monkeypatch=None):
    d = tempfile.mkdtemp(prefix="agentfs-test-")
    dyva.CACHE_DIR = d
    return d


def setup_function(_):
    _tmp_cache()


# ---- traversal guard -------------------------------------------------------

def test_rejects_path_traversal_names():
    for bad in ("../x", "../../etc/passwd", "/etc/passwd", "a/b.txt", "..", ".", "", "   "):
        ok, _ = dyva._agent_fs_write("b1", "a1", bad, "x")
        assert not ok, bad


def test_confined_to_agent_dir():
    dyva._agent_fs_write("b1", "a1", "chart.svg", "<svg/>")
    p = dyva._agent_fs_resolve("b1", "a1", "chart.svg")
    agent_dir = os.path.realpath(dyva._agent_fs_dir("b1", "a1"))
    assert os.path.realpath(p).startswith(agent_dir + os.sep)
    # a traversal name resolves to None, never outside
    assert dyva._agent_fs_resolve("b1", "a1", "../../../etc/passwd") is None


# ---- read / write / edit / list -------------------------------------------

def test_write_then_read_roundtrip():
    ok, _ = dyva._agent_fs_write("b1", "a1", "notes.md", "hello")
    assert ok
    ok, content = dyva._agent_fs_read("b1", "a1", "notes.md")
    assert ok and content == "hello"


def test_append():
    dyva._agent_fs_write("b1", "a1", "log.txt", "one\n")
    dyva._agent_fs_write("b1", "a1", "log.txt", "two\n", append=True)
    _, content = dyva._agent_fs_read("b1", "a1", "log.txt")
    assert content == "one\ntwo\n"


def test_edit_replaces_once():
    dyva._agent_fs_write("b1", "a1", "f.txt", "the quick brown fox")
    ok, _ = dyva._agent_fs_edit("b1", "a1", "f.txt", "quick", "slow")
    assert ok
    _, content = dyva._agent_fs_read("b1", "a1", "f.txt")
    assert content == "the slow brown fox"


def test_read_missing_file():
    ok, _ = dyva._agent_fs_read("b1", "a1", "nope.txt")
    assert not ok


def test_list_files():
    dyva._agent_fs_write("b1", "a1", "a.svg", "<svg/>")
    dyva._agent_fs_write("b1", "a1", "b.md", "# hi")
    names = {n for (n, _sz) in dyva._agent_fs_list("b1", "a1")}
    assert names == {"a.svg", "b.md"}


# ---- per-agent byte cap ----------------------------------------------------

def test_write_over_cap_rejected():
    ok, _ = dyva._agent_fs_write("b1", "a1", "big.bin", "x" * (dyva.AGENT_FS_MAX + 1))
    assert not ok
    # nothing was written
    assert dyva._agent_fs_list("b1", "a1") == []


def test_cap_counts_existing_files():
    half = dyva.AGENT_FS_MAX // 2
    assert dyva._agent_fs_write("b1", "a1", "one.bin", "x" * half)[0]
    # second file that would push total over the cap is rejected
    ok, _ = dyva._agent_fs_write("b1", "a1", "two.bin", "y" * (half + 100))
    assert not ok


def test_overwrite_reclaims_space():
    assert dyva._agent_fs_write("b1", "a1", "f.bin", "x" * (dyva.AGENT_FS_MAX - 10))[0]
    # overwriting the same file (not appending) frees its old bytes, so a small write fits
    assert dyva._agent_fs_write("b1", "a1", "f.bin", "y" * 10)[0]


# ---- isolation -------------------------------------------------------------

def test_agents_are_isolated():
    dyva._agent_fs_write("b1", "a1", "secret.txt", "A's data")
    # a different agent in the same batch cannot read it
    ok, _ = dyva._agent_fs_read("b1", "a2", "secret.txt")
    assert not ok
    # different batch, same agent name — also isolated
    ok, _ = dyva._agent_fs_read("b2", "a1", "secret.txt")
    assert not ok


def test_batch_ids_are_tokenized():
    # a hostile batch/agent id can't escape the agent-fs root either
    dyva._agent_fs_write("../../evil", "a1", "x.txt", "data")
    root = os.path.realpath(dyva._agent_fs_root())
    p = dyva._agent_fs_resolve("../../evil", "a1", "x.txt")
    assert p is None or os.path.realpath(p).startswith(root + os.sep)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
