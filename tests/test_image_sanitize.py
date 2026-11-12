"""Unusable-image swap (`uv run pytest tests/test_image_sanitize.py -q`).

A client can hand dyva an "image" that isn't image data at all — most notably a browser
`blob:` object-URL (valid only inside the page that made it). Forwarded into Ollama's
`images` field it's base64-decoded by every host and 400s the WHOLE pool ("illegal base64
data"). So instead of failing, dyva DROPS the unusable entry and leaves a short text note,
and the turn continues (routed as text, no vision required). Real image data — a `data:`
URL or raw base64 — passes through untouched and still requires a vision model. These pin:
the usable/unusable split and the _flatten_content swap + its effect on needs_caps.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

_RAWB64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
_BLOB = "blob:https://www.whackgpt.com/384c37ed-ef92-49d2-8f75-6b27c3abcd00"


# ---- the usable/unusable split --------------------------------------------

def test_raw_base64_is_kept():
    kept, dropped = dyva._usable_images([_RAWB64])
    assert kept == [_RAWB64] and dropped == 0


def test_data_url_is_reduced_to_base64():
    kept, dropped = dyva._usable_images(["data:image/png;base64," + _RAWB64])
    assert kept == [_RAWB64] and dropped == 0


def test_blob_url_is_dropped():
    kept, dropped = dyva._usable_images([_BLOB])
    assert kept == [] and dropped == 1


def test_remote_url_is_dropped():
    kept, dropped = dyva._usable_images(["https://example.com/cat.png"])
    assert kept == [] and dropped == 1


def test_mixed_keeps_the_good_one():
    kept, dropped = dyva._usable_images([_BLOB, _RAWB64])
    assert kept == [_RAWB64] and dropped == 1


# ---- the _flatten_content swap + needs_caps effect ------------------------

def test_blob_image_is_swapped_for_a_note_and_needs_no_vision():
    msgs = [{"role": "user", "content": "look at this", "images": [_BLOB]}]
    out = dyva._flatten_content(msgs)
    assert "images" not in out[0] or not out[0]["images"]      # blob dropped
    assert "look at this" in out[0]["content"]                 # original text kept
    assert "image" in out[0]["content"].lower()                # a note was left
    assert "vision" not in dyva.needs_caps(out)                # routes as text, not vision


def test_real_image_still_requires_vision():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _RAWB64}}]}]
    out = dyva._flatten_content(msgs)
    assert out[0].get("images") == [_RAWB64]                   # real image kept as base64
    assert "vision" in dyva.needs_caps(out)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
