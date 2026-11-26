import asyncio

import pytest
from test_full_api import PASSWORD
from test_realtime import api as api_fixture
from test_realtime import request
from test_realtime import server as server_fixture

from itmo_queue.smoke import prepare, run_flow

api = api_fixture
server = server_fixture
pytestmark = pytest.mark.asyncio


async def test_reproducible_http_flow_with_real_cookies_and_cors(api, server):
    admin = await request(api.root, "GET", "/me")
    prepared = await asyncio.to_thread(
        prepare, server, admin["login"], PASSWORD, "http://frontend.test"
    )
    report = await asyncio.to_thread(run_flow, prepared)
    assert report["queue_id"] == prepared["queue"]["id"]
    assert "history" in report["checks"]
    assert "credentialed CORS" in report["checks"]
