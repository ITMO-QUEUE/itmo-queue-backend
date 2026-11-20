import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.api.schemas.queue import SwapRead
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import QueueEntry, SwapOffer
from itmo_queue.services import queue as svc

router = APIRouter(tags=["swaps"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[User, Depends(get_current_user)]


@router.post(
    "/queues/{queue_id}/swaps",
    response_model=SwapRead,
    operation_id="createSwapOffer",
    status_code=201,
)
async def create_swap_offer(
    queue_id: uuid.UUID,
    from_entry_id: uuid.UUID,
    to_entry_id: uuid.UUID,
    session: DB,
    current_user: Actor,
):
    result = await svc.propose_swap(session, queue_id, current_user, from_entry_id, to_entry_id)
    await session.commit()
    return result


@router.post(
    "/queues/{queue_id}/entries/me/switch/{queue_number}",
    response_model=SwapRead,
    operation_id="proposePositionSwitch",
    status_code=201,
)
async def switch_position(
    queue_id: uuid.UUID, queue_number: Annotated[int, Path(ge=4)], session: DB, current_user: Actor
):
    await svc.get_queue(session, queue_id, current_user, lock=True)
    ordered = await svc.entries(session, queue_id)
    if queue_number > len(ordered):
        raise HTTPException(404, "Target position not found")
    own = await svc.own_entry(session, queue_id, current_user)
    result = await svc.propose_swap(
        session, queue_id, current_user, own.id, ordered[queue_number - 1].id
    )
    await session.commit()
    return result


@router.get("/queues/{queue_id}/swaps", response_model=list[SwapRead], operation_id="listMySwaps")
async def list_swaps(queue_id: uuid.UUID, session: DB, current_user: Actor):
    queue = await svc.get_queue(session, queue_id, current_user, lock=True)
    await svc.expire_swaps(session, queue)
    mine = select(QueueEntry.id).where(QueueEntry.student_id == current_user.id)
    offers = list(
        await session.scalars(
            select(SwapOffer)
            .where(
                SwapOffer.queue_id == queue_id,
                or_(SwapOffer.from_entry_id.in_(mine), SwapOffer.to_entry_id.in_(mine)),
            )
            .order_by(SwapOffer.created_at.desc())
            .limit(100)
        )
    )
    result = [await svc.swap_read(session, s) for s in offers]
    await session.commit()
    return result


async def respond(queue_id, swap_id, session, user, action):
    result = await svc.respond_swap(session, queue_id, swap_id, user, action)
    await session.commit()
    return result


@router.post(
    "/queues/{queue_id}/swaps/{swap_id}/accept",
    response_model=SwapRead,
    operation_id="acceptSwapOffer",
)
async def accept_swap(queue_id: uuid.UUID, swap_id: uuid.UUID, session: DB, current_user: Actor):
    return await respond(queue_id, swap_id, session, current_user, "accept")


@router.post(
    "/queues/{queue_id}/swaps/{swap_id}/decline",
    response_model=SwapRead,
    operation_id="declineSwapOffer",
)
async def decline_swap(queue_id: uuid.UUID, swap_id: uuid.UUID, session: DB, current_user: Actor):
    return await respond(queue_id, swap_id, session, current_user, "decline")


@router.post(
    "/queues/{queue_id}/swaps/{swap_id}/cancel",
    response_model=SwapRead,
    operation_id="cancelSwapOffer",
)
async def cancel_swap(queue_id: uuid.UUID, swap_id: uuid.UUID, session: DB, current_user: Actor):
    return await respond(queue_id, swap_id, session, current_user, "cancel")
