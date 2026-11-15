import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.api.schemas.queue import (
    AttemptRead,
    BanRead,
    Evaluation,
    Participant,
    ParticipantAction,
    Reason,
)
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import (
    Attempt,
    AttemptResult,
    AttemptStatus,
    EntryStatus,
    QueueEntry,
    QueueEvent,
    QueueStage,
    RegistrationBan,
)
from itmo_queue.services import queue as svc

router = APIRouter(tags=["defense"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Actor = Annotated[User, Depends(get_current_user)]
Key = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=100,
        description="Use the same key when retrying one teacher action after a lost response",
    ),
]


async def replay(db, queue_id, actor, key, action):
    if not key:
        return None
    previous = await db.scalar(
        select(QueueEvent).where(
            QueueEvent.queue_id == queue_id,
            QueueEvent.actor_id == actor.id,
            QueueEvent.event_type == "action.receipt",
            QueueEvent.payload["key"].astext == key,
        )
    )
    if previous and previous.payload["action"] != action:
        raise HTTPException(409, "Idempotency key was used for another action")
    return previous.payload["result"] if previous else None


async def receipt(db, queue_id, actor, key, action, result):
    if key:
        db.add(
            QueueEvent(
                queue_id=queue_id,
                actor_id=actor.id,
                event_type="action.receipt",
                payload={"key": key, "action": action, "result": result},
                created_at=svc.now(),
            )
        )


async def student_entry(db, queue_id, student_id):
    entry = await db.scalar(
        select(QueueEntry).where(
            QueueEntry.queue_id == queue_id,
            QueueEntry.student_id == student_id,
            QueueEntry.status.in_(svc.ACTIVE_ENTRIES),
        )
    )
    if not entry:
        raise HTTPException(404, "Active student entry not found")
    return entry


async def call(db, queue, actor, entry=None, confirm=False):
    if queue.stage != QueueStage.active:
        raise HTTPException(409, "Start the session before calling students")
    waiting = await svc.entries(db, queue.id, waiting=True)
    busy_students = set(
        await db.scalars(
            select(QueueEntry.student_id)
            .join(Attempt)
            .where(Attempt.status.in_(svc.ACTIVE_ATTEMPTS))
        )
    )
    available = [e for e in waiting if e.student_id not in busy_students]
    selected = entry or (available[0] if available else None)
    # Fixed lock order protects both teacher and student across different sessions.
    lock_ids = {actor.id} | ({selected.student_id} if selected else set())
    list(
        await db.scalars(
            select(User).where(User.id.in_(lock_ids)).order_by(User.id).with_for_update()
        )
    )
    busy = await db.scalar(
        select(Attempt).where(
            Attempt.teacher_id == actor.id, Attempt.status.in_(svc.ACTIVE_ATTEMPTS)
        )
    )
    if busy:
        if entry and busy.entry_id == entry.id:
            return busy
        raise HTTPException(409, "Finish your current attempt first")
    if not selected:
        raise HTTPException(409, "No available waiting students")
    entry = selected
    if entry.status != EntryStatus.waiting:
        raise HTTPException(409, "Student is already assigned")
    student_busy = await db.scalar(
        select(Attempt.id)
        .join(QueueEntry)
        .where(QueueEntry.student_id == entry.student_id, Attempt.status.in_(svc.ACTIVE_ATTEMPTS))
    )
    if student_busy:
        raise HTTPException(
            409, "Student has a current attempt in another session; retry next call"
        )
    out_of_order = bool(available and entry.id != available[0].id)
    if out_of_order and not confirm:
        raise HTTPException(409, "Confirm an out-of-order call")
    attempt = Attempt(
        entry_id=entry.id,
        teacher_id=actor.id,
        status=AttemptStatus.called,
        called_at=svc.now(),
        called_out_of_order=out_of_order,
    )
    db.add(attempt)
    entry.status, entry.updated_at = EntryStatus.called, svc.now()
    await svc.invalidate_swaps(db, queue.id, entry.id)
    await db.flush()
    await svc.notify(
        db,
        entry.student_id,
        queue.id,
        "participant.called",
        {
            "entry_id": str(entry.id),
            "attempt_id": str(attempt.id),
            "teacher_id": str(actor.id),
            "teacher_name": actor.name,
            "location": queue.location,
        },
    )
    await svc.event(db, queue, actor, "participant.status_changed")
    return attempt


