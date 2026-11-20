import asyncio
import json
import socket

import httpx
import pytest
from test_full_api import api as full_api_fixture
from test_full_api import request
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from itmo_queue.api.deps import SESSION_COOKIE, session_key

api = full_api_fixture
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def server(api):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    import uvicorn

    from itmo_queue.core.config import get_settings
    from itmo_queue.main import create_app

    application = create_app(
        get_settings().model_copy(
            update={"SESSION_COOKIE_SECURE": False, "cors_origins": ["http://frontend.test"]}
        )
    )
    runtime = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    )
    task = asyncio.create_task(runtime.serve())
    try:
        async with asyncio.timeout(10):
            while not runtime.started:
                if task.done():
                    await task
                    raise RuntimeError("Server failed to start")
                await asyncio.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        runtime.should_exit = True
        await asyncio.wait_for(task, timeout=10)


def cookie(client):
    return {"Cookie": f"{SESSION_COOKIE}={client.cookies[SESSION_COOKIE]}"}


async def receive_kind(socket, kind):
    async with asyncio.timeout(10):
        while True:
            message = json.loads(await socket.recv())
            if message["type"] == kind:
                return message


async def test_websocket_swap_and_revocation(api, server):
    a = api
    for i in range(6):
        await a.enroll(i)
    path = server.replace("http://", "ws://") + "/api/v1/ws/me"
    async with (
        connect(path, proxy=None, additional_headers=cookie(a.students[3][1])) as left,
        connect(path, proxy=None, additional_headers=cookie(a.students[4][1])) as right,
    ):
        await left.send(
            json.dumps({"type": "switch.propose", "queueId": a.q["id"], "targetQueueNumber": 5})
        )
        incoming = await receive_kind(right, "switch.incoming")
        offer_id = incoming["request"]["swap_id"]
        await right.send(json.dumps({"type": "switch.accept", "requestId": offer_id}))
        resolved = await receive_kind(left, "switch.resolved")
        assert resolved["request"]["status"] == "accepted"
        state = await request(a.students[3][1], "GET", a.base)
        assert state["my_entry"]["position"] == 5
        await right.send("[]")
        assert (await receive_kind(right, "error"))["status"] == 422
        # A logged-out connection loses access without needing to reconnect.
        token = a.students[4][1].cookies[SESSION_COOKIE]
        await a.app.state.redis.delete(session_key(token))
        async with asyncio.timeout(5):
            with pytest.raises(ConnectionClosed):
                while True:
                    await right.recv()


async def test_websocket_rejects_missing_cookie_and_foreign_origin(api, server):
    path = server.replace("http://", "ws://") + "/api/v1/ws/me"
    with pytest.raises(InvalidStatus):
        async with connect(path, proxy=None):
            pass
    with pytest.raises(InvalidStatus):
        async with connect(
            path, origin="https://foreign.example", additional_headers=cookie(api.tc)
        ):
            pass


async def read_sse(lines, event_name):
    async with asyncio.timeout(10):
        event = {}
        async for line in lines:
            if line.startswith("id: "):
                event["id"] = int(line[4:])
            elif line.startswith("event: "):
                event["type"] = line[7:]
            elif line.startswith("data: "):
                event["data"] = json.loads(line[6:])
            elif line == "":
                if event.get("type") == event_name:
                    return event
                event = {}
    raise AssertionError("SSE event not received")


async def test_sse_snapshot_event_replay_and_privacy(api, server):
    a = api
    url = server + "/api/v1" + a.base + "/stream"
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        async with client.stream("GET", url, headers=cookie(a.students[0][1])) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.aiter_lines()
            snapshot = await read_sse(lines, "snapshot")
            assert snapshot["data"]["students"] == []
            await a.enroll(1)
            event = await read_sse(lines, "queue.updated")
            assert len(event["data"]["students"]) == 1
            assert event["data"]["students"][0]["works"][0]["artifact_url"] is None
            cursor = event["id"]
        await a.enroll(2)
        async with client.stream(
            "GET", url, headers={**cookie(a.students[0][1]), "Last-Event-ID": str(cursor)}
        ) as response:
            lines = response.aiter_lines()
            await read_sse(lines, "snapshot")
            event = await read_sse(lines, "queue.updated")
            assert event["id"] > cursor and len(event["data"]["students"]) == 2
        cors = await client.options(
            server + "/api/v1/me",
            headers={"Origin": "http://frontend.test", "Access-Control-Request-Method": "GET"},
        )
        assert cors.headers["access-control-allow-credentials"] == "true"
        unauth = await client.get(server + "/api/v1/me")
        assert unauth.status_code == 401
