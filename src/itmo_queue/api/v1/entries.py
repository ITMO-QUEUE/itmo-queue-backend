import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.api.schemas.queue import Enrollment, Participant
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import EntryStatus, EntryWork, QueueEntry, QueueWork, Work
from itmo_queue.services import queue as svc

router = APIRouter(prefix="/queues/{queue_id}/entries", tags=["entries"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=Participant, status_code=201, operation_id="createEntry")
async def enroll_student(
    queue_id: uuid.UUID, payload: Enrollment, session: DB, current_user: Actor
):
    if current_user.role not in ("student", "admin"):
        raise HTTPException(403, "Student access required")
    queue = await svc.get_queue(session, queue_id, current_user, lock=True)
    svc.mutable(queue)
    if not queue.registration_open:
        raise HTTPException(400, "Registration is closed")
    if await svc.active_ban(session, queue_id, current_user.id):
        raise HTTPException(403, "Repeat registration is banned")
    existing = await session.scalar(
        select(QueueEntry.id).where(
            QueueEntry.queue_id == queue_id,
            QueueEntry.student_id == current_user.id,
            QueueEntry.status.in_(svc.ACTIVE_ENTRIES),
        )
    )
    if existing:
        raise HTTPException(409, "Student is already in this queue")
    allowed = set(
        await session.scalars(
            select(QueueWork.work_id)
            .join(Work, Work.id == QueueWork.work_id)
            .where(QueueWork.queue_id == queue_id, Work.is_active.is_(True))
        )
    )
    if not {w.work_id for w in payload.works} <= allowed:
        raise HTTPException(400, "This work is not allowed for this queue")
    entry = QueueEntry(
        queue_id=queue_id,
        student_id=current_user.id,
        status=EntryStatus.waiting,
        sort_key=await svc.tail(session, queue_id),
        self_moves_count=0,
        joined_at=svc.now(),
        updated_at=svc.now(),
    )
    session.add(entry)
    await session.flush()
    session.add_all(
        [
            EntryWork(
                entry_id=entry.id,
                queue_id=queue_id,
                work_id=w.work_id,
                artifact_url=str(w.artifact_url),
            )
            for w in payload.works
        ]
    )
    await svc.event(session, queue, current_user)
    result = await svc.participant(session, queue, entry, current_user)
    await session.commit()
    return result


async def cancel(queue_id, session, current_user, entry_id=None):
    queue = await svc.get_queue(session, queue_id, current_user, lock=True)
    entry = await svc.own_entry(session, queue_id, current_user, entry_id)
    if entry.status == EntryStatus.cancelled:
        return
    await svc.self_action(session, queue, entry)
    before = await svc.entries(session, queue_id)
    entry.status, entry.closed_at, entry.updated_at = EntryStatus.cancelled, svc.now(), svc.now()
    await svc.invalidate_swaps(session, queue_id, entry.id)
    await session.flush()
    await svc.promoted(session, queue, before)
    await svc.event(session, queue, current_user)
    await session.commit()


# Static /me routes precede UUID routes.
@router.delete("/me", status_code=204, operation_id="cancelMyEntry")
async def cancel_me(queue_id: uuid.UUID, session: DB, current_user: Actor):
    await cancel(queue_id, session, current_user)


@router.delete("/{entry_id}", status_code=204, operation_id="cancelEntry")
async def cancel_entry(queue_id: uuid.UUID, entry_id: uuid.UUID, session: DB, current_user: Actor):
    await cancel(queue_id, session, current_user, entry_id)


async def move(queue_id, session, actor, entry_id=None):
    queue = await svc.get_queue(session, queue_id, actor, lock=True)
    entry = await svc.own_entry(session, queue_id, actor, entry_id)
    await svc.self_action(session, queue, entry)
    if entry.self_moves_count:
        raise HTTPException(400, "You have already moved this entry once")
    before = await svc.entries(session, queue_id)
    entry.sort_key = await svc.tail(session, queue_id)
    entry.self_moves_count += 1
    entry.updated_at = svc.now()
    await svc.invalidate_swaps(session, queue_id, entry.id)
    await session.flush()
    await svc.promoted(session, queue, before)
    await svc.event(session, queue, actor)
    result = await svc.participant(session, queue, entry, actor)
    await session.commit()
    return result


@router.post("/me/move-to-end", response_model=Participant, operation_id="moveMyEntryToEnd")
async def move_me(queue_id: uuid.UUID, session: DB, current_user: Actor):
    return await move(queue_id, session, current_user)


@router.post("/{entry_id}/move-to-end", response_model=Participant, operation_id="moveEntryToEnd")
async def move_entry_to_end(
    queue_id: uuid.UUID, entry_id: uuid.UUID, session: DB, current_user: Actor
):
    return await move(queue_id, session, current_user, entry_id)
