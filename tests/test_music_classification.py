"""Music-generation checkpoints must bucket as `music` (an `_AUDIO_CLASSES`
member), not scatter into `image`/unclassified — pytest
(`uv run pytest tests/test_music_classification.py -q`).

The bug this pins: the model-classifier's `music` bucket was only
`(ace_step|acestep)`, so every other text-to-music family fell through to the
`image` catch-all (`\\.safetensors$`, the last bucket) when it had a file
extension, or to unclassified (None) when it didn't. minimax_music3, musicgen,
audiocraft, songbloom and YuE/YuE2 all misclassified this way — e.g.
`minimax_music3_dit.safetensors -> image` and bare `minimax_music3 -> None`.
Because `_find_music_hosts` keys off `_AUDIO_CLASSES = {audio, music, t2a}`,
those hosts were invisible to music routing, so a music request found far fewer
candidate hosts than actually existed.

The fix is a SELECTIVE one: each family is an anchored, specific token added to
the `music` bucket (which already precedes `image`), never a broad pattern —
the opposite failure (an image checkpoint stolen into `music`) is just as real,
since `image` is only `\\.safetensors$`. The selectivity guard below asserts
ordinary image/video checkpoints keep their class.

Integration test against the real graflex/model-classifier.json (the file dyva
re-reads at query time), so it fails until that file is fixed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402


# Real music-generation checkpoints, as they appear on ComfyUI hosts — both the
# extensioned path form and the bare name the dashboard model list also shows.
MUSIC_NAMES = [
    "ace_step_v1_3.5b.safetensors",          # already worked; regression guard
    "minimax_music3_dit.safetensors",
    "minimax_music3",                        # bare (no extension) -> was None
    "musicgen_large.safetensors",
    "audiocraft_musicgen.safetensors",
    "songbloom_full.safetensors",
    "yue2_3b.safetensors",
    "yue2",                                  # bare -> was None
    "YuE2-3B",                               # display name, mixed case + dash
    "yue_s1_7b_anneal_en_cot.safetensors",
]

# Models that must NOT be dragged into music by an over-broad pattern. These are
# the selectivity guard: each is a genuine image or video checkpoint.
NON_MUSIC_NAMES = {
    "sd_xl_base_1.0.safetensors": "image",
    "flux1-dev.safetensors": "image",
    "z-image-turbo.safetensors": "image",
    "wan2.1_t2v_1.3b.safetensors": "video",
}


def test_music_families_bucket_as_music():
    for name in MUSIC_NAMES:
        assert dyva.classify_model(name) == "music", name


def test_music_families_land_in_an_audio_routing_class():
    # what _find_music_hosts actually keys off: model_classes() must intersect
    # _AUDIO_CLASSES, or the host is never a music candidate.
    for name in MUSIC_NAMES:
        assert dyva.model_classes(name) & dyva._AUDIO_CLASSES, name


def test_image_and_video_checkpoints_are_not_stolen_into_music():
    for name, expected in NON_MUSIC_NAMES.items():
        got = dyva.classify_model(name)
        assert got == expected, f"{name}: {got} != {expected}"
        assert "music" not in dyva.model_classes(name), name
