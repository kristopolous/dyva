"""Decision-host request-level rejection classification — pytest
(`uv run pytest tests/test_decision_reject.py -q`).

A decision query (/v1/systemone) that comes back non-200 is EITHER the host failing
(down, crashed runner, 502, wrong model) OR the host rejecting THIS request because it's
too big for the host's fixed limit (413 payload/schema over cap, 500 "context size
exceeded"). The second kind is identical on every host — it's OUR payload that's oversized —
so penalising it walks one oversized public request across the whole pool marking every
decision host bad. That class must SKIP (record nothing), exactly like the existing 400
handler. _decision_is_request_reject draws that line; genuine host failures must NOT match.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


def test_413_payload_too_large_is_request_reject():
    # The dominant wrongly-bad bucket: host is up, has the model, rejects the oversized body.
    assert dyva._decision_is_request_reject(
        413, '{"error":"text and schema must not exceed 64 KiB"}')


def test_413_any_body_is_request_reject():
    # 413 is request-too-large regardless of the exact wording.
    assert dyva._decision_is_request_reject(413, "")
    assert dyva._decision_is_request_reject(413, "Request Entity Too Large")


def test_500_context_exceeded_is_request_reject():
    # Semantically identical to 413: the input didn't fit, the host is fine.
    assert dyva._decision_is_request_reject(
        500, '{"error":"Context size has been exceeded."}')


def test_500_oom_terminated_runner_is_not_request_reject():
    # The runner actually died — a real (if self-inflicted) host failure, NOT a clean
    # request rejection. Must keep penalising so it isn't fronted as healthy.
    assert not dyva._decision_is_request_reject(
        500, '{"error":"llama-server process has terminated: exit status 1: '
             'ggml_aligned_malloc: insufficient memory"}')
    assert not dyva._decision_is_request_reject(
        500, '{"error":"llama-server process has terminated: exit status 1: '
             'cudaMalloc failed: out of memory"}')


def test_502_bad_gateway_is_not_request_reject():
    # Genuinely down (nginx upstream dead) — must stay a host failure.
    assert not dyva._decision_is_request_reject(
        502, "<html><head><title>502 Bad Gateway</title></head></html>")


def test_404_model_not_found_is_not_request_reject():
    assert not dyva._decision_is_request_reject(
        404, '{"error":"model \'nimble-v3:latest\' not found"}')


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
