"""A shared SVG document renders the picture, not its raw XML
(`uv run pytest tests/test_doc_share_svg.py -q`).

The chat "share" link for a document (GET /docs/{cid}/{doc}) served an SVG doc as text/plain —
the recipient saw the raw `<svg…>` source instead of the image. handle_doc_share now renders a
sheet-/markdown-style reader page that embeds the SVG as an inert `<img>` data-URI (so any
`<script>` in the SVG can't run), while code files still serve as-is.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ---- detection -------------------------------------------------------------

def test_svg_doc_detected():
    assert dyva._doc_is_svg({"type": "code", "title": "la-weather.svg"}) is True
    assert dyva._doc_is_svg({"type": "code", "language": "svg", "title": "chart"}) is True
    assert dyva._doc_is_svg({"type": "code", "content": '<svg viewBox="0 0 10 10"></svg>'}) is True
    assert dyva._doc_is_svg({"type": "code", "content": '<?xml version="1.0"?><svg></svg>'}) is True
    assert dyva._doc_is_svg({"type": "markdown", "title": "notes.md",
                             "content": "talking about <svg> here"}) is False
    assert dyva._doc_is_svg({"type": "code", "language": "python", "title": "a.py"}) is False
    assert dyva._doc_is_svg({"type": "code", "title": "page.html",
                             "language": "html", "content": "<svg></svg>"}) is False


# ---- reader page -----------------------------------------------------------

def test_render_produces_a_full_html_page_with_embedded_svg():
    page = dyva._render_svg_share("la-weather.svg", '<svg viewBox="0 0 2 2"><rect/></svg>')
    assert page.startswith("<!doctype html>")
    assert "la-weather.svg" in page                                   # title substituted
    assert "data:image/svg+xml;base64," in page                       # embedded as an image
    assert "__SVG_URI__" not in page and "__DOC_TITLE__" not in page


def test_render_embeds_svg_inertly_not_as_live_markup():
    # A <script> inside the SVG must not survive as live markup in the page — the SVG is
    # base64'd into an <img>, where it can't execute and its tags don't reach the DOM.
    page = dyva._render_svg_share("t", '<svg><script>alert(1)</script></svg>')
    assert "<script>alert(1)</script>" not in page
    assert "<svg>" not in page                                        # not inlined as markup


def test_render_title_is_html_escaped():
    page = dyva._render_svg_share('<img src=x onerror=alert(1)>', "<svg></svg>")
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


def test_share_renders_svg_doc_as_html(monkeypatch):
    chat = {"documents": [{"id": "d1", "type": "code", "language": "svg",
                           "title": "la-weather.svg",
                           "content": '<svg viewBox="0 0 4 4"><circle/></svg>'}]}
    _patch(monkeypatch, chat)
    resp = _run(dyva.handle_doc_share(_Req("c1", "la-weather.svg")))
    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = resp.text
    assert "<!doctype html>" in body and "data:image/svg+xml;base64," in body
    assert not body.lstrip().startswith("<svg")                       # not the raw SVG
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "sandbox" in csp
    assert "allow-scripts" not in csp                                 # the SVG page needs no scripts


def test_share_still_serves_code_doc_as_plain_text(monkeypatch):
    chat = {"documents": [{"id": "d2", "type": "code", "language": "python",
                           "title": "hanoi.py", "content": "print('hi')"}]}
    _patch(monkeypatch, chat)
    resp = _run(dyva.handle_doc_share(_Req("c1", "hanoi.py")))
    assert resp.status == 200
    assert resp.content_type == "text/plain"
    assert resp.text == "print('hi')"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