@router.post(
    "/queues/{queue_id}/call-next", response_model=AttemptRead, operation_id="callNextStudent"
)
async def call_next(queue_id: uuid.UUID, db: DB, actor: Actor, idempotency_key: Key = None):
    queue = await svc.get_queue(db, queue_id, actor, lock=True, manage=True)
    previous = await replay(db, queue_id, actor, idempotency_key, "call-next")
    if previous:
        return previous
    result = AttemptRead.model_validate(await call(db, queue, actor))
    await receipt(db, queue_id, actor, idempotency_key, "call-next", result.model_dump(mode="json"))
    await db.commit()
    return result


@router.patch(
    "/queues/{queue_id}/students/{student_id}",
    response_model=Participant,
    operation_id="changeParticipantStatus",
)
async def participant_status(
    queue_id: uuid.UUID,
    student_id: uuid.UUID,
    payload: ParticipantAction,
    db: DB,
    actor: Actor,
    idempotency_key: Key = None,
):
    queue = await svc.get_queue(db, queue_id, actor, lock=True, manage=True)
    action = f"{student_id}:{payload.model_dump_json()}"
    previous = await replay(db, queue_id, actor, idempotency_key, action)
    if previous:
        return previous
    svc.mutable(queue)
    entry = await student_entry(db, queue_id, student_id)
    if payload.status == "called":
        await call(db, queue, actor, entry, payload.confirm_out_of_order)
    elif payload.status == "in_defense":
        attempt = await db.get(Attempt, payload.attempt_id) if payload.attempt_id else None
        if not attempt or attempt.entry_id != entry.id:
            raise HTTPException(422, "A matching attempt_id is required")
        if actor.role != "admin" and attempt.teacher_id != actor.id:
            raise HTTPException(403, "Only the receiving teacher can start this defense")
        if attempt.status == AttemptStatus.called:
            attempt.status, attempt.started_at = AttemptStatus.in_progress, svc.now()
            entry.status, entry.updated_at = EntryStatus.in_defense, svc.now()
            await svc.event(db, queue, actor, "participant.status_changed")
        elif attempt.status != AttemptStatus.in_progress:
            raise HTTPException(409, "Attempt is already closed")
    else:
        if not payload.reason:
            raise HTTPException(422, "A reason is required for a teacher move")
        if entry.status == EntryStatus.in_defense:
            raise HTTPException(409, "Cannot move a defense in progress")
        before = await svc.entries(db, queue_id)
        active = await db.scalar(
            select(Attempt).where(
                Attempt.entry_id == entry.id, Attempt.status == AttemptStatus.called
            )
        )
        if active:
            active.status, active.finished_at = AttemptStatus.aborted, svc.now()
        entry.status, entry.sort_key, entry.updated_at = (
            EntryStatus.waiting,
            await svc.tail(db, queue_id),
            svc.now(),
        )
        await svc.invalidate_swaps(db, queue_id, entry.id)
        await db.flush()
        await svc.promoted(db, queue, before)
        await svc.notify(db, student_id, queue_id, "entry.moved", {"reason": payload.reason})
        await svc.event(db, queue, actor, payload={"reason": payload.reason})
    result = await svc.participant(db, queue, entry, actor)
    await receipt(db, queue_id, actor, idempotency_key, action, result.model_dump(mode="json"))
    await db.commit()
    return result


