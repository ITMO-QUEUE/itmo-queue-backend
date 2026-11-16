import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import EntryStatus, Queue, QueueEntry, SwapOffer, SwapStatus

router = APIRouter(prefix="/queues/{queue_id}/swaps", tags=["swaps"])

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


# Проверка, что запись не в Топ-3 и в статусе waiting
async def is_top3(session: AsyncSession, queue_id: uuid.UUID, entry_id: uuid.UUID) -> bool:
    top_3 = await session.scalars(
        select(QueueEntry.id)
        .where(QueueEntry.queue_id == queue_id, QueueEntry.status == EntryStatus.waiting)
        .order_by(QueueEntry.sort_key)
        .limit(3)
    )
    return entry_id in top_3.all()


@router.post(
    "",
    operation_id="createSwapOffer",
    status_code=201,
    responses={
        400: {"description": "Invalid entries or they are in Top-3"},
        403: {"description": "Cannot swap someone else's entry"},
    },
)
async def create_swap_offer(
    queue_id: uuid.UUID,
    from_entry_id: uuid.UUID,
    to_entry_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    async with session.begin():
        # Блокируем очередь
        await session.get(Queue, queue_id, with_for_update=True)

        from_entry = await session.scalar(
            select(QueueEntry).where(
                QueueEntry.id == from_entry_id, QueueEntry.queue_id == queue_id
            )
        )
        to_entry = await session.scalar(
            select(QueueEntry).where(QueueEntry.id == to_entry_id, QueueEntry.queue_id == queue_id)
        )

        if not from_entry or not to_entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        if from_entry.student_id != current_user.id:
            raise HTTPException(
                status_code=403, detail="Can only initiate swap from your own entry"
            )
        if from_entry.status != EntryStatus.waiting or to_entry.status != EntryStatus.waiting:
            raise HTTPException(status_code=400, detail="Both entries must be in waiting status")

        # Проверка Топ-3
        if await is_top3(session, queue_id, from_entry.id) or await is_top3(
            session, queue_id, to_entry.id
        ):
            raise HTTPException(status_code=400, detail="Cannot swap Top-3 entries")

        # Проверка, что нет активных обменов
        existing_swap = await session.scalar(
            select(SwapOffer).where(
                SwapOffer.queue_id == queue_id,
                SwapOffer.status == SwapStatus.pending,
                (SwapOffer.from_entry_id.in_([from_entry_id, to_entry_id]))
                | (SwapOffer.to_entry_id.in_([from_entry_id, to_entry_id])),
            )
        )
        if existing_swap:
            raise HTTPException(
                status_code=400, detail="One of the entries already has a pending swap"
            )

        new_swap = SwapOffer(
            queue_id=queue_id,
            from_entry_id=from_entry_id,
            to_entry_id=to_entry_id,
            status=SwapStatus.pending,
            created_at=datetime.now(UTC),
        )
        session.add(new_swap)
        await session.flush()
        return {"swap_id": new_swap.id, "status": new_swap.status}


@router.post(
    "/{swap_id}/accept",
    operation_id="acceptSwapOffer",
    responses={
        400: {"description": "Conditions changed (Top-3, status changed)"},
        403: {"description": "Cannot accept someone else's swap"},
    },
)
async def accept_swap(
    queue_id: uuid.UUID,
    swap_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    async with session.begin():
        # Блокируем всё, чтобы избежать одновременного принятия
        await session.get(Queue, queue_id, with_for_update=True)
        swap = await session.scalar(
            select(SwapOffer)
            .where(SwapOffer.id == swap_id, SwapOffer.queue_id == queue_id)
            .with_for_update()
        )

        if not swap or swap.status != SwapStatus.pending:
            raise HTTPException(status_code=404, detail="Pending swap not found")

        from_entry = await session.scalar(
            select(QueueEntry).where(QueueEntry.id == swap.from_entry_id).with_for_update()
        )
        to_entry = await session.scalar(
            select(QueueEntry).where(QueueEntry.id == swap.to_entry_id).with_for_update()
        )

        if to_entry.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only accept swaps offered to you")

        # ПОВТОРНАЯ ПРОВЕРКА В ТРАНЗАКЦИИ (ТЗ)
        if from_entry.status != EntryStatus.waiting or to_entry.status != EntryStatus.waiting:
            swap.status = SwapStatus.cancelled
            raise HTTPException(status_code=400, detail="One of the entries is no longer waiting")

        if await is_top3(session, queue_id, from_entry.id) or await is_top3(
            session, queue_id, to_entry.id
        ):
            swap.status = SwapStatus.cancelled
            raise HTTPException(status_code=400, detail="One of the entries is now in Top-3")

        # Меняем местами sort_key
        from_entry.sort_key, to_entry.sort_key = to_entry.sort_key, from_entry.sort_key
        from_entry.updated_at = datetime.now(UTC)
        to_entry.updated_at = datetime.now(UTC)

        swap.status = SwapStatus.accepted
        swap.resolved_at = datetime.now(UTC)

        return {"status": "accepted"}


@router.post(
    "/{swap_id}/decline",
    operation_id="declineSwapOffer",
    responses={403: {"description": "Only the recipient can decline"}},
)
async def decline_swap(
    queue_id: uuid.UUID,
    swap_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    async with session.begin():
        # Блокируем обмен, чтобы его нельзя было одновременно принять и отклонить
        swap = await session.scalar(
            select(SwapOffer)
            .where(SwapOffer.id == swap_id, SwapOffer.queue_id == queue_id)
            .with_for_update()
        )
        if not swap or swap.status != SwapStatus.pending:
            raise HTTPException(status_code=404, detail="Pending swap not found")

        # Проверяем, что отклоняет именно тот, кому предложили (to_entry)
        to_entry = await session.scalar(select(QueueEntry).where(QueueEntry.id == swap.to_entry_id))
        if to_entry.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Only the recipient can decline this swap")

        swap.status = SwapStatus.declined
        swap.resolved_at = datetime.now(UTC)

        return {"status": "declined"}


@router.post(
    "/{swap_id}/cancel",
    operation_id="cancelSwapOffer",
    responses={403: {"description": "Only the initiator can cancel"}},
)
async def cancel_swap(
    queue_id: uuid.UUID,
    swap_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    async with session.begin():
        swap = await session.scalar(
            select(SwapOffer)
            .where(SwapOffer.id == swap_id, SwapOffer.queue_id == queue_id)
            .with_for_update()
        )
        if not swap or swap.status != SwapStatus.pending:
            raise HTTPException(status_code=404, detail="Pending swap not found")

        # Проверяем, что отменяет именно тот, кто создал предложение (from_entry)
        from_entry = await session.scalar(
            select(QueueEntry).where(QueueEntry.id == swap.from_entry_id)
        )
        if from_entry.student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Only the initiator can cancel this swap")

        swap.status = SwapStatus.cancelled
        swap.resolved_at = datetime.now(UTC)

        return {"status": "cancelled"}
