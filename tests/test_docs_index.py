"""Docs index behind read_docs / get_doc_section — pytest
(`uv run pytest tests/test_docs_index.py -q`).

The chat `read_docs` / `get_doc_section` tools answer questions about dyva from the
project's own markdown (the READMEs plus docs/** and brainstorm/**). To keep the chat
context lean, the tools never ship a whole file — they ship a heading list, one section,
or a short ranked snippet. These pin the pure pieces that do that, over in-memory text
(no disk), so they're deterministic:

  * _md_sections(text, path): split a doc at ATX headings into ordered sections, with any
    text before the first heading kept as a preamble section (heading "");
  * _docs_list(sections): headings only (navigation), no bodies;
  * _docs_get(sections, name): the one section whose heading best matches a name;
  * _docs_search(sections, query): sections ranked by query match (heading weighs more
    than body), each with a short snippet — the context-lean answer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


_DOC = """intro line about dyva
and more intro
# Host Reputation
Hosts earn trust over time. Good, maybe_good, unknown, bad.
## Busy hosts
ComfyUI runs one prompt at a time per GPU, so a second job just queues.
## Warming hosts
A cold host pays a load cost on the first request.
# Context window
Models declare a context limit; dyva learns the real one.
"""


def _sections():
    return dyva._md_sections(_DOC, "dyva/README.md")


def test_md_sections_splits_on_headings_with_preamble():
    secs = _sections()
    heads = [(s["heading"], s["level"]) for s in secs]
    assert heads == [("", 0), ("Host Reputation", 1), ("Busy hosts", 2),
                     ("Warming hosts", 2), ("Context window", 1)]
    # every section carries its source path, and bodies are split correctly
    assert all(s["path"] == "dyva/README.md" for s in secs)
    assert "one prompt at a time" in dict((s["heading"], s["body"]) for s in secs)["Busy hosts"]


def test_docs_list_is_headings_only():
    listed = dyva._docs_list(_sections())
    assert {"path", "heading", "level"} == set(listed[0])
    assert "body" not in listed[0]
    assert "Busy hosts" in [x["heading"] for x in listed]


def test_docs_get_matches_a_section_by_name_loosely():
    got = dyva._docs_get(_sections(), "busy hosts")      # case-insensitive
    assert got["heading"] == "Busy hosts"
    assert "queues" in got["body"]
    assert dyva._docs_get(_sections(), "no such section") is None


def test_docs_search_ranks_by_match_and_returns_snippets():
    hits = dyva._docs_search(_sections(), "busy comfyui gpu", limit=3)
    assert hits                                   # found something
    assert hits[0]["heading"] == "Busy hosts"     # best match first
    assert "path" in hits[0] and "snippet" in hits[0]
    assert len(hits) <= 3
    assert dyva._docs_search(_sections(), "", limit=3) == []   # empty query -> nothing


def test_docs_search_snippet_is_bounded():
    big = "# Big\n" + ("lorem ipsum busy " * 400)
    hits = dyva._docs_search(dyva._md_sections(big, "p.md"), "busy", limit=1)
    assert len(hits[0]["snippet"]) <= 400         # never dumps the whole section


import asyncio
import json


class _Req:
    """Minimal stand-in for an aiohttp request: .query and .app['session'] are read.
    No embedding host is configured in-test, so the ?q= handler's semantic path returns
    None and falls back to the keyword _docs_search these assertions pin."""
    def __init__(self, **q):
        self.query = q
        self.app = {"session": None}


def _call(**q):
    async def _go():
        # Force the no-embedder path so these pin the keyword _docs_search fallback.
        orig = dyva._embed_text

        async def _no_embed(session, model, text):
            return None
        dyva._embed_text = _no_embed
        try:
            return await dyva.handle_dyva_docs(_Req(**q))
        finally:
            dyva._embed_text = orig
    resp = asyncio.run(_go())
    return resp.status, json.loads(resp.body)


def test_endpoint_no_arg_lists_headings():
    status, data = _call()
    assert status == 200
    assert data["sections"] and "heading" in data["sections"][0]
    assert all("body" not in s for s in data["sections"])   # list stays lean


def test_endpoint_search_returns_snippets():
    status, data = _call(q="honeypot")
    assert status == 200
    assert data["hits"] and "snippet" in data["hits"][0]


def test_endpoint_unknown_section_is_404_with_list():
    status, data = _call(section="definitely not a real heading")
    assert status == 404
    assert "sections" in data        # hands back the list so the model can retry


def test_corpus_is_a_whitelist_never_a_filesystem_sweep():
    # Every indexed section traces to a file in the explicit whitelist — nothing else on
    # disk is reachable. The list carries only relative, in-repo paths (no '..', no abs).
    allowed = set(dyva._DOC_FILES)
    paths = {s["path"] for s in dyva._docs_index()}
    assert paths and paths <= allowed
    for rel in dyva._DOC_FILES:
        assert not os.path.isabs(rel) and ".." not in rel.split("/")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
