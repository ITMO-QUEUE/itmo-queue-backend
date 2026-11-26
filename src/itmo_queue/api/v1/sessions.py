import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.api.schemas.queue import Reason
from itmo_queue.api.schemas.session import (
    ErrorResponse,
    RegistrationUpdate,
    SessionCreate,
    SessionCreated,
    SessionOptions,
    SessionRead,
    SessionUpdate,
)
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import (
    Attempt,
    AttemptStatus,
    Discipline,
    EntryStatus,
    Queue,
    QueueEntry,
    QueueStage,
    QueueStream,
    QueueTeacher,
    QueueWork,
    Stream,
    TeacherDiscipline,
    Work,
)
from itmo_queue.services import queue as svc

router = APIRouter(
    tags=["sessions"],
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Teacher access required"},
        404: {"model": ErrorResponse, "description": "Session or referenced entity not found"},
        409: {"model": ErrorResponse, "description": "Operation conflicts with session state"},
        422: {"model": ErrorResponse, "description": "Invalid fields or referenced entities"},
    },
)
DB = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[User, Depends(get_current_user)]
QueueID = Annotated[uuid.UUID, Path(description="Defense session UUID")]


def require_teacher(user: User):
    if not user.is_active or user.role not in ("teacher", "admin"):
        raise HTTPException(403, "Teacher access required")


async def assigned_session(queue_id: uuid.UUID, db: AsyncSession, actor: User) -> Queue:
    return await svc.get_queue(db, queue_id, actor, lock=True, manage=True)


async def validate_references(db: AsyncSession, discipline_id, teacher_ids, works):
    if not await db.get(Discipline, discipline_id):
        raise HTTPException(404, "Discipline not found")
    teachers = set(
        await db.scalars(
            select(User.id).where(
                User.id.in_(teacher_ids), User.role == "teacher", User.is_active.is_(True)
            )
        )
    )
    if teachers != set(teacher_ids):
        raise HTTPException(422, "Teachers must exist and be active")
    allowed_teachers = set(
        await db.scalars(
            select(TeacherDiscipline.teacher_id).where(
                TeacherDiscipline.discipline_id == discipline_id,
                TeacherDiscipline.teacher_id.in_(teacher_ids),
            )
        )
    )
    if allowed_teachers != set(teacher_ids):
        raise HTTPException(422, "Teachers must be assigned to the discipline")
    valid_works = set(
        await db.scalars(
            select(Work.id).where(
                Work.id.in_([w.work_id for w in works]),
                Work.discipline_id == discipline_id,
                Work.is_active.is_(True),
            )
        )
    )
    if valid_works != {w.work_id for w in works}:
        raise HTTPException(422, "Works must be active and belong to the discipline")


async def response_for(queue: Queue, db: AsyncSession) -> SessionRead:
    teachers = list(
        await db.scalars(
            select(QueueTeacher.teacher_id)
            .where(QueueTeacher.queue_id == queue.id)
            .order_by(QueueTeacher.teacher_id)
        )
    )
    works = list(
        await db.scalars(
            select(QueueWork)
            .where(QueueWork.queue_id == queue.id)
            .order_by(QueueWork.priority, QueueWork.work_id)
        )
    )
    values = {
        field: getattr(queue, field)
        for field in SessionRead.model_fields
        if field not in {"teacher_ids", "works", "stream_ids"}
    }
    return SessionRead(
        **values,
        stream_ids=list(
            await db.scalars(
                select(QueueStream.stream_id)
                .where(QueueStream.queue_id == queue.id)
                .order_by(QueueStream.stream_id)
            )
        ),
        teacher_ids=teachers,
        works=[{"work_id": w.work_id, "priority": w.priority} for w in works],
    )


@router.get(
    "/session-options",
    response_model=SessionOptions,
    operation_id="getSessionOptions",
    summary="Get choices for the session creation form",
)
async def session_options(db: DB, actor: Actor):
    require_teacher(actor)
    choices = select(Discipline)
    if actor.role == "teacher":
        choices = choices.where(
            Discipline.id.in_(
                select(TeacherDiscipline.discipline_id).where(
                    TeacherDiscipline.teacher_id == actor.id
                )
            )
        )
    disciplines = await db.scalars(choices.order_by(Discipline.name))
    teachers = await db.scalars(
        select(User).where(User.role == "teacher", User.is_active.is_(True)).order_by(User.name)
    )
    return {
        "disciplines": [{"id": d.id, "name": d.name} for d in disciplines],
        "teachers": [
            {
                "id": t.id,
                "name": t.name,
                "discipline_ids": list(
                    await db.scalars(
                        select(TeacherDiscipline.discipline_id).where(
                            TeacherDiscipline.teacher_id == t.id
                        )
                    )
                ),
            }
            for t in teachers
        ],
    }


