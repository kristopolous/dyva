"""Model-query language suite — pytest (`uv run pytest tests/test_query_language.py`).

Pins the "Model names are routing patterns" contract from dyva/README.md: globs,
case/separator insensitivity, the `$` end-anchor, canon_pattern, and the size and
release-date predicates. All pure functions — deterministic, no hosts, no DB. (The
`;key op value` host-reputation facets are covered by tests/test_host_status_where.py;
the comma fallback chain by dyva/test_model_query.py.)
"""
import operator
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

mm = dyva.match_model
mq = dyva.model_query_match
cp = dyva.canon_pattern
ss = dyva._split_size_filter
sd = dyva._split_date_filter


# ---- partials / globs / case ------------------------------------------------

def test_bare_name_is_a_containment_match():
    assert mm("qwen3.8:27b", "qwen") is True
    assert mm("llama3.3:70b", "qwen") is False


def test_star_glob_matches_variants():
    assert mm("flux2-dev", "flux*2") is True
    assert mm("flux-2-klein", "flux*2") is True
    assert mm("qwen3-27b-instruct", "qwen*27b") is True


def test_question_mark_is_exactly_one_char():
    assert mm("llama3-70b", "llama3?70b") is True     # '-' fills the one slot
    assert mm("llama370b", "llama3?70b") is False      # nothing to fill it


def test_matching_is_case_insensitive():
    assert mm("gemma3:12b", "GEMMA") is True
    assert mm("Qwen3.8", "qwen3.8") is True


def test_separator_insensitive_via_model_query_match():
    # flux.2 / flux-2 / flux 2 all find flux2-dev
    for q in ("flux.2", "flux-2", "flux 2", "flux2"):
        assert mq("flux2-dev", q) is True, q


# ---- end-anchor $ -----------------------------------------------------------

def test_end_anchor_pins_exact_tail():
    assert mm("qwen3.8:27b", "qwen3.8:27b$") is True
    assert mm("qwen3.8:27b-q4_K_M", "qwen3.8:27b$") is False
    assert mm("qwen3.8:27b-abliterated", "qwen3.8:27b$") is False


def test_without_anchor_the_variants_are_swept_in():
    assert mm("qwen3.8:27b-q4_K_M", "qwen3.8:27b") is True


def test_anchor_also_honored_through_model_query_match():
    assert mq("qwen3.8:27b", "qwen3.8:27b$") is True
    assert mq("qwen3.8:27b-abliterated", "qwen3.8:27b$") is False


# ---- canon_pattern (shared reputation key) ----------------------------------

def test_canon_collapses_equivalent_patterns():
    assert cp("GEMMA*") == "gemma"
    assert cp("gemma") == "gemma"
    assert cp("*gemma*") == "gemma"


def test_canon_any_forms_collapse_to_empty():
    assert cp("*") == ""
    assert cp("**") == ""
    assert cp("") == ""
    assert cp(None) == ""


# ---- size predicates --------------------------------------------------------

def test_size_bare_predicate_strips_name_and_binds_threshold():
    name, pred = ss(">10gb")
    assert name == ""
    cmp, thresh = pred
    assert cmp is operator.gt and thresh == 10e9   # decimal gb = 1e9


def test_size_rides_alongside_a_name():
    name, pred = ss("qwen <=4gb")
    assert name == "qwen"
    cmp, thresh = pred
    assert cmp is operator.le and thresh == 4e9


def test_size_units_and_operators():
    assert ss("<700mb")[1][0] is operator.lt
    assert ss("<700mb")[1][1] == 700e6
    assert ss(">=1tb")[1][1] == 1e12
    assert ss(">500kb")[1][1] == 500e3


def test_size_absent_returns_query_unchanged():
    assert ss("qwen") == ("qwen", None)
    assert ss("") == ("", None)


# ---- date predicates --------------------------------------------------------

def test_date_year_expands_to_jan_first():
    name, pred = sd("qwen>2026")
    assert name == "qwen"
    cmp, date = pred
    assert cmp is operator.gt and date == "2026-01-01"


def test_date_month_and_day_granularities():
    assert sd(">2026-02")[1][1] == "2026-02-01"
    assert sd(">2026-02-15")[1][1] == "2026-02-15"


def test_date_tolerates_spaces_around_operator():
    name, pred = sd("qwen > 2026")
    assert name == "qwen" and pred[1] == "2026-01-01"


def test_date_and_size_compose_order_free():
    # Date split peels the date and LEAVES the size predicate on the name for the
    # size split to take next — so both compose: "qwen>2026-02>5gb".
    name, pred = sd("qwen>2026-02>5gb")
    assert pred[1] == "2026-02-01"
    assert name == "qwen>5gb"
    name2, size = ss(name)
    assert name2 == "qwen" and size[0] is operator.gt and size[1] == 5e9


def test_date_absent_returns_query_unchanged():
    assert sd("qwen") == ("qwen", None)


def test_expand_date_rule():
    assert dyva._expand_date("2026") == "2026-01-01"
    assert dyva._expand_date("2026-02") == "2026-02-01"
    assert dyva._expand_date("2026-02-15") == "2026-02-15"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
