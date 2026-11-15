"""Queue operations. Lock queue first, then users; commit in the HTTP/WS boundary.

Authentication may already have started the session transaction. Never nest begin().
Queue locks serialize membership/ordering changes across all application workers.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.schemas.queue import Participant, ParticipantWork, SwapRead
from itmo_queue.db.models.user import User
from itmo_queue.models import (
    Attempt,
    AttemptStatus,
    EntryStatus,
    EntryWork,
    Notification,
    Queue,
    QueueEntry,
    QueueEvent,
    QueueStage,
    QueueStream,
    QueueTeacher,
    QueueWork,
    RegistrationBan,
    SwapOffer,
    SwapStatus,
    Work,
)

ACTIVE_ENTRIES = (EntryStatus.waiting, EntryStatus.called, EntryStatus.in_defense)
ACTIVE_ATTEMPTS = (AttemptStatus.called, AttemptStatus.in_progress)
SWAP_TTL = timedelta(seconds=45)


def now():
    return datetime.now(UTC)


async def accessible(db: AsyncSession, queue: Queue, actor: User):
    if actor.role == "admin":
        return
    if actor.role == "teacher":
        if not await db.get(QueueTeacher, (queue.id, actor.id)):
            raise HTTPException(403, "Not assigned to this session")
        return
    streams = list(
        await db.scalars(select(QueueStream.stream_id).where(QueueStream.queue_id == queue.id))
    )
    if streams and actor.stream_id not in streams:
        raise HTTPException(403, "Session is not available to your stream")


async def get_queue(db, queue_id, actor, *, lock=False, manage=False):
    query = select(Queue).where(Queue.id == queue_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    queue = await db.scalar(query)
    if not queue:
        raise HTTPException(404, "Session not found")
    if manage and actor.role not in ("teacher", "admin"):
        raise HTTPException(403, "Teacher access required")
    await accessible(db, queue, actor)
    return queue


def mutable(queue):
    if queue.stage == QueueStage.finished:
        raise HTTPException(409, "Session is finished")


async def entries(db, queue_id, *, waiting=False):
    return list(
        await db.scalars(
            select(QueueEntry)
            .where(
                QueueEntry.queue_id == queue_id,
                QueueEntry.status.in_((EntryStatus.waiting,) if waiting else ACTIVE_ENTRIES),
            )
            .order_by(QueueEntry.sort_key, QueueEntry.joined_at, QueueEntry.id)
        )
    )


async def own_entry(db, queue_id, actor, entry_id=None):
    if entry_id:
        entry = await db.get(QueueEntry, entry_id)
        if not entry or entry.queue_id != queue_id:
            raise HTTPException(404, "Entry not found")
        if entry.student_id != actor.id:
            raise HTTPException(403, "Cannot change another student's entry")
        return entry
    entry = await db.scalar(
        select(QueueEntry).where(
            QueueEntry.queue_id == queue_id,
            QueueEntry.student_id == actor.id,
            QueueEntry.status.in_(ACTIVE_ENTRIES),
        )
    )
    if not entry:
        raise HTTPException(404, "Active entry not found")
    return entry


async def self_action(db, queue, entry):
    mutable(queue)
    if entry.status != EntryStatus.waiting:
        raise HTTPException(403, "Only waiting entries can be changed")
    if entry.id in [e.id for e in (await entries(db, queue.id, waiting=True))[:3]]:
        raise HTTPException(403, "Cannot change Top-3 entries")


async def tail(db, queue_id):
    return (
        await db.scalar(
            select(func.max(QueueEntry.sort_key)).where(QueueEntry.queue_id == queue_id)
        )
        or 0
    ) + 1


async def artifacts(db, entry_id, *, reveal=True):
    rows = (
        await db.execute(
            select(EntryWork, Work, QueueWork.priority)
            .join(Work, Work.id == EntryWork.work_id)
            .join(
                QueueWork,
                (QueueWork.work_id == EntryWork.work_id)
                & (QueueWork.queue_id == EntryWork.queue_id),
            )
            .where(EntryWork.entry_id == entry_id)
            .order_by(QueueWork.priority, Work.id)
        )
    ).all()
    return [
        ParticipantWork(
            work_id=w.id,
            title=w.title,
            priority=p,
            artifact_url=ew.artifact_url if reveal else None,
        )
        for ew, w, p in rows
    ]


async def participants(db, queue, actor=None, additional=None):
    ordered = await entries(db, queue.id)
    rows = ordered + (
        [additional] if additional and additional.id not in {e.id for e in ordered} else []
    )
    if not rows:
        return []
    ids = [e.id for e in ordered]
    waiting = [e.id for e in ordered if e.status == EntryStatus.waiting]
    users = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_([e.student_id for e in rows])))
    }
    attempts = {
        a.entry_id: a
        for a in await db.scalars(
            select(Attempt).where(
                Attempt.entry_id.in_([e.id for e in rows]), Attempt.status.in_(ACTIVE_ATTEMPTS)
            )
        )
    }
    work_rows = (
        await db.execute(
            select(EntryWork, Work, QueueWork.priority)
            .join(Work, Work.id == EntryWork.work_id)
            .join(
                QueueWork,
                (QueueWork.work_id == EntryWork.work_id)
                & (QueueWork.queue_id == EntryWork.queue_id),
            )
            .where(EntryWork.entry_id.in_([e.id for e in rows]))
            .order_by(QueueWork.priority, Work.id)
        )
    ).all()
    grouped = {}
    for ew, work, priority in work_rows:
        grouped.setdefault(ew.entry_id, []).append((ew, work, priority))
    result = []
    for entry in rows:
        reveal = bool(
            actor and (actor.id == entry.student_id or actor.role in ("teacher", "admin"))
        )
        works = [
            ParticipantWork(
                work_id=w.id,
                title=w.title,
                priority=p,
                artifact_url=ew.artifact_url if reveal else None,
            )
            for ew, w, p in grouped.get(entry.id, [])
        ]
        position = ids.index(entry.id) + 1 if entry.id in ids else None
        attempt = attempts.get(entry.id)
        result.append(
            Participant(
                id=entry.id,
                student_id=entry.student_id,
                student_name=users[entry.student_id].name,
                queue_id=queue.id,
                status=entry.status,
                position=position,
                is_reserve=bool(position and queue.quota is not None and position > queue.quota),
                is_top3=entry.id in waiting[:3],
                self_moves_count=entry.self_moves_count,
                teacher_id=attempt.teacher_id if attempt else None,
                attempt_id=attempt.id if attempt else None,
                works=works,
                work_id=works[0].work_id if len(works) == 1 else None,
                artifact_url=works[0].artifact_url if len(works) == 1 else None,
            )
        )
    return result


async def participant(db, queue, entry, actor=None):
    return next(p for p in await participants(db, queue, actor, entry) if p.id == entry.id)


async def notify(db, user_id, queue_id, kind, payload):
    db.add(
        Notification(
            user_id=user_id, queue_id=queue_id, type=kind, payload=payload, created_at=now()
        )
    )


async def event(db, queue, actor, kind="queue.updated", payload=None):
    queue.updated_at = now()
    await db.flush()
    students = [p.model_dump(mode="json") for p in await participants(db, queue)]
    db.add(
        QueueEvent(
            queue_id=queue.id,
            actor_id=actor.id,
            event_type=kind,
            payload={
                "queue_id": str(queue.id),
                "stage": queue.stage,
                "registration_open": queue.registration_open,
                "students": students,
                **(payload or {}),
            },
            created_at=now(),
        )
    )


async def expire_swaps(db, queue):
    offers = await db.scalars(
        select(SwapOffer).where(
            SwapOffer.queue_id == queue.id,
            SwapOffer.status == SwapStatus.pending,
            SwapOffer.created_at <= now() - SWAP_TTL,
        )
    )
    for offer in list(offers):
        await resolve_offer(db, offer, SwapStatus.expired)


async def swap_read(db, offer):
    ordered = [e.id for e in await entries(db, offer.queue_id)]
    a, b = (
        await db.get(QueueEntry, offer.from_entry_id),
        await db.get(QueueEntry, offer.to_entry_id),
    )
    return SwapRead(
        swap_id=offer.id,
        queue_id=offer.queue_id,
        from_entry_id=a.id,
        to_entry_id=b.id,
        from_student_id=a.student_id,
        to_student_id=b.student_id,
        from_position=ordered.index(a.id) + 1 if a.id in ordered else None,
        to_position=ordered.index(b.id) + 1 if b.id in ordered else None,
        status=offer.status,
        created_at=offer.created_at,
        expires_at=offer.created_at + SWAP_TTL,
        resolved_at=offer.resolved_at,
    )


async def resolve_offer(db, offer, status):
    offer.status, offer.resolved_at = status, now()
    read = (await swap_read(db, offer)).model_dump(mode="json")
    for field in ("from_student_id", "to_student_id"):
        await notify(
            db, uuid.UUID(read[field]), offer.queue_id, "switch.resolved", {"request": read}
        )


async def invalidate_swaps(db, queue_id, entry_id=None):
    query = select(SwapOffer).where(
        SwapOffer.queue_id == queue_id, SwapOffer.status == SwapStatus.pending
    )
    if entry_id:
        query = query.where(
            or_(SwapOffer.from_entry_id == entry_id, SwapOffer.to_entry_id == entry_id)
        )
    for offer in list(await db.scalars(query)):
        await resolve_offer(db, offer, SwapStatus.cancelled)


async def propose_swap(db, queue_id, actor, from_id, to_id):
    queue = await get_queue(db, queue_id, actor, lock=True)
    mutable(queue)
    await expire_swaps(db, queue)
    a = await own_entry(db, queue_id, actor, from_id)
    b = await db.get(QueueEntry, to_id)
    if not b or b.queue_id != queue_id:
        raise HTTPException(404, "Entry not found")
    if a.id == b.id:
        raise HTTPException(400, "Cannot swap an entry with itself")
    await self_action(db, queue, a)
    await self_action(db, queue, b)
    pending = await db.scalar(
        select(SwapOffer.id).where(
            SwapOffer.queue_id == queue_id,
            SwapOffer.status == SwapStatus.pending,
            or_(SwapOffer.from_entry_id.in_([a.id, b.id]), SwapOffer.to_entry_id.in_([a.id, b.id])),
        )
    )
    if pending:
        raise HTTPException(409, "A participant already has a pending swap")
    offer = SwapOffer(
        queue_id=queue_id,
        from_entry_id=a.id,
        to_entry_id=b.id,
        status=SwapStatus.pending,
        created_at=now(),
    )
    db.add(offer)
    await db.flush()
    read = await swap_read(db, offer)
    await notify(
        db,
        b.student_id,
        queue_id,
        "switch.incoming",
        {"request": read.model_dump(mode="json"), "expiresAt": read.expires_at.isoformat()},
    )
    return read


async def respond_swap(db, queue_id, offer_id, actor, action):
    queue = await get_queue(db, queue_id, actor, lock=True)
    offer = await db.get(SwapOffer, offer_id)
    if not offer or offer.queue_id != queue_id:
        raise HTTPException(404, "Swap not found")
    a, b = (
        await db.get(QueueEntry, offer.from_entry_id),
        await db.get(QueueEntry, offer.to_entry_id),
    )
    expected = a.student_id if action == "cancel" else b.student_id
    if actor.id != expected:
        raise HTTPException(403, "This swap action belongs to another participant")
    await expire_swaps(db, queue)
    if offer.status != SwapStatus.pending:
        return await swap_read(db, offer)  # Retrying cannot swap twice.
    status = {
        "accept": SwapStatus.accepted,
        "decline": SwapStatus.declined,
        "cancel": SwapStatus.cancelled,
    }[action]
    if action == "accept":
        try:
            await self_action(db, queue, a)
            await self_action(db, queue, b)
        except HTTPException:
            status = SwapStatus.cancelled
        if status == SwapStatus.accepted:
            a.sort_key, b.sort_key = b.sort_key, a.sort_key
            a.updated_at = b.updated_at = now()
            await db.flush()
    await resolve_offer(db, offer, status)
    if status == SwapStatus.accepted:
        await event(db, queue, actor)
    return await swap_read(db, offer)


async def active_ban(db, queue_id, student_id):
    return await db.scalar(
        select(RegistrationBan).where(
            RegistrationBan.queue_id == queue_id,
            RegistrationBan.student_id == student_id,
            RegistrationBan.lifted_at.is_(None),
        )
    )


async def promoted(db, queue, before):
    """Notify the first reserve when an occupied quota place becomes available."""
    if queue.quota is None:
        return
    after = await entries(db, queue.id)
    previous = {e.id for e in before[: queue.quota]}
    for entry in after[: queue.quota]:
        if entry.id not in previous:
            await notify(
                db, entry.student_id, queue.id, "entry.promoted", {"entry_id": str(entry.id)}
            )
