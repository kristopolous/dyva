"""`_messages_openai` must hand a host image_url parts it can actually use. A strict
OpenAI host (LM Studio) 400s "'url' field must be a base64 encoded image" when an
image_url carries a plain URL — it can't fetch our relative/local URL. So: a bare
`images` field becomes data: URLs, a local store URL is inlined to base64, and any
other non-data URL degrades to a text note instead of failing the whole host.

Run: .venv/bin/python -m pytest tests/test_vision_image_url.py -q
"""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

PNG = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                  "0000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082")
).decode()


def _parts(msgs):
    return msgs[0]["content"]


def test_bare_images_become_data_urls():
    out = dyva._messages_openai([{"role": "user", "content": "hi", "images": [PNG]}])
    parts = _parts(out)
    iu = next(p for p in parts if p.get("type") == "image_url")
    assert iu["image_url"]["url"].startswith("data:image/png;base64,")
    assert "images" not in out[0]


def test_data_url_image_part_is_left_alone():
    durl = "data:image/png;base64," + PNG
    out = dyva._messages_openai([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": durl}}]}])
    assert _parts(out)[0]["image_url"]["url"] == durl


def test_http_image_url_degrades_to_text_note_not_a_bad_url():
    out = dyva._messages_openai([{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "http://example.com/cat.png"}}]}])
    parts = _parts(out)
    assert all(p.get("type") != "image_url" for p in parts)       # no bare url survives
    assert any(p["type"] == "text" and "image not shown" in p["text"] for p in parts)


def test_local_store_url_is_inlined_to_base64(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "IMG_DIR", str(tmp_path))
    open(os.path.join(str(tmp_path), "garden.png"), "wb").write(base64.b64decode(PNG))
    for url in ("sdapi/v1/images/garden.png",
                "https://9ol.es/11434/sdapi/v1/images/garden.png"):
        out = dyva._messages_openai([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": url}}]}])
        iu = _parts(out)[0]
        assert iu["type"] == "image_url"
        assert iu["image_url"]["url"].startswith("data:image/png;base64,")


def test_missing_local_image_degrades_to_note():
    out = dyva._messages_openai([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "sdapi/v1/images/nope.png"}}]}])
    assert all(p.get("type") != "image_url" for p in _parts(out))


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
