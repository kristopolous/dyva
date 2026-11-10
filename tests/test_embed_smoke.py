"""Embedding-quality smoke gate — pytest (`uv run pytest tests/test_embed_smoke.py -q`).

A host can answer /api/embeddings with a correctly-shaped, fast, deterministic 768-float
vector that carries NO semantic meaning (a honeypot, or a broken model). Those vectors
poison the semantic index and every search ranks by noise. dyva already screens chat hosts
with the #000000 colour smoke; embedding hosts had no equivalent gate, so a fast fake won
every race and every stored vector came from it.

`_embed_quality_ok` is the discriminator: given embeddings of a known-SIMILAR pair and a
known-DISSIMILAR item, a real embedder puts the similar pair measurably closer than the odd
one out. A fake (random vectors) scores ~0 for every pair; a constant embedder (same vector
for everything) scores ~1 for every pair — both have ZERO margin, so both fail. These pin
that discriminator and its degenerate guards — pure, no network.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


# A SIMILAR pair (cos = 0.79, the measured real-nomic dog/puppy number) and a DISSIMILAR
# third vector (cos ~0.30 to each) — exactly the geometry a real embedder produces.
_REAL_A = [1.0, 0.0, 0.0]
_REAL_B = [0.79, math.sqrt(1 - 0.79 ** 2), 0.0]          # cos(A,B) = 0.79
_REAL_C = _unit([0.30, 0.1028, 0.9484])                   # cos(A,C) ~ cos(B,C) ~ 0.30


def test_real_embedder_passes():
    # similar pair clearly closer than the odd one out -> real
    assert dyva._embed_quality_ok(_REAL_A, _REAL_B, _REAL_C) is True


def test_random_vectors_fail():
    # The honeypot (67.219.104.59): well-formed 768-float vectors, but orthogonal noise —
    # every pair ~0, so the 'similar' pair is NOT closer. No structure -> fail.
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    c = [0.0, 0.0, 1.0]
    assert dyva._embed_quality_ok(a, b, c) is False


def test_constant_embedder_fails():
    # A host that returns the SAME vector for every input: cos=1 for every pair, so the
    # similar pair is no closer than the dissimilar one. Zero margin -> fail.
    v = _unit([0.3, 0.5, 0.2])
    assert dyva._embed_quality_ok(list(v), list(v), list(v)) is False


def test_zero_vector_fails():
    assert dyva._embed_quality_ok([0.0, 0.0, 0.0], _REAL_B, _REAL_C) is False


def test_dimension_mismatch_fails():
    assert dyva._embed_quality_ok([1.0, 0.0, 0.0], [1.0, 0.0], [0.0, 1.0, 0.0]) is False


def test_weak_but_real_structure_passes():
    # Not every real embedder is as crisp as nomic; a modest-but-clear margin still passes.
    a = [1.0, 0.0, 0.0]
    b = _unit([0.6, 0.8, 0.0])        # cos(a,b) = 0.60
    c = _unit([0.1, 0.1, 0.99])       # cos ~0.1 to both
    assert dyva._embed_quality_ok(a, b, c) is True


# ---- the gate wired into _embed_text: skip a fake host, use a real one -----------------

import asyncio    # noqa: E402


def _fake_servers(*hosts):
    # find_servers shape: [(prio, host, [resolved_model, ...]), ...]
    return lambda model, caps=None: [(i, h, ["nomic-embed-text:latest"]) for i, h in enumerate(hosts)]


def _real_vec(text):
    t = (text or "").lower()
    if "dog" in t:   return _unit([1.0, 0.05, 0.0])
    if "puppy" in t: return _unit([0.97, 0.24, 0.0])     # cos ~0.98 with dog
    if "tax" in t:   return _unit([0.30, 0.10, 0.95])    # far from dog/puppy
    return _unit([0.0, 1.0, 0.0])                        # the real payload text


_FAKE_ORDER = {}
def _fake_vec(text):
    # fabricated embedder: a distinct orthogonal unit vector per distinct text -> no structure
    idx = _FAKE_ORDER.setdefault(text, len(_FAKE_ORDER))
    v = [0.0] * 16
    v[idx % 16] = 1.0
    return v


def test_embed_text_skips_fake_host_uses_real(monkeypatch):
    FAKE, REAL = "http://fake:1", "http://real:2"
    monkeypatch.setattr(dyva, "find_servers", _fake_servers(FAKE, REAL))
    monkeypatch.setattr(dyva, "_embed_verified", set(), raising=False)
    monkeypatch.setattr(dyva, "_embed_failed", set(), raising=False)
    bad = []
    monkeypatch.setattr(dyva, "add_bad", lambda host, full, reason="": bad.append(host))

    async def fake_embed_one(session, host, full, text):
        return _fake_vec(text) if host == FAKE else _real_vec(text)
    monkeypatch.setattr(dyva, "_embed_one", fake_embed_one)

    # Ask for the real payload text; the gate must route past the fake to the real host.
    vec = asyncio.run(dyva._embed_text(None, "nomic-embed-text", "my trip to thailand"))
    assert vec == _real_vec("my trip to thailand")   # came from the REAL host
    assert FAKE in bad                                # fake condemned per (host,model)
    assert (REAL, "nomic-embed-text:latest") in dyva._embed_verified   # real host cached as good


def test_embed_text_none_when_only_fake_hosts(monkeypatch):
    FAKE = "http://fake:1"
    monkeypatch.setattr(dyva, "find_servers", _fake_servers(FAKE))
    monkeypatch.setattr(dyva, "_embed_verified", set(), raising=False)
    monkeypatch.setattr(dyva, "_embed_failed", set(), raising=False)
    monkeypatch.setattr(dyva, "add_bad", lambda *a, **k: None)

    async def fake_embed_one(session, host, full, text):
        return _fake_vec(text)
    monkeypatch.setattr(dyva, "_embed_one", fake_embed_one)

    # No real embedder -> no vector -> caller falls back to keyword search.
    assert asyncio.run(dyva._embed_text(None, "nomic-embed-text", "anything")) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
