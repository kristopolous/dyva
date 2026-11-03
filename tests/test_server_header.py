"""The HTTP Server: header carries the dyva build — pytest
(`uv run pytest tests/test_server_header.py`).

A bare `curl -I` should reveal which build is answering (same string as
/api/version), not the stock `Python/aiohttp`. Set via on_response_prepare so it
also stamps streaming responses, and so aiohttp's default Server doesn't win.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva                                        # noqa: E402
from aiohttp import web                            # noqa: E402
from aiohttp.test_utils import make_mocked_request, TestServer, TestClient  # noqa: E402


def test_stamp_server_sets_dyva_version():
    resp = web.Response()
    asyncio.run(dyva._stamp_server(make_mocked_request("GET", "/"), resp))
    assert resp.headers["Server"] == f"DYVA-{dyva.VERSION}"


def test_server_header_survives_aiohttp_default():
    # The real check: through a live prepare(), our value must beat aiohttp's stock Server.
    async def go():
        app = web.Application()
        async def _h(req):
            return web.Response(text="ok")
        app.router.add_get("/", _h)
        app.on_response_prepare.append(dyva._stamp_server)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/")
            return r.headers.get("Server")
    assert asyncio.run(go()) == f"DYVA-{dyva.VERSION}"
