"""The music gallery's backend: GET /v1/music (list) and
GET /v1/music/{id}/delete — pytest
(`uv run pytest tests/test_music_gallery.py -q`).

Music was already an async job subsystem (POST /v1/music, GET /v1/music/{id},
.../content) but had no LIST or DELETE endpoint, so a gallery like the video
tab's had nothing to read. These mirror handle_videos_list / handle_videos_delete:

  - list: every job newest-first, metadata only; `content_url` is set only for a
    completed job that actually wrote a file, so an in-flight or failed job never
    advertises a dead clip URL; in-flight and failed jobs are still listed so the
    gallery can show what is cooking and what went wrong;
  - delete: drop the job and remove its on-disk clip; a bool says whether a job
    by that id was there.

The handlers are driven with aiohttp's make_mocked_request; _MUSIC_JOBS and the
job files are pointed at tmp so nothing touches the real cache.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva                                        # noqa: E402
from aiohttp.test_utils import make_mocked_request  # noqa: E402


def _seed(monkeypatch, tmp_path, jobs):
    monkeypatch.setattr(dyva, "_MUSIC_JOBS", jobs, raising=False)
    monkeypatch.setattr(dyva, "MUSIC_JOBS_FILE", str(tmp_path / "music-jobs.json"), raising=False)
    monkeypatch.setattr(dyva, "MUSIC_JOBS_DIR", str(tmp_path / "music-jobs"), raising=False)


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


def test_list_is_newest_first_with_content_url_only_when_completed(tmp_path, monkeypatch):
    jobs = {
        "m1": {"status": "completed", "style": "lofi", "lyrics": "la la",
               "model": "ace_step.safetensors", "host": "http://h1:8188",
               "family": "AceStep", "seconds": 8, "created": 100.0,
               "content_path": str(tmp_path / "m1.bin")},
        "m2": {"status": "in_progress", "style": "synthwave", "host": "http://h2:8188",
               "created": 200.0},
        "m3": {"status": "failed", "style": "dreampop", "created": 150.0,
               "error": "all unreachable"},
    }
    _seed(monkeypatch, tmp_path, jobs)
    resp = asyncio.run(dyva.handle_music_list(make_mocked_request("GET", "/v1/music")))
    out = _body(resp)

    assert [j["id"] for j in out] == ["m2", "m3", "m1"]            # newest created first
    by = {j["id"]: j for j in out}
    assert by["m1"]["content_url"] == "/v1/music/m1/content"       # completed w/ file
    assert by["m2"]["content_url"] is None                          # in-flight: no dead URL
    assert by["m3"]["content_url"] is None                          # failed: no dead URL
    assert by["m3"]["error"] == "all unreachable"
    assert by["m1"]["style"] == "lofi" and by["m1"]["lyrics"] == "la la"


def test_completed_without_file_has_no_content_url(tmp_path, monkeypatch):
    # a job marked completed but missing its content_path must NOT claim a clip URL
    jobs = {"m9": {"status": "completed", "style": "x", "created": 1.0}}
    _seed(monkeypatch, tmp_path, jobs)
    resp = asyncio.run(dyva.handle_music_list(make_mocked_request("GET", "/v1/music")))
    assert _body(resp)[0]["content_url"] is None


def test_delete_removes_job_and_its_file(tmp_path, monkeypatch):
    clip = tmp_path / "m1.bin"
    clip.write_bytes(b"fake-audio")
    jobs = {"m1": {"status": "completed", "style": "lofi", "created": 1.0,
                   "content_path": str(clip)}}
    _seed(monkeypatch, tmp_path, jobs)

    req = make_mocked_request("GET", "/v1/music/m1/delete", match_info={"id": "m1"})
    resp = asyncio.run(dyva.handle_music_delete(req))

    assert _body(resp) == {"deleted": True}
    assert "m1" not in dyva._MUSIC_JOBS
    assert not clip.exists()


def test_delete_unknown_id_is_false(tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path, {})
    req = make_mocked_request("GET", "/v1/music/nope/delete", match_info={"id": "nope"})
    resp = asyncio.run(dyva.handle_music_delete(req))
    assert _body(resp) == {"deleted": False}
