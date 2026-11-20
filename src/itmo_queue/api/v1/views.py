import math
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_admin, get_current_user
from itmo_queue.api.schemas.queue import (
    HistoryItem,
    NotificationRead,
    QueueDetail,
    TeacherRead,
    UserProfile,
    WorkResult,
)
from itmo_queue.api.v1.sessions import response_for
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import (
    Attempt,
    AttemptResult,
    AttemptStatus,
    Discipline,
    EntryStatus,
    EntryWork,
    Notification,
    Queue,
    QueueEntry,
    QueueStage,
    QueueStream,
    QueueTeacher,
)
from itmo_queue.services import queue as svc

router = APIRouter(tags=["views"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(get_current_admin)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/me", response_model=UserProfile, operation_id="getMyProfile")
async def profile(actor: Actor):
    return actor


async def detail(db, queue, actor):
    session = await response_for(queue, db)
    discipline = await db.get(Discipline, queue.discipline_id)
    participants = await svc.participants(db, queue, actor)
    my_entry = next((p for p in participants if p.student_id == actor.id), None)
    teachers = []
    attempts = []
    external_busy = False
    for teacher_id in session.teacher_ids:
        teacher = await db.get(User, teacher_id)
        attempt = await db.scalar(
            select(Attempt).where(
                Attempt.teacher_id == teacher_id, Attempt.status.in_(svc.ACTIVE_ATTEMPTS)
            )
        )
        busy = attempt is not None
        if attempt:
            current_entry = await db.get(QueueEntry, attempt.entry_id)
            if current_entry.queue_id == queue.id:
                attempts.append(attempt)
            else:
                external_busy = True
                attempt = None
        teachers.append(
            TeacherRead(
                id=teacher_id,
                name=teacher.name,
                attempt_id=attempt.id if attempt else None,
                entry_id=attempt.entry_id if attempt else None,
                is_busy=busy,
                is_active=teacher.is_active,
            )
        )
    estimate, state = None, "unavailable"
    if queue.stage != QueueStage.active:
        state = "finished" if queue.stage == QueueStage.finished else "preliminary"
    elif my_entry and my_entry.status != EntryStatus.waiting:
        state = "called"
    elif my_entry and len(teachers) == 1 and teachers[0].is_active and not external_busy:
        duration = queue.avg_duration_min or 10
        ahead = sum(
            p.status == EntryStatus.waiting and p.position < my_entry.position for p in participants
        )
        current = attempts[0] if attempts else None
        if current and current.status == AttemptStatus.called:
            state = "waiting_for_start"
        elif current and (svc.now() - current.started_at).total_seconds() >= duration * 60:
            state = "overdue"
        elif not current and ahead == 0:
            state = "next"
        else:
            remaining = (
                max(0, duration - (svc.now() - current.started_at).total_seconds() / 60)
                if current
                else 0
            )
            estimate, state = math.ceil(ahead * duration + remaining), "estimate"
    return QueueDetail(
        **session.model_dump(),
        discipline_name=discipline.name,
        teachers=teachers,
        students=participants,
        waiting_count=sum(p.status == EntryStatus.waiting for p in participants),
        my_entry=my_entry,
        estimated_wait_minutes=estimate,
        wait_status=state,
    )


def visible(actor):
    if actor.role == "admin":
        return True
    if actor.role == "teacher":
        return exists(
            select(QueueTeacher.queue_id).where(
                QueueTeacher.queue_id == Queue.id, QueueTeacher.teacher_id == actor.id
            )
        )
    restricted = exists(select(QueueStream.queue_id).where(QueueStream.queue_id == Queue.id))
    allowed = exists(
        select(QueueStream.queue_id).where(
            QueueStream.queue_id == Queue.id, QueueStream.stream_id == actor.stream_id
        )
    )
    return or_(~restricted, allowed)


@router.get("/queues", response_model=list[QueueDetail], operation_id="listQueues")
async def list_queues(
    db: DB,
    actor: Actor,
    limit: Limit = 20,
    offset: Offset = 0,
    search: str | None = Query(None, max_length=255),
    discipline: uuid.UUID | None = None,
    stage: QueueStage | None = None,
    registration_open: bool | None = None,
    stream: uuid.UUID | None = None,
):
    query = select(Queue).join(Discipline).where(visible(actor))
    if search:
        matching_teacher = exists(
            select(QueueTeacher.queue_id)
            .join(User, User.id == QueueTeacher.teacher_id)
            .where(QueueTeacher.queue_id == Queue.id, User.name.ilike(f"%{search}%"))
        )
        query = query.where(
            or_(
                Queue.title.ilike(f"%{search}%"),
                Discipline.name.ilike(f"%{search}%"),
                matching_teacher,
            )
        )
    if discipline:
        query = query.where(Queue.discipline_id == discipline)
    if stage:
        query = query.where(Queue.stage == stage)
    if registration_open is not None:
        query = query.where(Queue.registration_open == registration_open)
    if stream:
        query = query.where(
            exists(
                select(QueueStream.queue_id).where(
                    QueueStream.queue_id == Queue.id, QueueStream.stream_id == stream
                )
            )
        )
    queues = list(
        await db.scalars(query.order_by(Queue.planned_start, Queue.id).limit(limit).offset(offset))
    )
    return [await detail(db, queue, actor) for queue in queues]


@router.get("/queues/{queue_id}", response_model=QueueDetail, operation_id="getQueue")
async def get_queue(queue_id: uuid.UUID, db: DB, actor: Actor):
    return await detail(db, await svc.get_queue(db, queue_id, actor), actor)


async def student_queues(db, actor, student_id, limit, offset):
    queues = list(
        await db.scalars(
            select(Queue)
            .join(QueueEntry)
            .where(
                QueueEntry.student_id == student_id,
                QueueEntry.status.in_(svc.ACTIVE_ENTRIES),
                visible(actor),
            )
            .distinct()
            .order_by(Queue.planned_start, Queue.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [await detail(db, q, actor) for q in queues]


@router.get("/me/queues", response_model=list[QueueDetail], operation_id="listMyQueues")
async def my_queues(db: DB, actor: Actor, limit: Limit = 20, offset: Offset = 0):
    if actor.role == "teacher":
        return await list_queues(db, actor, limit, offset, None, None, None, None, None)
    return await student_queues(db, actor, actor.id, limit, offset)


@router.get(
    "/students/{student_id}/queues",
    response_model=list[QueueDetail],
    operation_id="listStudentQueues",
)
async def another_queues(
    student_id: uuid.UUID, db: DB, actor: Admin, limit: Limit = 20, offset: Offset = 0
):
    if not await db.get(User, student_id):
        raise HTTPException(404, "Student not found")
    return await student_queues(db, actor, student_id, limit, offset)


async def history(db, actor, student_id, queue_id, day, discipline, work, limit, offset):
    query = (
        select(Attempt, QueueEntry, Queue, Discipline)
        .join(QueueEntry, QueueEntry.id == Attempt.entry_id)
        .join(Queue)
        .join(Discipline)
    )
    if student_id:
        query = query.where(QueueEntry.student_id == student_id)
    if queue_id:
        query = query.where(Queue.id == queue_id)
    if day:
        start = datetime.combine(day, time.min, UTC)
        query = query.where(
            Attempt.called_at >= start, Attempt.called_at < start + timedelta(days=1)
        )
    if discipline:
        query = query.where(Queue.discipline_id == discipline)
    if work:
        query = query.where(
            exists(
                select(EntryWork.entry_id).where(
                    EntryWork.entry_id == QueueEntry.id, EntryWork.work_id == work
                )
            )
        )
    rows = (
        await db.execute(
            query.order_by(Attempt.called_at.desc(), Attempt.id).limit(limit).offset(offset)
        )
    ).all()
    result = []
    for attempt, entry, queue, disc in rows:
        teacher = await db.get(User, attempt.teacher_id)
        student = await db.get(User, entry.student_id)
        grades = list(
            await db.scalars(select(AttemptResult).where(AttemptResult.attempt_id == attempt.id))
        )
        result.append(
            HistoryItem(
                **{
                    f: getattr(attempt, f)
                    for f in (
                        "id",
                        "entry_id",
                        "teacher_id",
                        "status",
                        "called_at",
                        "started_at",
                        "finished_at",
                        "called_out_of_order",
                    )
                },
                queue_id=queue.id,
                queue_title=queue.title,
                discipline_id=disc.id,
                discipline_name=disc.name,
                student_id=student.id,
                student_name=student.name,
                teacher_name=teacher.name,
                works=await svc.artifacts(db, entry.id),
                results=[WorkResult.model_validate(g) for g in grades],
            )
        )
    return result


@router.get("/me/history", response_model=list[HistoryItem], operation_id="getMyHistory")
async def my_history(
    db: DB,
    actor: Actor,
    date: date | None = None,
    discipline: uuid.UUID | None = None,
    work: uuid.UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    return await history(db, actor, actor.id, None, date, discipline, work, limit, offset)


@router.get(
    "/students/{student_id}/history",
    response_model=list[HistoryItem],
    operation_id="getStudentHistory",
)
async def student_history(
    student_id: uuid.UUID,
    db: DB,
    actor: Admin,
    date: date | None = None,
    discipline: uuid.UUID | None = None,
    work: uuid.UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    if not await db.get(User, student_id):
        raise HTTPException(404, "Student not found")
    return await history(db, actor, student_id, None, date, discipline, work, limit, offset)


@router.get(
    "/queues/{queue_id}/history", response_model=list[HistoryItem], operation_id="getQueueHistory"
)
async def queue_history(
    queue_id: uuid.UUID, db: DB, actor: Actor, limit: Limit = 20, offset: Offset = 0
):
    await svc.get_queue(db, queue_id, actor, manage=True)
    return await history(db, actor, None, queue_id, None, None, None, limit, offset)


@router.get(
    "/me/notifications", response_model=list[NotificationRead], operation_id="listMyNotifications"
)
async def notifications(db: DB, actor: Actor, limit: Limit = 20, offset: Offset = 0):
    return list(
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == actor.id)
            .order_by(Notification.created_at.desc(), Notification.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.post(
    "/me/notifications/{notification_id}/read",
    response_model=NotificationRead,
    operation_id="readMyNotification",
)
async def read_notification(notification_id: uuid.UUID, db: DB, actor: Actor):
    item = await db.get(Notification, notification_id)
    if not item or item.user_id != actor.id:
        raise HTTPException(404, "Notification not found")
    item.read_at = item.read_at or svc.now()
    await db.commit()
    return item
