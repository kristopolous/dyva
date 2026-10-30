"""The out-of-band OpenRouter price refresher — pytest.

model-prices.json is curated reference data that ships in the package and is only
READ at runtime. It's refreshed out-of-band by `python -m dyva.refresh_prices`,
which pulls the OpenRouter catalog and rewrites the file for a human to commit.

The one rule that must not drift: the refresher buckets OpenRouter models by the
SAME family match dyva uses to LOOK a price up (dyva.price_family_key) — otherwise
the updater would group models one way and the live lookup match them another.
These tests pin that shared rule and the build() transform, with synthetic data
(no network).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva                       # noqa: E402
from dyva import refresh_prices as rp   # noqa: E402


def test_family_key_longest_first():
    fams = ["granite", "ibm-granite", "qwen"]
    assert dyva.price_family_key("ibm-granite-3b", fams) == "ibm-granite"  # longest wins
    assert dyva.price_family_key("Qwen2.5:7b", fams) == "qwen"             # case-insensitive
    assert dyva.price_family_key("mistral-7b", fams) is None               # no family


def test_price_of_uses_shared_rule(monkeypatch):
    monkeypatch.setattr(dyva, "_model_prices",
                        {"default": {"prompt": 1.0, "completion": 2.0},
                         "families": {"qwen": {"prompt": 0.1, "completion": 0.2}}})
    assert dyva._price_of("qwen3:8b") == {"prompt": 0.1, "completion": 0.2}
    assert dyva._price_of("something-else") == {"prompt": 1.0, "completion": 2.0}


def test_per_mtok_converts_per_token_string():
    # OpenRouter quotes USD per TOKEN as a string; dyva stores USD per MILLION tokens.
    # (raw float carries binary-fp noise; build() rounds to 3 decimals downstream.)
    assert rp.per_mtok("0.0000002") == pytest.approx(0.2)
    assert rp.per_mtok("0.000001") == pytest.approx(1.0)


def test_build_buckets_median_excludes_free_keeps_thin_families():
    existing = {"_comment": "x",
                "_match_note": "y",
                "_default": {"prompt": 9.9, "completion": 9.9},
                "families": {"qwen": {"prompt": 0.0, "completion": 0.0},
                             "llama": {"prompt": 5.0, "completion": 5.0}}}
    models = [
        {"id": "qwen/qwen3-8b",       "pricing": {"prompt": "0.0000002", "completion": "0.0000006"}},  # 0.2 / 0.6
        {"id": "qwen/qwen2.5-72b",    "pricing": {"prompt": "0.0000004", "completion": "0.0000010"}},  # 0.4 / 1.0
        {"id": "qwen/qwen3-8b:free",  "pricing": {"prompt": "0",         "completion": "0"}},          # free -> excluded
        {"id": "meta-llama/llama-3.3-70b", "pricing": {"prompt": "0.0000009", "completion": "0.0000009"}},  # 1 sample
        {"id": "openai/gpt-6",        "pricing": {"prompt": "0.000005", "completion": "0.000015"}},    # no family -> ignored
    ]
    out, changed = rp.build(models, existing)

    # qwen: median([0.2, 0.4]) = 0.3 prompt, median([0.6, 1.0]) = 0.8 completion
    assert out["families"]["qwen"] == {"prompt": 0.3, "completion": 0.8}
    # llama: only one priced sample (< MIN_SAMPLES) -> curated value kept, not clobbered
    assert out["families"]["llama"] == {"prompt": 5.0, "completion": 5.0}
    # curated meta preserved; provenance stamped
    assert out["_default"] == {"prompt": 9.9, "completion": 9.9}
    assert out["_comment"] == "x" and out["_match_note"] == "y"
    assert out["_source"] == "openrouter.ai/api/v1/models"
    assert "_generated" in out
    # the family that actually moved is reported; the kept one is not
    assert "qwen" in changed and "llama" not in changed


def test_main_writes_file_from_saved_catalog(tmp_path, monkeypatch):
    """The path `dyva --refresh-prices CATALOG` drives: main() reads a catalog file,
    rewrites MODEL_PRICES_FILE in place, and the result reloads as valid JSON."""
    prices = tmp_path / "model-prices.json"
    prices.write_text(json.dumps({
        "_comment": "keep me",
        "_default": {"prompt": 1.0, "completion": 1.0},
        "families": {"qwen": {"prompt": 0.0, "completion": 0.0}}}))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"data": [
        {"id": "qwen/qwen3-8b",    "pricing": {"prompt": "0.0000002", "completion": "0.0000006"}},
        {"id": "qwen/qwen2.5-72b", "pricing": {"prompt": "0.0000004", "completion": "0.0000010"}}]}))

    monkeypatch.setattr(rp, "MODEL_PRICES_FILE", str(prices))
    rc = rp.main(["dyva --refresh-prices", str(catalog)])

    assert rc == 0
    written = json.loads(prices.read_text())
    assert written["families"]["qwen"] == {"prompt": 0.3, "completion": 0.8}
    assert written["_comment"] == "keep me"                 # curated meta survives
    assert written["_source"] == "openrouter.ai/api/v1/models"


def test_build_preserves_family_order_and_leaves_unmatched_families_alone():
    existing = {"_default": {"prompt": 1.0, "completion": 1.0},
                "families": {"deepseek": {"prompt": 1.0, "completion": 1.0},
                             "gemma": {"prompt": 2.0, "completion": 2.0}}}
    out, changed = rp.build([], existing)   # empty catalog -> nothing to update
    assert list(out["families"]) == ["deepseek", "gemma"]      # order preserved
    assert out["families"]["deepseek"] == {"prompt": 1.0, "completion": 1.0}  # untouched
    assert changed == {}