@router.post(
    "/queues/{queue_id}/students/{student_id}/evaluate",
    response_model=AttemptRead,
    operation_id="evaluateStudent",
)
async def evaluate(
    queue_id: uuid.UUID, student_id: uuid.UUID, payload: Evaluation, db: DB, actor: Actor
):
    queue = await svc.get_queue(db, queue_id, actor, lock=True, manage=True)
    attempt = await db.get(Attempt, payload.attempt_id)
    entry = await db.get(QueueEntry, attempt.entry_id) if attempt else None
    if not entry or entry.queue_id != queue_id or entry.student_id != student_id:
        raise HTTPException(404, "Attempt not found")
    if actor.role != "admin" and attempt.teacher_id != actor.id:
        raise HTTPException(403, "Only the receiving teacher can evaluate this attempt")
    if attempt.status not in svc.ACTIVE_ATTEMPTS:
        # Replay is safe, but changing a completed result is forbidden.
        expected = AttemptStatus.no_show if payload.status == "absent" else AttemptStatus.finished
        stored = list(
            await db.scalars(select(AttemptResult).where(AttemptResult.attempt_id == attempt.id))
        )
        saved = {r.work_id: (r.kind, r.score, r.comment) for r in stored}
        wanted = {r.work_id: (r.kind, r.score, r.comment) for r in payload.results}
        if attempt.status != expected or saved != wanted:
            raise HTTPException(409, "Attempt result is immutable")
        return AttemptRead.model_validate(attempt)
    svc.mutable(queue)
    before = await svc.entries(db, queue_id)
    if payload.status == "absent":
        if attempt.status != AttemptStatus.called:
            raise HTTPException(409, "Defense already started; no-show is not applicable")
        attempt.status = AttemptStatus.no_show
        entry.status, entry.sort_key = EntryStatus.waiting, await svc.tail(db, queue_id)
    else:
        if attempt.status != AttemptStatus.in_progress:
            raise HTTPException(409, "Start the defense before recording results")
        allowed = {w.work_id for w in await svc.artifacts(db, entry.id)}
        if not {r.work_id for r in payload.results} <= allowed:
            raise HTTPException(422, "Results must refer to enrolled works")
        db.add_all(
            [AttemptResult(attempt_id=attempt.id, **r.model_dump()) for r in payload.results]
        )
        attempt.status = AttemptStatus.finished
        entry.status, entry.closed_at = EntryStatus.completed, svc.now()
    attempt.finished_at, entry.updated_at = svc.now(), svc.now()
    await svc.invalidate_swaps(db, queue_id, entry.id)
    await db.flush()
    await svc.promoted(db, queue, before)
    await svc.notify(
        db,
        student_id,
        queue_id,
        "attempt.finished",
        {"attempt_id": str(attempt.id), "status": attempt.status},
    )
    await svc.event(db, queue, actor, "participant.status_changed")
    result = AttemptRead.model_validate(attempt)
    await db.commit()
    return result


@router.delete(
    "/queues/{queue_id}/students/{student_id}", status_code=204, operation_id="removeStudentEntry"
)
async def remove_student(
    queue_id: uuid.UUID, student_id: uuid.UUID, payload: Reason, db: DB, actor: Actor
):
    queue = await svc.get_queue(db, queue_id, actor, lock=True, manage=True)
    svc.mutable(queue)
    entry = await student_entry(db, queue_id, student_id)
    if entry.status == EntryStatus.in_defense:
        raise HTTPException(409, "Finish the defense before removing its entry")
    before = await svc.entries(db, queue_id)
    attempt = await db.scalar(
        select(Attempt).where(Attempt.entry_id == entry.id, Attempt.status == AttemptStatus.called)
    )
    if attempt:
        attempt.status, attempt.finished_at = AttemptStatus.aborted, svc.now()
    entry.status, entry.closed_at = EntryStatus.removed, svc.now()
    entry.removed_by, entry.removed_reason, entry.updated_at = actor.id, payload.reason, svc.now()
    if not await svc.active_ban(db, queue_id, student_id):
        db.add(
            RegistrationBan(
                queue_id=queue_id,
                student_id=student_id,
                reason=payload.reason,
                created_by=actor.id,
                created_at=svc.now(),
            )
        )
    await svc.invalidate_swaps(db, queue_id, entry.id)
    await db.flush()
    await svc.promoted(db, queue, before)
    await svc.notify(db, student_id, queue_id, "entry.removed", {"reason": payload.reason})
    await svc.event(db, queue, actor, payload={"reason": payload.reason})
    await db.commit()


@router.delete(
    "/queues/{queue_id}/students/{student_id}/ban",
    status_code=204,
    operation_id="liftRegistrationBan",
)
async def lift_ban(queue_id: uuid.UUID, student_id: uuid.UUID, db: DB, actor: Actor):
    queue = await svc.get_queue(db, queue_id, actor, lock=True, manage=True)
    svc.mutable(queue)
    ban = await svc.active_ban(db, queue_id, student_id)
    if ban:
        ban.lifted_by, ban.lifted_at = actor.id, svc.now()
        await svc.notify(db, student_id, queue_id, "entry.ban_lifted", {})
        await db.commit()


@router.get(
    "/queues/{queue_id}/bans", response_model=list[BanRead], operation_id="listRegistrationBans"
)
async def bans(queue_id: uuid.UUID, db: DB, actor: Actor):
    await svc.get_queue(db, queue_id, actor, manage=True)
    rows = (
        await db.execute(
            select(RegistrationBan, User.name)
            .join(User, User.id == RegistrationBan.student_id)
            .where(RegistrationBan.queue_id == queue_id, RegistrationBan.lifted_at.is_(None))
        )
    ).all()
    return [
        {
            "student_id": b.student_id,
            "student_name": name,
            "reason": b.reason,
            "created_at": b.created_at,
        }
        for b, name in rows
    ]
