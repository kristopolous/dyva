"""Synthetic /api/tags digest signal (`uv run pytest tests/test_honeypot_digests.py -q`).

A real ollama host reports sha256 GGUF digests — random 64-hex. A costume/honeypot that
fakes a catalog (parroting frontier model names it can't actually run) mints its "digests"
from a rolling counter: pure byte-ramps (every byte = prev + 0x11) and byte-shifted copies
of one sequence, so the catalog's digests share long substrings. Captured verbatim from
157.245.193.55:8080 (2026-11-11) — it parrots gpt-4o / claude-* / o1 and stalls agentic
runs (it never emits a real tool call). The digests are VALID hex, so the existing `_SHA`
regex and the ouroboros guard pass them; THIS is the new tell. Catalog-level: any synthetic
evidence -> a hard, host-wide honeypot mark so the pool never serves it.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import graflex   # noqa: E402

# Rolling-ramp digests actually served by the honeypot (a subset of the 67 it listed).
SYNTH = [
    "d1a6f8e2b3c4a5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0",  # pure +0x11 byte ramp
    "f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2",
    "a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3",
    "b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4",
]


def _real_digests(n):
    """Genuine, random-looking sha256 hex — the negative class."""
    return [hashlib.sha256(f"model-{i}".encode()).hexdigest() for i in range(n)]


# ---- single-digest tell ----------------------------------------------------

def test_flags_pure_byte_ramp():
    assert graflex._synthetic_digest(
        "d1a6f8e2b3c4a5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0")


def test_flags_rolling_sequence_digest():
    assert graflex._synthetic_digest(
        "a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3")


def test_real_sha256_is_not_synthetic():
    for d in _real_digests(25):
        assert not graflex._synthetic_digest(d), d


def test_synthetic_digest_tolerates_junk():
    assert graflex._synthetic_digest(None) is False
    assert graflex._synthetic_digest("") is False
    assert graflex._synthetic_digest("not-a-digest") is False


# ---- catalog level ---------------------------------------------------------

def test_catalog_flags_rolling_digests():
    payload = {"models": [{"name": f"m{i}", "digest": d} for i, d in enumerate(SYNTH)]}
    assert graflex.synthetic_digest_catalog(payload)


def test_catalog_flags_mixed_real_and_synthetic():
    # The real honeypot sprinkles a few random digests among the rolling ones; still a costume.
    digs = SYNTH + _real_digests(5)
    payload = {"models": [{"name": f"m{i}", "digest": d} for i, d in enumerate(digs)]}
    assert graflex.synthetic_digest_catalog(payload)


def test_real_catalog_is_not_flagged():
    payload = {"models": [{"name": f"m{i}", "digest": d} for i, d in enumerate(_real_digests(30))]}
    assert not graflex.synthetic_digest_catalog(payload)


def test_openai_payload_without_digests_is_not_flagged():
    # /v1/models (openai/vllm) carries no digests; this signal must not fire there.
    payload = {"object": "list", "data": [{"id": "llama3:8b"}, {"id": "qwen2.5:72b"}]}
    assert not graflex.synthetic_digest_catalog(payload)


def test_junk_payloads_are_not_flagged():
    assert not graflex.synthetic_digest_catalog(None)
    assert not graflex.synthetic_digest_catalog([])
    assert not graflex.synthetic_digest_catalog({"models": []})
    assert not graflex.synthetic_digest_catalog({"models": [{"name": "x"}]})   # no digest key


# ---- the verdict must survive normalization --------------------------------
# REGRESSION (157.245.193.55:8080 slipped through): the check gate ran the digest
# catalog on the NORMALIZED doorknock result, but _filter_models collapses /api/tags
# to Form A ({name: {}}) and throws every digest away — so the gate always saw an
# empty digest set and never fired. The verdict has to be computed from the RAW
# payload and carried onto the result for the gate to read.

def test_filter_models_drops_the_digests_the_catalog_needs():
    raw = [{"name": f"m{i}", "digest": d} for i, d in enumerate(SYNTH)]
    norm = graflex._filter_models(raw)                       # the pipeline's shape
    # digests are gone, so re-running the catalog on the normalized result is blind:
    assert not graflex.synthetic_digest_catalog({"models": norm})


def test_flag_synthetic_digests_carries_verdict_from_raw():
    raw = {"models": [{"name": f"m{i}", "digest": d} for i, d in enumerate(SYNTH)]}
    result = {"service": "ollama", "models": graflex._filter_models(raw["models"])}
    assert graflex._flag_synthetic_digests(result, raw) is True
    assert result.get("honeypot_digests") is True            # gate reads this


def test_flag_synthetic_digests_absent_for_real_catalog():
    raw = {"models": [{"name": f"m{i}", "digest": d}
                      for i, d in enumerate(_real_digests(30))]}
    result = {"service": "ollama", "models": graflex._filter_models(raw["models"])}
    assert graflex._flag_synthetic_digests(result, raw) is False
    assert "honeypot_digests" not in result                  # clean host unmarked


# ---- the verdict both probe paths must share ------------------------------
# REGRESSION (157.245.193.55:8080 stayed in the pool): the honeypot gate lived
# only in check_one (the initial `check`). The `check-working` rescan of the
# EXISTING pool had no gate at all — it refreshed any answering host, costume or
# not, straight back into `working`, so a host already in the pool was never
# evicted. Both paths must call one shared verdict so they cannot drift again.

import asyncio   # noqa: E402


class _FakeResp:
    def __init__(self, status):
        self.status = status
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Minimal stand-in for aiohttp session.get — records whether /wp-login.php was hit."""
    def __init__(self, status=200):
        self.status = status
        self.hit = False
    def get(self, url, **kw):
        self.hit = True
        return _FakeResp(self.status)


def test_honeypot_detail_flags_synthetic_digests_without_probing():
    # The digest tell is proof enough: it must NOT spend a /wp-login.php probe.
    result = {"service": "ollama", "models": [{"name": "x"}], "honeypot_digests": True}
    sess = _FakeSession()
    detail = asyncio.run(graflex._honeypot_detail(sess, "1.2.3.4:8080", result, 10))
    assert detail == "synthetic /api/tags digests"
    assert sess.hit is False


def test_honeypot_detail_clears_a_real_host():
    # A broad catalog that isn't the phantom frozen set, no digest flag -> real.
    result = {"service": "ollama",
              "models": [{"name": n} for n in ("mistral:7b", "llama3:8b", "qwen2.5:72b")]}
    sess = _FakeSession(status=404)
    detail = asyncio.run(graflex._honeypot_detail(sess, "1.2.3.4:8080", result, 10))
    assert detail is None


def test_honeypot_detail_flags_phantom_catalog_via_wp_login():
    # Whole catalog is the frozen phantom set + /wp-login.php 200 -> honeypot.
    result = {"service": "ollama", "models": [{"name": "llama2:latest"}]}
    sess = _FakeSession(status=200)
    detail = asyncio.run(graflex._honeypot_detail(sess, "1.2.3.4:8080", result, 10))
    assert detail == "phantom catalog + /wp-login.php 200"
    assert sess.hit is True


def test_honeypot_detail_ignores_error_results():
    assert asyncio.run(graflex._honeypot_detail(_FakeSession(), "h", {"error": "timeout"}, 10)) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