@router.post(
    "/queues",
    response_model=SessionCreated,
    status_code=201,
    operation_id="createSession",
    summary="Create a preliminary session",
    description=(
        "Teachers need creation permission for the discipline; admins may create any session."
    ),
)
async def create_session(payload: SessionCreate, db: DB, actor: Actor):
    require_teacher(actor)
    if actor.role != "admin" and not actor.can_create_queues:
        raise HTTPException(403, "Session creation is not allowed")
    teachers = list(set(payload.teacher_ids) | ({actor.id} if actor.role == "teacher" else set()))
    if actor.role != "admin" and not await db.get(
        TeacherDiscipline, (actor.id, payload.discipline_id)
    ):
        raise HTTPException(403, "No creation permission for this discipline")
    await validate_references(db, payload.discipline_id, teachers, [])
    now = datetime.now(UTC)
    stream = Stream(id=uuid.uuid4(), name=payload.title)
    works = [
        Work(
            id=uuid.uuid4(),
            discipline_id=payload.discipline_id,
            title=work.title,
            position_no=index,
            is_active=True,
        )
        for index, work in enumerate(payload.works, start=1)
    ]
    db.add(stream)
    db.add_all(works)
    queue = Queue(
        **payload.model_dump(
            exclude={
                "teacher_ids",
                "works",
                "auditorium",
                "onlineLink",
                "registration_open",
                "stage",
                "finish_reason",
            }
        ),
        auditorium=payload.auditorium,
        online_link=payload.onlineLink,
        location=payload.auditorium or payload.onlineLink,
        created_by=actor.id,
        stage=QueueStage.preliminary,
        registration_open=payload.registration_open,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(queue)
    await db.flush()
    db.add_all([QueueTeacher(queue_id=queue.id, teacher_id=t, assigned_at=now) for t in teachers])
    db.add_all(
        [
            QueueWork(queue_id=queue.id, work_id=w.id, priority=spec.priority)
            for w, spec in zip(works, payload.works, strict=True)
        ]
    )
    db.add(QueueStream(queue_id=queue.id, stream_id=stream.id))
    await svc.event(db, queue, actor, "queue.status_changed")
    await db.flush()
    existing = await response_for(queue, db)
    response = SessionCreated(
        **existing.model_dump(exclude={"location", "stream_ids", "works"}),
        works=[
            {"work_id": work.id, "title": work.title, "priority": spec.priority}
            for work, spec in sorted(
                zip(works, payload.works, strict=True), key=lambda pair: pair[1].priority
            )
        ],
        stream_id=stream.id,
        auditorium=queue.auditorium,
        onlineLink=queue.online_link,
    )
    await db.commit()
    return response


@router.patch(
    "/queues/{queue_id}",
    response_model=SessionRead,
    operation_id="updateSession",
    summary="Update a session as an assigned teacher",
    description=(
        "Finished sessions are immutable. Registered works cannot be removed. "
        "Discipline is immutable."
    ),
)
async def update_session(queue_id: QueueID, payload: SessionUpdate, db: DB, actor: Actor):
    queue = await assigned_session(queue_id, db, actor)
    if queue.stage == QueueStage.finished:
        raise HTTPException(409, "Session is finished")
    values = payload.model_dump(
        exclude_unset=True,
        exclude={
            "teacher_ids",
            "works",
            "stream_ids",
            "registration_open",
            "stage",
            "finish_reason",
        },
    )
    start = values.get("planned_start", queue.planned_start)
    end = values.get("planned_end", queue.planned_end)
    if end <= start:
        raise HTTPException(422, "planned_end must be after planned_start")
    teachers = payload.teacher_ids
    works = payload.works
    if teachers is not None or works is not None:
        existing = await response_for(queue, db)
        await validate_references(
            db,
            queue.discipline_id,
            teachers if teachers is not None else existing.teacher_ids,
            works if works is not None else existing.works,
        )
    if works is not None:
        existing_ids = set(
            await db.scalars(select(QueueWork.work_id).where(QueueWork.queue_id == queue.id))
        )
        if existing_ids != {w.work_id for w in works} and await db.scalar(
            select(QueueEntry.id).where(QueueEntry.queue_id == queue.id).limit(1)
        ):
            raise HTTPException(409, "Cannot change work set after registration")
        await db.execute(delete(QueueWork).where(QueueWork.queue_id == queue.id))
        db.add_all([QueueWork(queue_id=queue.id, **w.model_dump()) for w in works])
    if teachers is not None:
        busy = await db.scalar(
            select(Attempt.id)
            .join(QueueEntry, Attempt.entry_id == QueueEntry.id)
            .where(
                QueueEntry.queue_id == queue.id,
                Attempt.teacher_id.not_in(teachers),
                Attempt.status.in_([AttemptStatus.called, AttemptStatus.in_progress]),
            )
            .limit(1)
        )
        if busy:
            raise HTTPException(409, "Cannot unassign a teacher with an active attempt")
        await db.execute(delete(QueueTeacher).where(QueueTeacher.queue_id == queue.id))
        db.add_all(
            [
                QueueTeacher(queue_id=queue.id, teacher_id=t, assigned_at=datetime.now(UTC))
                for t in teachers
            ]
        )
    if payload.stream_ids is not None:
        if set(payload.stream_ids) != set(
            await db.scalars(select(Stream.id).where(Stream.id.in_(payload.stream_ids)))
        ):
            raise HTTPException(422, "Unknown stream")
        previous_streams = set(
            await db.scalars(select(QueueStream.stream_id).where(QueueStream.queue_id == queue.id))
        )
        restrictions_changed = (
            set(payload.stream_ids) != previous_streams
            and payload.stream_ids
            and (not previous_streams or not set(payload.stream_ids).issuperset(previous_streams))
        )
        if restrictions_changed and await db.scalar(
            select(QueueEntry.id).where(QueueEntry.queue_id == queue.id).limit(1)
        ):
            raise HTTPException(409, "Cannot restrict streams after registration")
        await db.execute(delete(QueueStream).where(QueueStream.queue_id == queue.id))
        db.add_all([QueueStream(queue_id=queue.id, stream_id=s) for s in payload.stream_ids])
    if payload.stage is not None and payload.stage != queue.stage:
        if payload.stage == QueueStage.active and queue.stage == QueueStage.preliminary:
            queue.stage, queue.activated_at = QueueStage.active, datetime.now(UTC)
        elif payload.stage != QueueStage.finished:
            raise HTTPException(409, "Session stages cannot go back")
    if payload.registration_open is not None:
        queue.registration_open = payload.registration_open
    for key, value in values.items():
        setattr(queue, key, value)
    queue.updated_at = datetime.now(UTC)
    if payload.stage == QueueStage.finished:
        await db.flush()
        return await finish(
            queue_id,
            db,
            actor,
            Reason(reason=payload.finish_reason) if payload.finish_reason else None,
        )
    await svc.event(db, queue, actor, "queue.status_changed")
    response = await response_for(queue, db)
    await db.commit()
    return response


@router.post(
    "/queues/{queue_id}/registration",
    response_model=SessionRead,
    operation_id="setSessionRegistration",
    summary="Open or close registration",
)
async def registration(queue_id: QueueID, payload: RegistrationUpdate, db: DB, actor: Actor):
    queue = await assigned_session(queue_id, db, actor)
    if queue.stage == QueueStage.finished:
        raise HTTPException(409, "Session is finished")
    queue.registration_open = payload.open
    await svc.event(db, queue, actor, "queue.status_changed")
    queue.updated_at = datetime.now(UTC)
    response = await response_for(queue, db)
    await db.commit()
    return response


@router.post(
    "/queues/{queue_id}/activate",
    response_model=SessionRead,
    operation_id="activateSession",
    summary="Manually start the defense session",
)
async def activate(queue_id: QueueID, db: DB, actor: Actor):
    queue = await assigned_session(queue_id, db, actor)
    if queue.stage == QueueStage.active:
        return await response_for(queue, db)
    if queue.stage != QueueStage.preliminary:
        raise HTTPException(409, "Only preliminary sessions can be activated")
    queue.stage = QueueStage.active
    queue.activated_at = queue.updated_at = datetime.now(UTC)
    await svc.event(db, queue, actor, "queue.status_changed")
    response = await response_for(queue, db)
    await db.commit()
    return response


@router.post(
    "/queues/{queue_id}/finish",
    response_model=SessionRead,
    operation_id="finishSession",
    summary="Manually finish the defense session",
)
async def finish(queue_id: QueueID, db: DB, actor: Actor, payload: Reason | None = None):
    queue = await assigned_session(queue_id, db, actor)
    if queue.stage == QueueStage.finished:
        return await response_for(queue, db)
    busy = await db.scalar(
        select(Attempt.id)
        .join(QueueEntry, Attempt.entry_id == QueueEntry.id)
        .where(
            QueueEntry.queue_id == queue.id,
            Attempt.status.in_([AttemptStatus.called, AttemptStatus.in_progress]),
        )
        .limit(1)
    )
    if busy:
        raise HTTPException(409, "Complete the current attempts first")
    if queue.stage != QueueStage.active:
        raise HTTPException(409, "Only active sessions can be finished")
    remaining = await svc.entries(db, queue_id)
    if remaining and payload is None:
        raise HTTPException(422, "A cancellation reason is required for remaining entries")
    for entry in remaining:
        entry.status, entry.closed_at = EntryStatus.cancelled, datetime.now(UTC)
        entry.updated_at = datetime.now(UTC)
        entry.removed_reason = payload.reason
        entry.removed_by = actor.id
        await svc.notify(
            db, entry.student_id, queue_id, "session.finished", {"reason": payload.reason}
        )
    await svc.invalidate_swaps(db, queue_id)
    queue.stage = QueueStage.finished
    queue.registration_open = False
    queue.finished_at = queue.updated_at = datetime.now(UTC)
    await svc.event(db, queue, actor, "queue.status_changed")
    response = await response_for(queue, db)
    await db.commit()
    return response
