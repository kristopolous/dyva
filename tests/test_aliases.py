"""Alias config parsing + the `!` expansion pre-pass — pytest
(`uv run pytest tests/test_aliases.py -q`).

Aliases are bash-style, redefinable routing shortcuts. The user edits a dumb config file
(`!name` on one line, its query on the next; `#comments` and blank lines ignored) and types
`!name` in chat. Before find_servers, `!name` is substituted in place — and, like a bash
alias, anything appended rides along: `!qwen>10gb` -> `<body>>10gb`.

Key rules fixed in design:
- sigil is `!`, and only at a SEGMENT START (string start, or right after a `,`), so the `!`
  in a `;size!=0` negation facet is never touched, nor is a `!` mid-token.
- names are case-insensitive.
- an undefined `!name` is left literal — it matches nothing, so a `,*` fallback saves it.
  (A footgun by design: the comma chain is the safety net, not validation.)
- nested aliases resolve; a cycle terminates instead of hanging.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ----------------------------- parse_aliases -----------------------------

def test_parse_basic_two_blocks():
    text = ("!image-edit\n"
            "qwen-image-edit,flux-kontext;state=good\n"
            "\n"
            "!video\n"
            "*\n")
    assert dyva.parse_aliases(text) == {
        "image-edit": "qwen-image-edit,flux-kontext;state=good",
        "video": "*",
    }


def test_parse_ignores_comments_and_blanks():
    text = ("# my aliases\n"
            "\n"
            "!qwen\n"
            "qwen3.8\n"
            "   # trailing note\n")
    assert dyva.parse_aliases(text) == {"qwen": "qwen3.8"}


def test_parse_names_are_lowercased():
    assert dyva.parse_aliases("!QWEN\nqwen3.8\n") == {"qwen": "qwen3.8"}


def test_parse_later_duplicate_wins():
    text = "!q\nqwen3.8\n\n!Q\ngemma4\n"
    assert dyva.parse_aliases(text) == {"q": "gemma4"}


def test_parse_empty_is_empty_map():
    assert dyva.parse_aliases("") == {}
    assert dyva.parse_aliases("# only a comment\n\n") == {}


# ----------------------------- expand_aliases -----------------------------

AL = {
    "qwen": "qwen3.8",
    "image-edit": "qwen-image-edit,flux-kontext;state=good",
    "video": "*",
}


def test_expand_plain():
    assert dyva.expand_aliases("!qwen", AL) == "qwen3.8"


def test_expand_bash_style_suffix_rides_along():
    assert dyva.expand_aliases("!qwen>10gb", AL) == "qwen3.8>10gb"
    assert dyva.expand_aliases("!qwen*abliterated", AL) == "qwen3.8*abliterated"


def test_expand_is_case_insensitive():
    assert dyva.expand_aliases("!QWEN", AL) == "qwen3.8"
    assert dyva.expand_aliases("!Qwen", AL) == "qwen3.8"


def test_expand_only_at_segment_start():
    # a bare facet '=' is untouched (no '!' at all)
    assert dyva.expand_aliases("qwen;state=good", AL) == "qwen;state=good"
    # a '!' mid-token is NOT a sigil
    assert dyva.expand_aliases("foo!qwen", AL) == "foo!qwen"
    # the '!' inside a negation facet is mid-segment, never a sigil
    assert dyva.expand_aliases("qwen;size!=0", AL) == "qwen;size!=0"


def test_expand_after_comma():
    assert dyva.expand_aliases("gemma,!qwen", AL) == "gemma,qwen3.8"


def test_expand_undefined_is_left_literal():
    # undefined -> stays as typed -> matches nothing -> the ,* fallback saves it
    assert dyva.expand_aliases("!bogus", AL) == "!bogus"
    assert dyva.expand_aliases("!bogus,*", AL) == "!bogus,*"


def test_expand_body_can_carry_facets_and_chain():
    assert dyva.expand_aliases("!image-edit", AL) == "qwen-image-edit,flux-kontext;state=good"


def test_expand_no_aliases_passthrough():
    assert dyva.expand_aliases("!qwen", {}) == "!qwen"
    assert dyva.expand_aliases("qwen3.8", AL) == "qwen3.8"


def test_expand_nested():
    al = {"a": "!b", "b": "gemma4"}
    assert dyva.expand_aliases("!a", al) == "gemma4"


def test_expand_cycle_terminates():
    al = {"a": "!b", "b": "!a"}
    # must not hang; resolves to a literal, not an exception
    out = dyva.expand_aliases("!a", al)
    assert out.startswith("!")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
