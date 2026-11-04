"""Per-model stats fold spelling-variants of the same model together — pytest
(`uv run pytest tests/test_stats_model_fold.py -q`).

The Stats "by model" table grouped on the RAW model string, so one model showed up as
many rows: `qwen3.8-27b`, `Qwen3.8-27B`, `qwen3.8:27b`, `HivenetQuant/Qwen3.8-27B` are
all the same thing under case / `-`-vs-`:` / `publisher/` differences. `_fold_models`
collapses them with `_norm_model` (the SAME key routing already uses), summing tokens and
requests, re-meaning tps across the whole group, taking the latest `last`, and displaying
the group's heaviest spelling. Quant/tag/size variants (`-MXFP4`, `:latest`, bare
`qwen3.8`) have different norm keys, so they STAY separate — that's intended.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

# rows are (name, up, down, reqs, tsum, tn, last)


def test_case_colon_publisher_variants_merge():
    rows = [
        ("qwen3.8-27b", 100, 50, 5, 10.0, 1, 1000),
        ("Qwen3.8-27B", 200, 100, 3, 40.0, 1, 2000),
        ("qwen3.8:27b", 10, 5, 1, None, 0, 500),
        ("HivenetQuant/Qwen3.8-27B", 1, 1, 1, None, 0, 100),
    ]
    out = dyva._fold_models(rows)
    assert len(out) == 1
    (name, b), = out.items()
    assert name == "Qwen3.8-27B"              # heaviest spelling by tokens (300 > 150 > 15 > 2)
    assert b["up"] == 311 and b["down"] == 156 and b["reqs"] == 10
    assert b["tps"] == 25.0                    # (10+40) / 2 readings, re-meaned across the group
    assert b["last"] == 2000                   # most recent across the group


def test_quant_tag_size_variants_stay_separate():
    rows = [
        ("qwen3.8-27b", 1, 1, 1, None, 0, 0),
        ("Qwen3.8-MXFP4", 1, 1, 1, None, 0, 0),    # different quant
        ("qwen3.8:latest", 1, 1, 1, None, 0, 0),   # different tag token
        ("qwen3.8", 1, 1, 1, None, 0, 0),          # bare base, no size
        ("dmtx/qwen3.5-9b-abliterated:latest", 1, 1, 1, None, 0, 0),  # different model
    ]
    out = dyva._fold_models(rows)
    assert len(out) == 5


def test_null_tps_group_reports_none():
    rows = [("foo:7b", 5, 5, 2, None, 0, 10), ("FOO-7B", 3, 3, 1, None, 0, 20)]
    out = dyva._fold_models(rows)
    (name, b), = out.items()
    assert b["tps"] is None                    # no readings anywhere in the group
    assert b["up"] == 8 and b["reqs"] == 3 and b["last"] == 20
