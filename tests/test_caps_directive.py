"""Explicit caps override in the model string (`uv run pytest tests/test_caps_directive.py -q`).

dyva normally INFERS the required caps from the messages (an image -> 'vision', tool_calls ->
'tools', …). A custom app built on dyva may want to state them EXPLICITLY — e.g. attach an image
for a tool yet route to a non-vision model. The override rides on the model string as a leading
directive: ";caps=cap1,cap2;<normal model query>". It REPLACES the inferred caps. These pin: the
parse (incl. aliases, whitespace, fallback queries), the blank/invalid -> no-override fallback,
and that the override wins over message inference.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

_IMG_MSG = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]}]


# ---- the parser ------------------------------------------------------------

def test_no_directive_is_passthrough():
    assert dyva._strip_caps_directive("qwen3") == ("qwen3", None)
    assert dyva._strip_caps_directive("qwen3||llama3") == ("qwen3||llama3", None)


def test_parses_caps_and_cleans_model():
    clean, caps = dyva._strip_caps_directive(";caps=vision,tools;qwen3")
    assert clean == "qwen3"
    assert caps == {"vision", "tools", "completion"}   # completion is always implied


def test_keeps_the_rest_of_the_model_query():
    clean, caps = dyva._strip_caps_directive(";caps=text;qwen3||llama3:70b")
    assert clean == "qwen3||llama3:70b"
    assert caps == {"completion"}


def test_aliases_and_whitespace():
    _, caps = dyva._strip_caps_directive(";caps= image , think ;m")
    assert caps == {"vision", "thinking", "completion"}


def test_blank_or_invalid_caps_fall_back_to_auto():
    # directive present but empty / all-unknown -> strip it, but no override (None)
    assert dyva._strip_caps_directive(";caps=;qwen3") == ("qwen3", None)
    assert dyva._strip_caps_directive(";caps=bogus;qwen3") == ("qwen3", None)


# ---- caps_for: override vs inference ---------------------------------------

def test_override_replaces_message_inference():
    # an image in the messages would normally force 'vision'; the override drops it
    clean, caps = dyva.caps_for(";caps=text;qwen3", _IMG_MSG)
    assert clean == "qwen3"
    assert "vision" not in caps and "completion" in caps


def test_no_override_infers_from_messages():
    clean, caps = dyva.caps_for("qwen3", _IMG_MSG)
    assert clean == "qwen3"
    assert "vision" in caps


def test_no_override_adds_tools_and_images_flags():
    _, caps = dyva.caps_for("qwen3", [], tools=True, images=True)
    assert "tools" in caps and "vision" in caps


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
