"""needs_caps() must flag "vision" for every image encoding the flattener accepts — pytest
(`uv run pytest tests/test_needs_caps_image_tags.py -q`).

needs_caps() is the requirement-detector that feeds routing: an image in a request becomes
a `vision` requirement, and the router then races vision-capable hosts (exhausting the pool
and erroring like any unsatisfiable request if none exist). It is NOT a capability gate —
no special-casing, no change to the routing primitives.

The bug (seen when a Telegram/fetch image should have been seen but wasn't): needs_caps only
recognized ("image_url", "file"), while the flattener that actually moves images to Ollama's
`images` list accepts ("image_url", "input_image", "image") and treats a `file` block as a
TEXT document, not an image. So an image sent as the OpenAI Responses `input_image` shape (or
a bare `image` block) never emitted the vision requirement and slipped to a text host; and a
document `file` wrongly demanded a vision model. This aligns needs_caps' image set with the
flattener's.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _caps(content):
    return dyva.needs_caps([{"role": "user", "content": content}])


def test_input_image_block_requires_vision():
    # OpenAI Responses API shape — the flattener handles it, needs_caps used to miss it.
    assert "vision" in _caps([{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}])


def test_bare_image_block_requires_vision():
    assert "vision" in _caps([{"type": "image", "image_url": "data:image/png;base64,AAAA"}])


def test_image_url_block_still_requires_vision():
    # regression guard: the common OpenAI chat shape already worked; keep it working.
    assert "vision" in _caps([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])


def test_text_only_has_no_vision():
    assert "vision" not in _caps([{"type": "text", "text": "hi"}])


def test_file_attachment_does_not_require_vision():
    # a `file` block is a text document in the flattener, not an image — it must not demand a
    # vision model (which would route to a vision host that then just gets text).
    assert "vision" not in _caps([{"type": "file", "file": {"filename": "a.txt", "file_data": "hi"}}])


def test_top_level_images_list_still_requires_vision():
    # the Ollama-native shape (bare base64 in a sibling `images` list) is unchanged.
    assert "vision" in dyva.needs_caps([{"role": "user", "images": ["AAAA"], "content": ""}])
