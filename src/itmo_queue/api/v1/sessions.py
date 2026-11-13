import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.api.schemas.session import (
    ErrorResponse,
    RegistrationUpdate,
    SessionCreate,
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
    Queue,
    QueueEntry,
    QueueStage,
    QueueTeacher,
    QueueWork,
    Work,
)

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
    if not user.is_active or user.role != "teacher":
        raise HTTPException(403, "Teacher access required")


async def assigned_session(queue_id: uuid.UUID, db: AsyncSession, actor: User) -> Queue:
    require_teacher(actor)
    queue = await db.scalar(select(Queue).where(Queue.id == queue_id).with_for_update())
    if not queue:
        raise HTTPException(404, "Session not found")
    if not await db.get(QueueTeacher, (queue_id, actor.id)):
        raise HTTPException(403, "Not assigned to this session")
    return queue


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
        if field not in {"teacher_ids", "works"}
    }
    return SessionRead(
        **values,
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
    disciplines = await db.scalars(select(Discipline).order_by(Discipline.name))
    teachers = await db.scalars(
        select(User).where(User.role == "teacher", User.is_active.is_(True)).order_by(User.name)
    )
    works = await db.scalars(
        select(Work)
        .where(Work.is_active.is_(True))
        .order_by(Work.discipline_id, Work.position_no, Work.id)
    )
    return {
        "disciplines": [{"id": d.id, "name": d.name} for d in disciplines],
        "teachers": [{"id": t.id, "name": t.name} for t in teachers],
        "works": [
            {
                "id": w.id,
                "discipline_id": w.discipline_id,
                "title": w.title,
                "position_no": w.position_no,
            }
            for w in works
        ],
    }


@router.post(
    "/queues",
    response_model=SessionRead,
    status_code=201,
    operation_id="createSession",
    summary="Create a preliminary session",
    description=(
        "Creator needs can_create_queues and is always assigned. Registration opens immediately."
    ),
)
async def create_session(payload: SessionCreate, db: DB, actor: Actor):
    require_teacher(actor)
    if not actor.can_create_queues:
        raise HTTPException(403, "Session creation is not allowed")
    teachers = list(set(payload.teacher_ids) | {actor.id})
    await validate_references(db, payload.discipline_id, teachers, payload.works)
    now = datetime.now(UTC)
    queue = Queue(
        **payload.model_dump(exclude={"teacher_ids", "works"}),
        created_by=actor.id,
        stage=QueueStage.preliminary,
        registration_open=True,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(queue)
    await db.flush()
    db.add_all([QueueTeacher(queue_id=queue.id, teacher_id=t, assigned_at=now) for t in teachers])
    db.add_all([QueueWork(queue_id=queue.id, **w.model_dump()) for w in payload.works])
    await db.flush()
    response = await response_for(queue, db)
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
    values = payload.model_dump(exclude_unset=True, exclude={"teacher_ids", "works"})
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
    for key, value in values.items():
        setattr(queue, key, value)
    queue.updated_at = datetime.now(UTC)
    await db.flush()
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
    if queue.stage != QueueStage.preliminary:
        raise HTTPException(409, "Only preliminary sessions can be activated")
    queue.stage = QueueStage.active
    queue.activated_at = queue.updated_at = datetime.now(UTC)
    response = await response_for(queue, db)
    await db.commit()
    return response


@router.post(
    "/queues/{queue_id}/finish",
    response_model=SessionRead,
    operation_id="finishSession",
    summary="Manually finish the defense session",
)
async def finish(queue_id: QueueID, db: DB, actor: Actor):
    queue = await assigned_session(queue_id, db, actor)
    if queue.stage != QueueStage.active:
        raise HTTPException(409, "Only active sessions can be finished")
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
    queue.stage = QueueStage.finished
    queue.registration_open = False
    queue.finished_at = queue.updated_at = datetime.now(UTC)
    response = await response_for(queue, db)
    await db.commit()
    return response
