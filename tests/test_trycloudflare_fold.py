"""Collapse all *.trycloudflare.com tunnels in the models card — pytest
(`uv run pytest tests/test_trycloudflare_fold.py -q`).

Cloudflare quick-tunnels get a fresh random hostname (loan-violin-cute-occurred…)
every restart, and a single ollama behind one can advertise a huge model list. The
models card's existing defences key on the model NAME — the junk/stub/fake-name
suppression and the GGUF-digest rename-family collapse — so an ephemeral tunnel with
plausible, varied names and churning hostnames sprawls the list and inflates per-model
host counts. "Another way to do this" keys on the HOST DOMAIN instead: every model that
lives ONLY on *.trycloudflare.com folds into a single 'trycloudflare.com' row.

Two pure units carry the rule:
  * _is_trycloudflare(host): is this pool `server` value an ephemeral CF quick-tunnel?
    Matches the real domain suffix in bare host, host:port and URL forms, and must NOT
    be fooled by a lookalike (nottrycloudflare.com, …trycloudflare.com.evil.net).
  * _trycloudflare_fold(model_hosts): a model is tunnel-only iff it has >=1 host and
    EVERY host is such a tunnel. A model also served by a real host is NOT folded (its
    real presence stays visible). Returns the tunnel-only names and the union of their
    tunnel hosts (the fold row's host count).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva    # noqa: E402


def test_is_trycloudflare_matches_tunnel_forms():
    assert dyva._is_trycloudflare("https://loan-violin-cute-occurred.trycloudflare.com")
    assert dyva._is_trycloudflare("loan-violin-cute-occurred.trycloudflare.com")
    assert dyva._is_trycloudflare("https://a.b.trycloudflare.com:443/v1")
    assert dyva._is_trycloudflare("trycloudflare.com")


def test_is_trycloudflare_rejects_lookalikes_and_real_hosts():
    assert not dyva._is_trycloudflare("nottrycloudflare.com")              # no dot boundary
    assert not dyva._is_trycloudflare("x.trycloudflare.com.attacker.net")  # not the suffix
    assert not dyva._is_trycloudflare("https://184.170.166.250:1234")      # a real host
    assert not dyva._is_trycloudflare("ollama.example.org:11434")
    assert not dyva._is_trycloudflare("")
    assert not dyva._is_trycloudflare(None)


def test_fold_takes_models_that_live_only_on_tunnels():
    model_hosts = {
        # only on tunnels (two different ephemeral hostnames) -> folded
        "junk-a": {"https://one.trycloudflare.com", "https://two.trycloudflare.com"},
        "junk-b": {"https://two.trycloudflare.com"},
        # on a tunnel AND a real host -> NOT folded, its real row stands
        "qwen3-8b": {"https://three.trycloudflare.com", "https://10.0.0.5:11434"},
        # only on real hosts -> never considered
        "llama3.3-70b": {"https://10.0.0.6:11434"},
    }
    tunnel_only, host_union = dyva._trycloudflare_fold(model_hosts)
    assert tunnel_only == {"junk-a", "junk-b"}
    # host-count of the folded row is the UNION of those names' tunnel hosts
    assert host_union == {"https://one.trycloudflare.com", "https://two.trycloudflare.com"}


def test_fold_ignores_models_with_no_hosts():
    tunnel_only, host_union = dyva._trycloudflare_fold({"ghost": set()})
    assert tunnel_only == set()
    assert host_union == set()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
