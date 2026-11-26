"""A shared spreadsheet renders to a formatted table, not raw JSON
(`uv run pytest tests/test_doc_share_sheet.py -q`).

The chat "share" link for a document (GET /docs/{cid}/{doc}) served a `sheet` doc as
text/plain — the recipient saw the raw JSON 2D-array ("I just get a lousy json"). A reader
opening a shared spreadsheet expects the *formatted grid*. handle_doc_share now renders a sheet
into a self-contained reader page (the SAME mathjs compute the dashboard uses, so formulas are
evaluated), while code files still serve as-is and markdown keeps its own reader page.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ---- detection -------------------------------------------------------------

def test_sheet_doc_detected():
    assert dyva._doc_is_sheet({"type": "sheet", "title": "data.csv"}) is True
    assert dyva._doc_is_sheet({"type": "SHEET"}) is True              # case-insensitive
    assert dyva._doc_is_sheet({"type": "markdown", "title": "x.md"}) is False
    assert dyva._doc_is_sheet({"type": "code", "title": "a.py"}) is False


# ---- reader page -----------------------------------------------------------

def test_render_produces_a_full_html_page_with_mathjs():
    page = dyva._render_sheet_share("Fares-2026", '[["A","B"],["1","=A2*2"]]')
    assert page.startswith("<!doctype html>")
    assert "Fares-2026" in page                                       # title substituted
    assert "mathjs@13" in page                                        # the compute engine
    assert "_sheetCompute" in page                                    # the shared compute logic
    assert "__DOC_TITLE__" not in page and "__SHEET_SRC__" not in page


def test_render_escapes_script_breakout():
    # The grid JSON is embedded in a JS string; a literal </script> in a cell must not be able
    # to close the page's own <script> and inject.
    page = dyva._render_sheet_share("t", '[["hi </script><script>alert(1)</script>"]]')
    assert "</script><script>alert(1)</script>" not in page
    assert "<\\/script>" in page                                      # the close tag was neutralised


def test_render_title_is_html_escaped():
    page = dyva._render_sheet_share('<img src=x onerror=alert(1)>', "[[1]]")
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


def test_share_renders_sheet_doc_as_html(monkeypatch):
    chat = {"documents": [{"id": "d1", "type": "sheet",
                           "title": "Airport-Transit-Fares-2026",
                           "content": '[["City","Fare"],["London","7.73"]]'}]}
    _patch(monkeypatch, chat)
    resp = _run(dyva.handle_doc_share(_Req("c1", "Airport-Transit-Fares-2026")))
    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = resp.text
    assert "<!doctype html>" in body and "mathjs@13" in body
    # the raw JSON is NOT the response body any more (it's embedded for client render)
    assert not body.lstrip().startswith("[[")
    assert "sandbox" in resp.headers.get("Content-Security-Policy", "")


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
