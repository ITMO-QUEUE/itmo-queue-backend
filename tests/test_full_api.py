"""Real PostgreSQL + Redis + HTTP cookies; no authentication/DB overrides."""

import asyncio
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from itmo_queue.api.deps import SESSION_COOKIE, session_key
from itmo_queue.core.config import get_settings
from itmo_queue.core.security import get_password_hash
from itmo_queue.db.models.user import User
from itmo_queue.main import create_app
from itmo_queue.models import Attempt, QueueEntry, QueueEvent, SwapOffer

pytestmark = pytest.mark.asyncio
PASSWORD = "пароль-длиннее-72-байт-" * 5


async def request(client, method, path, code=200, **kwargs):
    response = await client.request(method, "/api/v1" + path, **kwargs)
    assert response.status_code == code, response.text
    return response.json() if response.content else None


@pytest.fixture
async def api():
    app = create_app(get_settings().model_copy(update={"SESSION_COOKIE_SECURE": False}))
    async with app.router.lifespan_context(app), AsyncExitStack() as stack:
        async with app.state.session_factory() as db:
            admin = User(
                name="Admin",
                login=uuid.uuid4().hex,
                role="admin",
                password_hash=get_password_hash(PASSWORD),
            )
            db.add(admin)
            await db.commit()

        async def client_for(user):
            client = await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            )
            await request(
                client, "POST", "/auth/login", json={"login": user["login"], "password": PASSWORD}
            )
            assert client.cookies[SESSION_COOKIE]
            return client

        root = await client_for({"login": admin.login})

        async def create_user(role, **extra):
            user = await request(
                root,
                "POST",
                "/auth/register",
                201,
                json={
                    "name": role,
                    "login": uuid.uuid4().hex,
                    "password": PASSWORD,
                    "role": role,
                    **extra,
                },
            )
            return user, await client_for(user)

        teacher, tc = await create_user("teacher", can_create_queues=True)
        teacher2, tc2 = await create_user("teacher", can_create_queues=True)
        discipline = await request(
            root, "POST", "/disciplines", 201, json={"name": uuid.uuid4().hex}
        )
        for t in (teacher, teacher2):
            await request(
                root, "PATCH", "/users/" + t["id"], json={"discipline_ids": [discipline["id"]]}
            )
        works = [
            await request(
                root,
                "POST",
                "/works",
                201,
                json={"discipline_id": discipline["id"], "title": f"Lab {i}", "position_no": i},
            )
            for i in (1, 2)
        ]
        students = [await create_user("student") for _ in range(6)]
        now = datetime.now(UTC)

        queue_payloads = {}

        async def queue(**extra):
            stream_ids = extra.pop("stream_ids", [])
            result = await request(
                tc,
                "POST",
                "/queues",
                201,
                json={
                    "title": "Programming",
                    "discipline_id": discipline["id"],
                    "planned_start": now.isoformat(),
                    "planned_end": (now + timedelta(hours=2)).isoformat(),
                    "teacher_ids": [teacher["id"], teacher2["id"]],
                    "works": [
                        {"title": w["title"], "priority": 2 - i} for i, w in enumerate(works)
                    ],
                    "auditorium": "Room 101",
                    "quota": 4,
                    "registration_open": True,
                    **extra,
                },
            )
            await request(tc, "PATCH", "/queues/" + result["id"], json={"stream_ids": stream_ids})
            by_priority = {w["priority"]: w["work_id"] for w in result["works"]}
            queue_payloads[result["id"]] = {
                "works": [
                    {"work_id": by_priority[2 - i], "artifact_url": f"https://example.org/{i}"}
                    for i in range(2)
                ]
            }
            return result

        q = await queue()
        base = "/queues/" + q["id"]
        payload = queue_payloads[q["id"]]
        for i, w in enumerate(works):
            w["id"] = payload["works"][i]["work_id"]

        async def enroll(index, url=base):
            return await request(
                students[index][1],
                "POST",
                url + "/entries",
                201,
                json=queue_payloads[url.rsplit("/", 1)[-1]],
            )

        yield SimpleNamespace(
            app=app,
            root=root,
            teacher=teacher,
            teacher2=teacher2,
            tc=tc,
            tc2=tc2,
            students=students,
            works=works,
            discipline=discipline,
            queue=queue,
            q=q,
            base=base,
            payload=payload,
            enroll=enroll,
            create_user=create_user,
        )


