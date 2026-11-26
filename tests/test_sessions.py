import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from itmo_queue.api.deps import get_current_user
from itmo_queue.core.config import get_settings
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.main import create_app
from itmo_queue.models import (
    Attempt,
    AttemptStatus,
    Discipline,
    EntryStatus,
    Queue,
    QueueEntry,
    QueueStream,
    Stream,
    TeacherDiscipline,
    Work,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def context():
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()

        def session():
            return AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )

        async with session() as db:
            teachers = [
                User(
                    name=f"Teacher {i}",
                    login=uuid.uuid4().hex,
                    password_hash="not-used",
                    role="teacher",
                    can_create_queues=True,
                )
                for i in range(3)
            ]
            discipline = Discipline(name=uuid.uuid4().hex)
            db.add_all([*teachers, discipline])
            await db.flush()
            works = [
                Work(discipline_id=discipline.id, title=f"Work {i}", position_no=i, is_active=True)
                for i in range(2)
            ]
            db.add_all(works)
            db.add_all(
                [TeacherDiscipline(teacher_id=t.id, discipline_id=discipline.id) for t in teachers]
            )
            await db.commit()
        state = SimpleNamespace(
            actor=teachers[0],
            teachers=teachers,
            works=works,
            discipline=discipline,
            session=session,
        )
        app = create_app()

        async def database():
            async with session() as db:
                yield db

        async def actor():
            return state.actor

        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[get_current_user] = actor
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            state.client = client
            yield state
        await transaction.rollback()
    await engine.dispose()


def payload(ctx):
    now = datetime.now(UTC)
    return {
        "discipline_id": str(ctx.discipline.id),
        "title": "Defense",
        "planned_start": (now - timedelta(days=2)).isoformat(),
        "planned_end": (now - timedelta(days=1)).isoformat(),
        "teacher_ids": [str(t.id) for t in ctx.teachers[:2]],
        "auditorium": "Room 101",
        "works": [{"title": w.title, "priority": 2 - i} for i, w in enumerate(ctx.works)],
        "quota": 1,
    }


async def create(ctx):
    result = await ctx.client.post("/api/v1/queues", json=payload(ctx))
    assert result.status_code == 201, result.text
    async with ctx.session() as db:
        by_id = {
            w.id: w
            for w in await db.scalars(
                select(Work).where(
                    Work.id.in_([uuid.UUID(w["work_id"]) for w in result.json()["works"]])
                )
            )
        }
        ctx.works = sorted(by_id.values(), key=lambda w: w.position_no)
    return result.json()


@pytest.mark.parametrize("place", [{"auditorium": "3200"}, {"onlineLink": "https://ktalk.ru/a"}])
async def test_create_venue_stream_and_works(context, place):
    data = payload(context)
    data.pop("auditorium")
    result = await context.client.post("/api/v1/queues", json=data | place)
    assert result.status_code == 201, result.text
    body = result.json()
    assert body["auditorium"] == place.get("auditorium")
    assert body["onlineLink"] == place.get("onlineLink")
    async with context.session() as db:
        assert await db.get(Stream, uuid.UUID(body["stream_id"]))
        assert await db.get(QueueStream, (uuid.UUID(body["id"]), uuid.UUID(body["stream_id"])))
        for item in body["works"]:
            work = await db.get(Work, uuid.UUID(item["work_id"]))
            assert work.discipline_id == context.discipline.id and work.is_active


