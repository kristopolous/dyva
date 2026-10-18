"""Stopping a committed ComfyUI render actually stops it — pytest.

Before this, every media worker registered stop=lambda:None, and comfy_collect had
no cancel signal: clicking Stop on a flux job left the render running to completion
on the host and the worker card latched on "stopping…". These pin the two cancel
paths and the host-side interrupt:

  * the Event path (edit): a render awaited after host selection, stopped via the
    `cancel` Event armed onto the worker — interrupts the host, raises ComfyCancelled.
  * the task-cancellation path (txt2img and friends): a render inside a race attempt
    task, stopped when _race_hosts cancels the task — the wrapper fires a /interrupt
    so the host doesn't keep rendering.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva  # noqa: E402


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data if data is not None else {}

    async def release(self):
        pass

    async def json(self, content_type=None):
        return self._data

    async def read(self):
        return b""


class FakeSession:
    """Records every POST/GET so a test can assert what hit the host."""
    def __init__(self, history_data=None):
        self.calls = []
        self.history_data = history_data or {}

    async def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return FakeResp(200, {})

    async def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        if "/history/" in url:
            return FakeResp(200, self.history_data)
        if url.endswith("/queue"):
            return FakeResp(200, {"queue_running": [], "queue_pending": []})
        return FakeResp(200, {})

    def posts(self):
        return [(u, j) for (m, u, j) in self.calls if m == "POST"]


def test_comfy_cancelled_is_not_a_comfy_error():
    # The handlers' `except ComfyError` legs record a bad verdict and fail the host
    # over to the next one. A user stop is not a host fault, so it must not be caught
    # there — hence ComfyCancelled is a plain Exception, not a ComfyError.
    assert not issubclass(dyva.ComfyCancelled, dyva.ComfyError)


def test_comfy_interrupt_hits_interrupt_and_queue_delete():
    sess = FakeSession()
    asyncio.run(dyva.comfy_interrupt(sess, "1.2.3.4:8188", "pid-1"))
    posts = sess.posts()
    assert any(u.endswith("/interrupt") for u, _ in posts)
    assert any(u.endswith("/queue") and j == {"delete": ["pid-1"]} for u, j in posts)


def test_comfy_interrupt_without_prompt_id_only_interrupts():
    sess = FakeSession()
    asyncio.run(dyva.comfy_interrupt(sess, "1.2.3.4:8188"))
    posts = sess.posts()
    assert any(u.endswith("/interrupt") for u, _ in posts)
    assert not any(u.endswith("/queue") for u, _ in posts)   # nothing to delete


def test_collect_cancel_event_interrupts_host_and_raises():
    # The edit path: a preset cancel Event means Stop was hit. collect must interrupt
    # the host (free its GPU) and raise ComfyCancelled rather than wait out the render.
    sess = FakeSession()
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(dyva.ComfyCancelled):
        asyncio.run(dyva.comfy_collect(sess, "1.2.3.4:8188", "pid-9", ["images"],
                                       timeout=30, poll=2, cancel=cancel))
    posts = sess.posts()
    assert any(u.endswith("/interrupt") for u, _ in posts)
    assert any(u.endswith("/queue") and j == {"delete": ["pid-9"]} for u, j in posts)


def test_tts_collect_forwards_cancel():
    # Speech renders sync (awaited inline), so its Stop drops the render the same
    # way edit does — _tts_collect must thread the cancel Event into comfy_collect.
    sess = FakeSession()
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(dyva.ComfyCancelled):
        asyncio.run(dyva._tts_collect(sess, "1.2.3.4:8188", "pid-tts",
                                      timeout=30, cancel=cancel))
    assert any(u.endswith("/interrupt") for u, _ in sess.posts())


def test_collect_task_cancel_fires_host_interrupt():
    # The txt2img path: the render runs inside a race attempt task with no cancel
    # Event; _race_hosts cancels the task on Stop. The wrapper must still interrupt
    # the host so it doesn't keep rendering an image nobody will collect.
    async def run():
        sess = FakeSession()               # history stays empty -> collect keeps polling
        t = asyncio.ensure_future(
            dyva.comfy_collect(sess, "1.2.3.4:8188", "pid-7", ["images"],
                               timeout=30, poll=5))
        await asyncio.sleep(0.05)          # let it enter the poll wait
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        await asyncio.sleep(0.05)          # let the detached interrupt task run
        urls = [u for u, _ in sess.posts()]
        assert any(u.endswith("/interrupt") for u in urls)

    asyncio.run(run())