async def test_cookie_multiwork_privacy_and_filters(api):
    a = api
    # Admin-created students may have no ISU; the legacy list must still serialize them.
    await request(a.root, "GET", "/students")
    entry = await a.enroll(0)
    assert len(entry["works"]) == 2
    assert [w["priority"] for w in entry["works"]] == [1, 2]
    assert entry["is_top3"] is True
    own = await request(a.students[0][1], "GET", a.base)
    assert own["my_entry"]["works"][0]["artifact_url"]
    other = await request(a.students[1][1], "GET", a.base)
    assert other["students"][0]["works"][0]["artifact_url"] is None
    assert "password" not in str(other) and "login" not in str(other)
    assert len(await request(a.students[0][1], "GET", "/me/queues")) >= 1
    found = await request(a.tc, "GET", "/queues", params={"search": "Programming"})
    assert a.q["id"] in [q["id"] for q in found]
    await request(a.students[0][1], "GET", "/students/" + a.students[1][0]["id"] + "/history", 403)
    await request(a.students[0][1], "POST", a.base + "/entries", 409, json=a.payload)
    await request(a.students[0][1], "DELETE", a.base + "/entries/me", 403)
    await request(
        a.students[1][1],
        "POST",
        a.base + "/entries",
        422,
        json={"works": [{"work_id": a.works[0]["id"], "artifact_url": "not a url"}]},
    )


async def test_concurrent_calls_results_no_show_history_and_finish(api):
    a = api
    await a.enroll(0)
    await a.enroll(1)
    await request(a.tc, "POST", a.base + "/call-next", 409)
    await request(a.tc2, "POST", a.base + "/activate")
    first, second = await asyncio.gather(
        request(a.tc, "POST", a.base + "/call-next", headers={"Idempotency-Key": "first"}),
        request(a.tc2, "POST", a.base + "/call-next"),
    )
    assert first["entry_id"] != second["entry_id"]
    replay = await request(
        a.tc, "POST", a.base + "/call-next", headers={"Idempotency-Key": "first"}
    )
    assert replay["id"] == first["id"]
    await request(a.tc, "POST", a.base + "/call-next", 409)
    students = (await request(a.tc, "GET", a.base))["students"]
    first_user = next(p["student_id"] for p in students if p["id"] == first["entry_id"])
    second_user = next(p["student_id"] for p in students if p["id"] == second["entry_id"])
    p1 = a.base + "/students/" + first_user
    p2 = a.base + "/students/" + second_user
    await request(a.tc2, "PATCH", p1, 403, json={"status": "in_defense", "attempt_id": first["id"]})
    await request(a.tc, "PATCH", p1, json={"status": "in_defense", "attempt_id": first["id"]})
    await request(
        a.tc, "POST", p1 + "/evaluate", 409, json={"attempt_id": first["id"], "status": "absent"}
    )
    await request(a.tc, "POST", a.base + "/finish", 409, json={"reason": "Done"})
    grade = {
        "attempt_id": first["id"],
        "status": "accepted",
        "results": [{"work_id": a.works[0]["id"], "kind": "score", "score": "7.5"}],
    }
    result = await request(a.tc, "POST", p1 + "/evaluate", json=grade)
    assert result["status"] == "finished"
    assert (await request(a.tc, "POST", p1 + "/evaluate", json=grade))["id"] == first["id"]
    grade["results"][0]["score"] = 9
    await request(a.tc, "POST", p1 + "/evaluate", 409, json=grade)
    absent = {"attempt_id": second["id"], "status": "absent"}
    await request(a.tc2, "POST", p2 + "/evaluate", json=absent)
    state = await request(a.tc2, "GET", a.base)
    waiting = state["students"][0]
    assert waiting["status"] == "waiting" and waiting["self_moves_count"] == 0
    await request(a.tc2, "POST", p2 + "/evaluate", json=absent)
    history = await request(a.root, "GET", "/students/" + first_user + "/history")
    assert len(history) == 1 and len(history[0]["results"]) == 1
    assert history[0]["results"][0]["score"] == "7.5"
    assert len(history[0]["works"]) == 2  # Unreviewed second work gets no grade.
    filtered = await request(
        a.root, "GET", "/students/" + first_user + "/history", params={"date": "2000-01-01"}
    )
    assert filtered == []
    await request(a.tc, "POST", a.base + "/finish", 422)
    finished = await request(a.tc, "POST", a.base + "/finish", json={"reason": "Class ended"})
    assert finished["stage"] == "finished"
    assert not (await request(a.tc, "GET", a.base))["students"]
    await request(a.tc, "POST", a.base + "/activate", 409)
    await request(a.tc, "GET", a.base + "/history")
    async with a.app.state.session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(Attempt)
            .join(QueueEntry)
            .where(QueueEntry.queue_id == uuid.UUID(a.q["id"]))
        )
        assert count == 2


