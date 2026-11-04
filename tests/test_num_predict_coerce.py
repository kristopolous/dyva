"""`options.num_predict` must reach Ollama as an integer — pytest
(`uv run pytest tests/test_num_predict_coerce.py -q`).

Seen live: `status 500: option "num_predict" must be of type integer`. A caller can pin
`options.num_predict` (an agentic client, or an OpenAI max_tokens that rode in), and
`_with_num_predict` used to pass a pinned value straight through — so a float / string /
null reached Ollama verbatim and 500'd the whole request. Now a pinned value is coerced
to int; an uncoercible one (null, garbage) falls back to dyva's own default of -1
(generate until a natural stop or the window fills), which is what an unpinned request
already gets.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def _np(payload):
    return dyva._with_num_predict(payload)["options"]["num_predict"]


def test_float_is_coerced_to_int():
    v = _np({"options": {"num_predict": 512.0}})
    assert v == 512 and isinstance(v, int)


def test_numeric_string_is_coerced_to_int():
    v = _np({"options": {"num_predict": "256"}})
    assert v == 256 and isinstance(v, int)


def test_none_falls_back_to_default():
    assert _np({"options": {"num_predict": None}}) == -1


def test_uncoercible_falls_back_to_default():
    assert _np({"options": {"num_predict": "lots"}}) == -1


def test_valid_int_is_preserved():
    assert _np({"options": {"num_predict": 4096}}) == 4096


def test_unpinned_still_defaults_to_minus_one():
    assert _np({"options": {}}) == -1
    assert _np({}) == -1


# --- the OpenAI dialect has the same hole: options.num_predict -> max_tokens, and a
# top-level max_tokens, both reach a strict OpenAI/vLLM host, which 400s a non-int. ---

def test_openai_num_predict_float_coerced_to_max_tokens_int():
    out = dyva.openai_payload({"options": {"num_predict": 512.0}})
    assert out["max_tokens"] == 512 and isinstance(out["max_tokens"], int)


def test_openai_num_predict_null_drops_max_tokens():
    out = dyva.openai_payload({"options": {"num_predict": None}})
    assert "max_tokens" not in out            # uncoercible -> omit, host uses its default


def test_openai_top_level_float_max_tokens_coerced():
    out = dyva.openai_payload({"max_tokens": 256.0})
    assert out["max_tokens"] == 256 and isinstance(out["max_tokens"], int)


def test_openai_valid_max_tokens_preserved():
    assert dyva.openai_payload({"options": {"num_predict": 4096}})["max_tokens"] == 4096


def test_openai_temperature_float_is_not_coerced():
    # regression guard: only max_tokens is integer-coerced; temperature stays a float.
    out = dyva.openai_payload({"options": {"temperature": 0.7, "num_predict": 128}})
    assert out["temperature"] == 0.7 and isinstance(out["temperature"], float)
