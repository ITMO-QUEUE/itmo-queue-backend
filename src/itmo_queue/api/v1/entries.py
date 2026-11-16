import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.api.schemas.entry import EntryCreate, EntryRead
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import (
    EntryStatus,
    EntryWork,
    Queue,
    QueueEntry,
    QueueStage,
    QueueWork,
)

router = APIRouter(prefix="/queues/{queue_id}/entries", tags=["entries"])

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post(
    "",
    response_model=EntryRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createEntry",
    responses={
        400: {"description": "Registration is closed or work is not allowed"},
        403: {"description": "Only students can enroll"},
        404: {"description": "Queue not found"},
        409: {"description": "Student is already in this queue"},
    },
)
async def enroll_student(
    queue_id: uuid.UUID,
    payload: EntryCreate,
    session: SessionDep,
    current_user: CurrentUser,
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can enroll")

    async with session.begin():
        queue = await session.get(Queue, queue_id, with_for_update=True)

        if not queue:
            raise HTTPException(status_code=404, detail="Queue not found")
        if not queue.registration_open or queue.stage == QueueStage.finished:
            raise HTTPException(status_code=400, detail="Registration is closed")

        queue_work = await session.scalar(
            select(QueueWork).where(
                QueueWork.queue_id == queue_id, QueueWork.work_id == payload.work_id
            )
        )
        if not queue_work:
            raise HTTPException(status_code=400, detail="This work is not allowed for this queue")

        existing_entry = await session.scalar(
            select(QueueEntry).where(
                QueueEntry.queue_id == queue_id,
                QueueEntry.student_id == current_user.id,
                QueueEntry.status.in_(
                    [EntryStatus.waiting, EntryStatus.called, EntryStatus.in_defense]
                ),
            )
        )
        if existing_entry:
            raise HTTPException(status_code=409, detail="Student is already in this queue")

        max_sort = await session.scalar(
            select(func.max(QueueEntry.sort_key)).where(QueueEntry.queue_id == queue_id)
        )
        new_sort_key = (max_sort or Decimal(0)) + Decimal(1)

        entry = QueueEntry(
            queue_id=queue_id,
            student_id=current_user.id,
            status=EntryStatus.waiting,
            sort_key=new_sort_key,
            self_moves_count=0,
            joined_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(entry)
        await session.flush()

        entry_work = EntryWork(
            entry_id=entry.id,
            work_id=payload.work_id,
            queue_id=queue_id,
            artifact_url=payload.artifact_url,
        )
        session.add(entry_work)

        ahead_count = await session.scalar(
            select(func.count()).where(
                QueueEntry.queue_id == queue_id,
                QueueEntry.status.in_(
                    [EntryStatus.waiting, EntryStatus.called, EntryStatus.in_defense]
                ),
                QueueEntry.sort_key < new_sort_key,
            )
        )
        position = (ahead_count or 0) + 1
        quota = queue.quota or 0

        return EntryRead(
            id=entry.id,
            student_id=entry.student_id,
            queue_id=entry.queue_id,
            status=entry.status.value,
            position=position,
            is_reserve=position > quota,
            work_id=payload.work_id,
            artifact_url=payload.artifact_url,
        )


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="cancelEntry",
    responses={
        400: {"description": "Entry is already closed"},
        403: {"description": "Cannot cancel entry that is called, in defense, or in Top-3"},
        404: {"description": "Queue or Entry not found"},
    },
)
async def cancel_entry(
    queue_id: uuid.UUID,
    entry_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    async with session.begin():
        queue = await session.get(Queue, queue_id, with_for_update=True)
        if not queue:
            raise HTTPException(status_code=404, detail="Queue not found")

        entry = await session.get(QueueEntry, entry_id)
        if not entry or entry.queue_id != queue_id or entry.student_id != current_user.id:
            raise HTTPException(status_code=404, detail="Entry not found")

        if entry.status in [EntryStatus.called, EntryStatus.in_defense]:
            raise HTTPException(
                status_code=403, detail="Cannot cancel entry that is called or in defense"
            )
        if entry.status in [EntryStatus.cancelled, EntryStatus.completed, EntryStatus.removed]:
            raise HTTPException(status_code=400, detail="Entry is already closed")

        top_3 = await session.scalars(
            select(QueueEntry.id)
            .where(QueueEntry.queue_id == queue_id, QueueEntry.status == EntryStatus.waiting)
            .order_by(QueueEntry.sort_key)
            .limit(3)
        )
        if entry.id in top_3.all():
            raise HTTPException(status_code=403, detail="Cannot cancel from Top-3")

        entry.status = EntryStatus.cancelled
        entry.closed_at = datetime.now(UTC)
        entry.updated_at = datetime.now(UTC)


@router.post(
    "/{entry_id}/move-to-end",
    response_model=EntryRead,
    operation_id="moveEntryToEnd",
    responses={
        403: {"description": "Cannot move Top-3, called, or in-defense entries"},
        404: {"description": "Entry not found"},
        400: {"description": "You have already moved this entry"},
    },
)
async def move_entry_to_end(
    queue_id: uuid.UUID,
    entry_id: uuid.UUID,
    session: SessionDep,  # <-- ИЗМЕНЕНО
    current_user: CurrentUser,
):
    async with session.begin():
        queue = await session.get(Queue, queue_id, with_for_update=True)
        if not queue:
            raise HTTPException(status_code=404, detail="Queue not found")

        # Блокируем запись
        entry = await session.scalar(
            select(QueueEntry)
            .where(QueueEntry.id == entry_id, QueueEntry.queue_id == queue_id)
            .with_for_update()
        )

        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")

        if entry.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot move someone else's entry")

        if entry.status != EntryStatus.waiting:
            raise HTTPException(status_code=403, detail="Can only move waiting entries")

        if entry.self_moves_count >= 1:
            raise HTTPException(status_code=400, detail="You have already moved this entry once")

        # Проверяем Топ-3
        top_3 = await session.scalars(
            select(QueueEntry.id)
            .where(QueueEntry.queue_id == queue_id, QueueEntry.status == EntryStatus.waiting)
            .order_by(QueueEntry.sort_key)
            .limit(3)
        )
        if entry.id in top_3.all():
            raise HTTPException(status_code=403, detail="Cannot move Top-3 entries")

        # Переносим в конец
        max_sort = await session.scalar(
            select(func.max(QueueEntry.sort_key)).where(QueueEntry.queue_id == queue_id)
        )
        entry.sort_key = (max_sort or Decimal(0)) + Decimal(1)
        entry.self_moves_count += 1
        entry.updated_at = datetime.now(UTC)

        await session.flush()

        # (Возвращаем обновленную запись - логика подсчета позиции как в POST)
        ahead_count = await session.scalar(
            select(func.count()).where(
                QueueEntry.queue_id == queue_id,
                QueueEntry.status.in_(
                    [EntryStatus.waiting, EntryStatus.called, EntryStatus.in_defense]
                ),
                QueueEntry.sort_key < entry.sort_key,
            )
        )
        position = (ahead_count or 0) + 1
        quota = queue.quota or 0

        # Достаем work_id и artifact_url для ответа
        entry_work = await session.scalar(select(EntryWork).where(EntryWork.entry_id == entry.id))

        return EntryRead(
            id=entry.id,
            student_id=entry.student_id,
            queue_id=entry.queue_id,
            status=entry.status.value,
            position=position,
            is_reserve=position > quota,
            work_id=entry_work.work_id,
            artifact_url=entry_work.artifact_url,
        )