async def test_reserve_promotions_bans_and_move_limit(api):
    a = api
    for i in range(6):
        await a.enroll(i)
    sc = a.students[3][1]
    user_id = a.students[3][0]["id"]
    await request(
        a.tc, "DELETE", a.base + "/students/" + user_id, 204, json={"reason": "Removed by teacher"}
    )
    await request(sc, "POST", a.base + "/entries", 403, json=a.payload)
    promoted = await request(a.students[4][1], "GET", a.base)
    assert not promoted["my_entry"]["is_reserve"]
    notices = await request(a.students[4][1], "GET", "/me/notifications")
    assert "entry.promoted" in [n["type"] for n in notices]
    await request(a.tc2, "DELETE", a.base + "/students/" + user_id + "/ban", 204)
    await a.enroll(3)
    moved = await request(sc, "POST", a.base + "/entries/me/move-to-end")
    assert moved["self_moves_count"] == 1 and len(moved["works"]) == 2
    await request(sc, "POST", a.base + "/entries/me/move-to-end", 400)
    await request(sc, "DELETE", a.base + "/entries/me", 204)
    await request(a.students[0][1], "GET", a.base + "/bans", 403)


async def test_swaps_revalidate_expire_and_retries_are_safe(api):
    a = api
    for i in range(6):
        await a.enroll(i)
    s4, s5 = a.students[3][1], a.students[4][1]
    offer = await request(s4, "POST", a.base + "/entries/me/switch/5", 201)
    accepted = await request(s5, "POST", a.base + f"/swaps/{offer['swap_id']}/accept")
    assert accepted["status"] == "accepted"
    again = await request(s5, "POST", a.base + f"/swaps/{offer['swap_id']}/accept")
    assert again["from_position"] == accepted["from_position"]
    await request(s4, "POST", a.base + "/entries/me/switch/5", 400)  # self-swap
    pending = await request(s4, "POST", a.base + "/entries/me/switch/6", 201)
    async with a.app.state.session_factory() as db:
        row = await db.get(SwapOffer, uuid.UUID(pending["swap_id"]))
        row.created_at -= timedelta(seconds=46)
        await db.commit()
    expired = await request(
        a.students[5][1], "POST", a.base + f"/swaps/{pending['swap_id']}/accept"
    )
    assert expired["status"] == "expired"
    offer = await request(s4, "POST", a.base + "/entries/me/switch/6", 201)
    # Remove front student: target/initiator can enter top3 before confirmation.
    for i in range(2):
        await request(
            a.tc,
            "DELETE",
            a.base + "/students/" + a.students[i][0]["id"],
            204,
            json={"reason": "Left"},
        )
    invalid = await request(a.students[5][1], "POST", a.base + f"/swaps/{offer['swap_id']}/accept")
    assert invalid["status"] == "cancelled"
    async with a.app.state.session_factory() as db:
        stored = await db.get(SwapOffer, uuid.UUID(offer["swap_id"]))
        assert stored.status == "cancelled"  # Persisted, not rolled back on an HTTP error.


