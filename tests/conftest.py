"""Shared pytest fixtures.

The find_servers reputation loaders (load_bad/good/maybe via _keys_with_state,
load_incompatible, load_unreachable, load_attested_honeypots, load_attested_pass) carry
module-level TTL caches (cachetools) so a request burst doesn't rebuild ~19k-element sets
every call. Tests swap dyva._status_db to isolated per-test databases, so without a reset a
set cached from one test's DB would leak into the next within the TTL window. Clear them
before every test — the same reason tests reset _status_db itself.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _clear_reputation_caches():
    try:
        import dyva
    except Exception:
        yield
        return
    _reset_rep_caches(dyva)
    yield


def _reset_rep_caches(dyva):
    for name in ("_keys_with_state", "load_incompatible", "load_unreachable"):
        fn = getattr(dyva, name, None)
        cache = getattr(fn, "cache", None)
        if cache is not None:
            cache.clear()
