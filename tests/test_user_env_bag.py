"""The api-key field as a `k=v;` env-var bag → a stable userhash
(`uv run pytest tests/test_user_env_bag.py -q`).

Foundation of the opt-in user-sessions feature (see brainstorm/user-account.md). The public
demo ignores the api-key field today, so we repurpose it: a client sends
`user=chris;pass=whatever` (both the delimiter form and `pass` being OPTIONAL are the point),
and we derive an opaque, stable `userhash` to key that user's stored sessions. No `user` key
means no opt-in — nothing is kept, exactly as today. These two functions are pure; the header
plumbing that feeds them the raw api-key string is tested separately.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# ---- parsing the bag -------------------------------------------------------

def test_parses_user_and_pass():
    assert dyva._parse_env_bag("user=chris;pass=secret") == {"user": "chris", "pass": "secret"}


def test_pass_is_optional():
    assert dyva._parse_env_bag("user=chris") == {"user": "chris"}


def test_trims_whitespace_and_lowercases_keys_only():
    # keys are normalised (forgiving for a human-typed field); values are preserved verbatim.
    assert dyva._parse_env_bag("  User = Chris ; Pass = Hunter2 ") == {
        "user": "Chris", "pass": "Hunter2"}


def test_blank_and_none_are_empty():
    assert dyva._parse_env_bag("") == {}
    assert dyva._parse_env_bag(None) == {}


def test_drops_malformed_segments_and_trailing_semicolon():
    assert dyva._parse_env_bag("justtext;user=bob;") == {"user": "bob"}


def test_value_may_contain_equals():
    # split on the FIRST '=' only, so a value with '=' in it survives.
    assert dyva._parse_env_bag("user=a=b") == {"user": "a=b"}


def test_empty_value_is_kept_empty():
    assert dyva._parse_env_bag("user=chris;pass=") == {"user": "chris", "pass": ""}


# ---- deriving the userhash -------------------------------------------------

def test_userhash_is_stable_and_opaque():
    h1 = dyva._userhash_from_bag({"user": "chris", "pass": "x"})
    h2 = dyva._userhash_from_bag({"user": "chris", "pass": "x"})
    assert h1 and h1 == h2                 # deterministic
    assert "chris" not in h1               # opaque (hashed, not the raw name)


def test_no_user_means_no_identity():
    # no opt-in without a user: these all yield None (nothing gets stored for them).
    assert dyva._userhash_from_bag({}) is None
    assert dyva._userhash_from_bag({"pass": "x"}) is None
    assert dyva._userhash_from_bag({"user": ""}) is None


def test_pass_absent_equals_pass_empty():
    # pass optional: a bare user and user+empty-pass are the SAME identity.
    assert (dyva._userhash_from_bag({"user": "chris"})
            == dyva._userhash_from_bag({"user": "chris", "pass": ""}))


def test_different_pass_is_a_different_bucket():
    assert (dyva._userhash_from_bag({"user": "bob", "pass": "1"})
            != dyva._userhash_from_bag({"user": "bob", "pass": "2"}))


def test_user_pass_boundary_is_unambiguous():
    # ('a','bc') and ('ab','c') must not collide — the join needs a real separator.
    assert (dyva._userhash_from_bag({"user": "a", "pass": "bc"})
            != dyva._userhash_from_bag({"user": "ab", "pass": "c"}))


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