async def test_create_rolls_back_on_error(context, monkeypatch):
    async with context.session() as db:
        before = [
            await db.scalar(select(func.count()).select_from(m)) for m in (Queue, Stream, Work)
        ]

    async def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr("itmo_queue.api.v1.sessions.svc.event", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        await context.client.post("/api/v1/queues", json=payload(context))
    async with context.session() as db:
        after = [
            await db.scalar(select(func.count()).select_from(m)) for m in (Queue, Stream, Work)
        ]
    assert before == after


async def test_lifecycle_and_equal_teacher_rights(context):
    ctx = context
    queue = await create(ctx)
    assert queue["stage"] == "preliminary"  # Past planned dates do not activate it.
    assert queue["registration_open"] is True
    assert [w["priority"] for w in queue["works"]] == [1, 2]
    base = f"/api/v1/queues/{queue['id']}"
    ctx.actor = ctx.teachers[1]
    result = await ctx.client.patch(base, json={"title": "Changed", "quota": 2})
    assert result.status_code == 200
    assert result.json()["title"] == "Changed"
    result = await ctx.client.patch(
        base,
        json={
            "works": [{"work_id": str(w.id), "priority": i + 1} for i, w in enumerate(ctx.works)],
            "teacher_ids": [str(t.id) for t in ctx.teachers],
        },
    )
    assert result.status_code == 200, result.text
    assert len(result.json()["teacher_ids"]) == 3
    assert result.json()["works"][0]["work_id"] == str(ctx.works[0].id)
    assert (await ctx.client.post(base + "/finish")).status_code == 409
    assert (await ctx.client.post(base + "/registration", json={"open": False})).status_code == 200
    result = await ctx.client.post(base + "/activate")
    assert result.json()["stage"] == "active"
    assert result.json()["registration_open"] is False
    assert (await ctx.client.post(base + "/activate")).status_code == 200
    assert (await ctx.client.post(base + "/registration", json={"open": True})).status_code == 200
    result = await ctx.client.post(base + "/finish")
    assert result.json()["stage"] == "finished"
    assert result.json()["registration_open"] is False
    assert (await ctx.client.patch(base, json={"quota": 4})).status_code == 409
    assert (await ctx.client.post(base + "/registration", json={"open": True})).status_code == 409


async def test_access_and_validation(context):
    ctx = context
    queue = await create(ctx)
    base = f"/api/v1/queues/{queue['id']}"
    ctx.actor = ctx.teachers[2]
    assert (await ctx.client.patch(base, json={"title": "Forbidden"})).status_code == 403
    ctx.actor = ctx.teachers[0]
    ctx.actor.can_create_queues = False
    assert (await ctx.client.post("/api/v1/queues", json=payload(ctx))).status_code == 403
    # Existing assignments do not depend on permission to create new sessions.
    assert (await ctx.client.patch(base, json={"title": "Allowed"})).status_code == 200
    assert (await ctx.client.patch(base, json={"title": None})).status_code == 422
    assert (await ctx.client.patch(base, json={"teacher_ids": []})).status_code == 422
    assert (
        await ctx.client.patch(base, json={"planned_end": "2000-01-01T00:00:00Z"})
    ).status_code == 422
    ctx.actor.role = "student"
    assert (await ctx.client.patch(base, json={"quota": 3})).status_code == 403
    assert (await ctx.client.get("/api/v1/session-options")).status_code == 403


async def test_work_validation_and_options(context):
    ctx = context
    data = payload(ctx)
    data["works"][0]["title"] = ""
    assert (await ctx.client.post("/api/v1/queues", json=data)).status_code == 422
    data = payload(ctx)
    data["works"].append(data["works"][0])
    assert (await ctx.client.post("/api/v1/queues", json=data)).status_code == 422
    options = (await ctx.client.get("/api/v1/session-options")).json()
    assert str(ctx.discipline.id) in [d["id"] for d in options["disciplines"]]
    assert str(ctx.works[0].id) in [w["id"] for w in options["works"]]


async def test_registered_works_and_busy_teacher_are_preserved(context):
    ctx = context
    queue = await create(ctx)
    queue_id = uuid.UUID(queue["id"])
    base = f"/api/v1/queues/{queue_id}"
    async with ctx.session() as db:
        student = User(name="Student", login=uuid.uuid4().hex, password_hash="unused")
        db.add(student)
        await db.flush()
        now = datetime.now(UTC)
        entry = QueueEntry(
            queue_id=queue_id,
            student_id=student.id,
            status=EntryStatus.called,
            sort_key=1,
            self_moves_count=0,
            joined_at=now,
            updated_at=now,
        )
        db.add(entry)
        await db.flush()
        db.add(
            Attempt(
                entry_id=entry.id,
                teacher_id=ctx.teachers[1].id,
                status=AttemptStatus.called,
                called_at=now,
                called_out_of_order=False,
            )
        )
        await db.commit()
    assert (
        await ctx.client.patch(base, json={"works": [payload(ctx)["works"][0]]})
    ).status_code == 409
    assert (
        await ctx.client.patch(base, json={"teacher_ids": [str(ctx.teachers[0].id)]})
    ).status_code == 409
    assert (await ctx.client.post(base + "/activate")).status_code == 200
    assert (await ctx.client.post(base + "/finish")).status_code == 409
    async with ctx.session() as db:
        assert (await db.scalar(select(Queue).where(Queue.id == queue_id))).stage.value == "active"


async def test_session_authentication_required():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/session-options")).status_code == 401


async def test_openapi_session_contracts():
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v1/queues"]["post"]
    assert operation["operationId"] == "createSession"
    assert operation["security"]
    assert "201" in operation["responses"]
    assert {"401", "403", "404", "409", "422"} <= operation["responses"].keys()