async def test_stream_access_auth_expiry_and_duplicate_enrollment(api):
    a = api
    stream = await request(a.root, "POST", "/streams", 201, json={"name": "Restricted"})
    q = await a.queue(stream_ids=[stream["id"]], quota=None)
    base = "/queues/" + q["id"]
    sc = a.students[0][1]
    await request(sc, "GET", base, 403)
    await request(sc, "POST", base + "/entries", 403, json=a.payload)
    await request(
        a.root, "PATCH", "/users/" + a.students[0][0]["id"], json={"stream_id": stream["id"]}
    )
    results = await asyncio.gather(
        *[
            sc.post(
                "/api/v1" + base + "/entries",
                json={
                    "works": [
                        {"work_id": w["work_id"], "artifact_url": "https://example.org/lab"}
                        for w in q["works"]
                    ]
                },
            )
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert not (await request(sc, "GET", base))["my_entry"]["is_reserve"]
    # Saving unchanged restrictions remains possible after the first enrollment.
    await request(a.tc, "PATCH", base, json={"stream_ids": [stream["id"]], "title": "Updated"})
    token = sc.cookies[SESSION_COOKIE]
    await a.app.state.redis.delete(session_key(token))
    await request(sc, "GET", "/me", 401)
    await request(sc, "POST", a.base + "/entries", 401, json=a.payload)


async def test_no_secret_fields_in_openapi_responses_and_event_payloads(api):
    a = api
    await a.enroll(0)
    await request(a.tc, "POST", a.base + "/activate")
    await request(a.tc, "POST", a.base + "/call-next")
    async with a.app.state.session_factory() as db:
        events = list(
            await db.scalars(select(QueueEvent).where(QueueEvent.queue_id == uuid.UUID(a.q["id"])))
        )
        for item in events:
            assert "password" not in str(item.payload)
            assert "https://example.org" not in str(item.payload)
    schema = a.app.openapi()
    for path, methods in schema["paths"].items():
        if path in ("/health", "/api/v1/auth/login"):
            continue
        for op in methods.values():
            assert op.get("security"), path
            assert {"401", "403", "422"} <= set(op["responses"]), path
            for code in ("200", "201"):
                if code in op["responses"] and not path.endswith("/stream"):
                    contract = op["responses"][code]["content"]["application/json"]["schema"]
                    assert contract and contract != {}, path


async def test_cross_session_student_and_teacher_are_not_called_twice(api):
    a = api
    await a.enroll(0)
    another = await a.queue()
    base = "/queues/" + another["id"]
    await a.enroll(0, base)
    await a.enroll(1, base)
    await request(a.tc, "POST", a.base + "/activate")
    await request(a.tc, "PATCH", base, json={"stage": "active"})
    await request(a.tc, "POST", a.base + "/call-next")
    await request(a.tc, "POST", base + "/call-next", 409)
    # The second teacher skips a student already being served in another session.
    second = await request(a.tc2, "POST", base + "/call-next")
    state = await request(a.tc2, "GET", base)
    called = next(p for p in state["students"] if p["id"] == second["entry_id"])
    assert called["student_id"] == a.students[1][0]["id"]


async def test_wait_estimate_uses_defense_start_and_does_not_promise_when_overdue(api):
    a = api
    q = await a.queue(teacher_ids=[a.teacher["id"]], avg_duration_min=10)
    base = "/queues/" + q["id"]
    await a.enroll(0, base)
    await a.enroll(1, base)
    viewer = a.students[1][1]
    assert (await request(viewer, "GET", base))["wait_status"] == "preliminary"
    await request(a.tc, "PATCH", base, json={"stage": "active"})
    attempt = await request(a.tc, "POST", base + "/call-next")
    assert (await request(viewer, "GET", base))["wait_status"] == "waiting_for_start"
    student = a.students[0][0]["id"]
    await request(
        a.tc,
        "PATCH",
        base + "/students/" + student,
        json={"status": "in_defense", "attempt_id": attempt["id"]},
    )
    state = await request(viewer, "GET", base)
    assert state["wait_status"] == "estimate" and state["estimated_wait_minutes"] == 10
    async with a.app.state.session_factory() as db:
        row = await db.get(Attempt, uuid.UUID(attempt["id"]))
        row.started_at -= timedelta(minutes=11)
        await db.commit()
    state = await request(viewer, "GET", base)
    assert state["wait_status"] == "overdue" and state["estimated_wait_minutes"] is None
