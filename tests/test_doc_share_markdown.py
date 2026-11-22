"""A shared markdown document renders to HTML, not raw source
(`uv run pytest tests/test_doc_share_markdown.py -q`).

The chat "share" link for a document (GET /docs/{cid}/{doc}) used to serve a markdown doc as
text/plain — the recipient saw raw markdown. A reader opening a shared link expects the
*generated HTML*. handle_doc_share now renders a markdown doc into a self-contained reader page
(the same marked/DOMPurify/highlight/mermaid stack the dashboard uses); code files and
spreadsheets still serve as-is, and raw-HTML docs keep their existing sandboxed path.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ---- detection -------------------------------------------------------------

def test_markdown_doc_detected():
    assert dyva._doc_is_markdown({"type": "markdown", "title": "options.md"}) is True
    assert dyva._doc_is_markdown({"type": "", "title": "notes.md"}) is True       # .md fallback


def test_code_sheet_html_are_not_markdown():
    assert dyva._doc_is_markdown({"type": "code", "title": "app.md"}) is False     # code, even .md
    assert dyva._doc_is_markdown({"type": "sheet", "title": "data.csv"}) is False
    assert dyva._doc_is_markdown({"type": "html", "title": "page.html"}) is False
    assert dyva._doc_is_markdown({"type": "code", "language": "python",
                                  "title": "x.py"}) is False


# ---- reader page -----------------------------------------------------------

def test_render_produces_a_full_html_page_with_the_app_stack():
    page = dyva._render_markdown_share("my_doc.md", "# Title\n\ntext")
    assert page.startswith("<!doctype html>")
    assert "my_doc.md" in page                                   # title substituted
    for lib in ("marked@12", "dompurify@3", "mermaid@10", "highlight.js/11.9.0"):
        assert lib in page
    assert "__DOC_TITLE__" not in page and "__DOC_SRC__" not in page


def test_render_escapes_script_breakout():
    # The markdown is embedded in a JS string; a literal </script> in the content must not be
    # able to close the page's own <script> and inject.
    page = dyva._render_markdown_share("t", "hi </script><script>alert(1)</script> bye")
    assert "</script><script>alert(1)</script>" not in page
    assert "<\\/script>" in page                                 # the close tag was neutralised


def test_render_title_is_html_escaped():
    page = dyva._render_markdown_share('<img src=x onerror=alert(1)>', "x")
    assert "<img src=x onerror=alert(1)>" not in page
    assert "&lt;img" in page


# ---- the share handler -----------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


class _Req:
    def __init__(self, cid, doc):
        self.match_info = {"cid": cid, "doc": doc}


def _patch(monkeypatch, chat):
    async def _ok(_r):
        return None
    monkeypatch.setattr(dyva, "_check_local", _ok)
    monkeypatch.setattr(dyva, "_chat_get", lambda cid: chat)


def test_share_renders_markdown_doc_as_html(monkeypatch):
    chat = {"documents": [{"id": "d1", "type": "markdown",
                           "title": "newhall_3d_printing_options.md",
                           "content": "# Options\n\n- resin\n- FDM"}]}
    _patch(monkeypatch, chat)
    resp = _run(dyva.handle_doc_share(_Req("c1", "newhall_3d_printing_options.md")))
    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = resp.text
    assert "<!doctype html>" in body and "marked@12" in body
    # the raw markdown is NOT the response body any more (it's embedded for client render)
    assert not body.lstrip().startswith("# Options")
    assert "sandbox" in resp.headers.get("Content-Security-Policy", "")


def test_share_still_serves_code_doc_as_plain_text(monkeypatch):
    chat = {"documents": [{"id": "d2", "type": "code", "language": "python",
                           "title": "hanoi.py", "content": "print('hi')"}]}
    _patch(monkeypatch, chat)
    resp = _run(dyva.handle_doc_share(_Req("c1", "hanoi.py")))
    assert resp.status == 200
    assert resp.content_type == "text/plain"
    assert resp.text == "print('hi')"


def test_share_missing_doc_is_404(monkeypatch):
    _patch(monkeypatch, {"documents": []})
    resp = _run(dyva.handle_doc_share(_Req("c1", "nope.md")))
    assert resp.status == 404


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
