"""Durable realtime delivery: committed events survive reconnects and multiple workers."""

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket
from sqlalchemy import func, or_, select
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from itmo_queue.api.deps import SESSION_COOKIE, get_current_user
from itmo_queue.api.v1.swaps import switch_position
from itmo_queue.api.v1.views import detail
from itmo_queue.db.models.user import User
from itmo_queue.models import Notification, QueueEntry, QueueEvent, SwapOffer, SwapStatus
from itmo_queue.services import queue as svc

router = APIRouter(tags=["realtime"])


async def authenticated(db, redis, token):
    return await get_current_user(token, redis, db)


@router.get(
    "/queues/{queue_id}/stream",
    operation_id="streamQueue",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE: snapshot, queue.updated, participant.status_changed, "
            "queue.status_changed. Reconnect with Last-Event-ID.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream(
    queue_id: uuid.UUID,
    request: Request,
    actor: Annotated[User, Depends(get_current_user)],
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
):
    factory = request.app.state.session_factory
    token = request.cookies.get(SESSION_COOKIE)
    redis = request.app.state.redis
    async with factory() as db:
        queue = await svc.get_queue(db, queue_id, actor)
        cursor = last_event_id if last_event_id is not None else after
        if cursor is None:
            cursor = (
                await db.scalar(
                    select(func.max(QueueEvent.id)).where(QueueEvent.queue_id == queue_id)
                )
                or 0
            )
        snapshot = (await detail(db, queue, actor)).model_dump(mode="json")

    async def events():
        nonlocal cursor
        yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
        ticks = 0
        while not await request.is_disconnected():
            async with factory() as db:
                try:
                    user = await authenticated(db, redis, token)
                    await svc.get_queue(db, queue_id, user)
                except HTTPException:
                    yield "event: auth.required\ndata: {}\n\n"
                    return
                updates = list(
                    await db.scalars(
                        select(QueueEvent)
                        .where(QueueEvent.queue_id == queue_id, QueueEvent.id > cursor)
                        .order_by(QueueEvent.id)
                        .limit(100)
                    )
                )
                for update in updates:
                    cursor = update.id
                    if update.event_type != "action.receipt":
                        yield (
                            f"id: {update.id}\nevent: {update.event_type}\n"
                            f"data: {json.dumps(update.payload)}\n\n"
                        )
            ticks += 1
            if ticks % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.websocket("/ws/me")
async def personal_socket(socket: WebSocket):
    origin = socket.headers.get("origin")
    own_origin = (
        str(socket.base_url).rstrip("/").replace("ws://", "http://").replace("wss://", "https://")
    )
    if origin and origin not in [own_origin, *socket.app.state.settings.cors_origins]:
        await socket.close(code=1008)
        return
    factory, redis = socket.app.state.session_factory, socket.app.state.redis
    token = socket.cookies.get(SESSION_COOKIE)
    try:
        async with factory() as db:
            await authenticated(db, redis, token)
    except HTTPException:
        await socket.close(code=1008)
        return
    await socket.accept()
    seen = set()
    try:
        while True:
            async with factory() as db:
                try:
                    user = await authenticated(db, redis, token)
                except HTTPException:
                    await socket.close(code=1008)
                    return
                # Expiry is persisted and sent to both users even without an incoming message.
                mine = select(QueueEntry.id).where(QueueEntry.student_id == user.id)
                queue_ids = list(
                    await db.scalars(
                        select(SwapOffer.queue_id)
                        .where(
                            SwapOffer.status == SwapStatus.pending,
                            or_(SwapOffer.from_entry_id.in_(mine), SwapOffer.to_entry_id.in_(mine)),
                        )
                        .distinct()
                        .order_by(SwapOffer.queue_id)
                    )
                )
                for qid in queue_ids:
                    queue = await svc.get_queue(db, qid, user, lock=True)
                    await svc.expire_swaps(db, queue)
                await db.commit()
                notifications = list(
                    await db.scalars(
                        select(Notification)
                        .where(Notification.user_id == user.id)
                        .order_by(Notification.created_at.desc(), Notification.id)
                        .limit(100)
                    )
                )
                for notification in reversed(notifications):
                    if notification.id not in seen:
                        seen.add(notification.id)
                        await socket.send_json(
                            {
                                "type": notification.type,
                                "notificationId": str(notification.id),
                                **notification.payload,
                            }
                        )
            try:
                raw = await asyncio.wait_for(socket.receive_text(), timeout=1)
            except TimeoutError:
                continue
            except KeyError:
                await socket.close(code=1003)
                return
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Object expected")
                kind = message.get("type")
                async with factory() as db:
                    user = await authenticated(db, redis, token)
                    if kind == "switch.propose":
                        qid = uuid.UUID(str(message["queueId"]))
                        number = message["targetQueueNumber"]
                        if type(number) is not int or number < 4:
                            raise ValueError("Target position must be an integer greater than 3")
                        result = await switch_position(qid, number, db, user)
                    elif kind in ("switch.accept", "switch.reject", "switch.cancel"):
                        offer_id = uuid.UUID(str(message["requestId"]))
                        offer = await db.get(SwapOffer, offer_id)
                        if not offer:
                            raise HTTPException(404, "Swap not found")
                        result = await svc.respond_swap(
                            db,
                            offer.queue_id,
                            offer.id,
                            user,
                            {
                                "switch.accept": "accept",
                                "switch.reject": "decline",
                                "switch.cancel": "cancel",
                            }[kind],
                        )
                        await db.commit()
                    else:
                        raise ValueError("Unknown message type")
                    await socket.send_json(
                        {"type": "switch.ack", "request": result.model_dump(mode="json")}
                    )
            except (KeyError, TypeError, ValueError):
                await socket.send_json(
                    {"type": "error", "status": 422, "detail": "Invalid WebSocket message"}
                )
            except HTTPException as exc:
                await socket.send_json(
                    {"type": "error", "status": exc.status_code, "detail": exc.detail}
                )
    except WebSocketDisconnect:
        return
