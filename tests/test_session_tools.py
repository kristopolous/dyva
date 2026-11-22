"""Phase 2 core: the prefixed session tools — definitions, double-gated injection, and
server-side execution (`uv run pytest tests/test_session_tools.py -q`).

The opt-in tools (`<prefix>_list_sessions`, `<prefix>_search_sessions`) are injected into a
request's tool array ONLY when BOTH gates hold: the caller is opted in (has a userhash) AND the
request is already tool-calling (has client tools). The random prefix guarantees they can't
collide with the caller's own tool names, so interception is unambiguous: a call whose name
carries the prefix is ours (executed server-side); anything else is relayed to the client. This
file covers the pure/ library parts; the streaming loop that drives them is wired separately.
See brainstorm/user-account.md.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402

CLIENT_TOOLS = [{"type": "function",
                 "function": {"name": "get_weather", "description": "weather",
                              "parameters": {"type": "object", "properties": {}}}}]


# ---- the two gates ---------------------------------------------------------

def test_injects_both_session_tools_when_opted_in_and_tool_calling():
    out = dyva._inject_session_tools(CLIENT_TOOLS, "uh1")
    names = [t["function"]["name"] for t in out]
    assert "get_weather" in names                          # client tools preserved
    assert f"{dyva.SESSION_TOOL_PREFIX}_list_sessions" in names
    assert f"{dyva.SESSION_TOOL_PREFIX}_search_sessions" in names


def test_noop_without_userhash():
    assert dyva._inject_session_tools(CLIENT_TOOLS, None) == CLIENT_TOOLS
    assert dyva._inject_session_tools(CLIENT_TOOLS, "") == CLIENT_TOOLS


def test_noop_when_not_already_tool_calling():
    # the second gate: don't augment a plain chat with no client tools
    assert dyva._inject_session_tools([], "uh1") == []
    assert dyva._inject_session_tools(None, "uh1") is None


def test_injection_does_not_mutate_the_caller_list():
    original = list(CLIENT_TOOLS)
    dyva._inject_session_tools(CLIENT_TOOLS, "uh1")
    assert CLIENT_TOOLS == original                        # caller's list untouched


# ---- classification (intercept-ours / relay-theirs) ------------------------

def test_is_session_tool_matches_only_the_prefix():
    assert dyva._is_session_tool(f"{dyva.SESSION_TOOL_PREFIX}_search_sessions")
    assert dyva._is_session_tool(f"{dyva.SESSION_TOOL_PREFIX}_list_sessions")
    assert not dyva._is_session_tool("get_weather")
    assert not dyva._is_session_tool("")
    assert not dyva._is_session_tool(None)


# ---- server-side execution -------------------------------------------------

@pytest.fixture
def indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(dyva, "_sessions_db", None)
    monkeypatch.setattr(dyva, "SEMINDEX_DB", str(tmp_path / "sem.db"))
    monkeypatch.setattr(dyva, "_sem_db", None)

    async def _fake_embed(session, model, text):
        v = [float((text or "").lower().count(w)) for w in ("routing", "vision", "cat")]
        return v if sum(v) else [1e-6, 0.0, 0.0]
    monkeypatch.setattr(dyva, "_embed_text", _fake_embed)
    yield


def test_run_list_sessions_returns_the_users_sessions(indexes):
    dyva._session_upsert("uh1", "aff1", "Routing chat",
                         {"title": "Routing chat", "messages": [{"role": "user", "content": "x"}]})
    out = asyncio.run(dyva._run_session_tool(None, "uh1",
                      f"{dyva.SESSION_TOOL_PREFIX}_list_sessions", {}))
    assert "Routing chat" in out


def test_run_search_sessions_finds_relevant(indexes):
    dyva._session_upsert("uh1", "aff1", "routing talk",
                         {"title": "routing talk", "messages": [{"role": "user", "content": "routing routing"}]})
    out = asyncio.run(dyva._run_session_tool(None, "uh1",
                      f"{dyva.SESSION_TOOL_PREFIX}_search_sessions", {"query": "routing"}))
    assert "routing talk" in out.lower()


def test_run_scopes_to_the_calling_user(indexes):
    dyva._session_upsert("uh2", "affX", "someone elses secret",
                         {"title": "someone elses secret", "messages": [{"role": "user", "content": "x"}]})
    out = asyncio.run(dyva._run_session_tool(None, "uh1",
                      f"{dyva.SESSION_TOOL_PREFIX}_list_sessions", {}))
    assert "secret" not in out                             # uh1 can't see uh2's sessions


def test_run_unknown_session_tool_is_handled(indexes):
    out = asyncio.run(dyva._run_session_tool(None, "uh1",
                      f"{dyva.SESSION_TOOL_PREFIX}_bogus", {}))
    assert out and ("unknown" in out.lower() or "not available" in out.lower())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
