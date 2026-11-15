"""Administrator provisioning for a working installation without manual SQL."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from itmo_queue.api.deps import get_current_admin, get_current_user
from itmo_queue.api.schemas.queue import StrictModel, UserCreate, UserProfile
from itmo_queue.core.security import get_password_hash
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session
from itmo_queue.models import Discipline, Stream, TeacherDiscipline, Work

router = APIRouter(tags=["administration"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Admin = Annotated[User, Depends(get_current_admin)]
Actor = Annotated[User, Depends(get_current_user)]


class NamedCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)


class NamedRead(NamedCreate):
    id: uuid.UUID


class WorkCreate(StrictModel):
    discipline_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    position_no: int = Field(ge=1)
    is_active: bool = True


class WorkRead(WorkCreate):
    id: uuid.UUID


class WorkUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    position_no: int | None = Field(default=None, ge=1)
    is_active: bool | None = None

    @model_validator(mode="after")
    def nonnull(self):
        if any(getattr(self, f) is None for f in self.model_fields_set):
            raise ValueError("Explicit null is not allowed")
        return self


class UserUpdate(StrictModel):
    is_active: bool | None = None
    can_create_queues: bool | None = None
    stream_id: uuid.UUID | None = None
    discipline_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def nonnull(self):
        if any(getattr(self, f) is None for f in self.model_fields_set - {"stream_id"}):
            raise ValueError("Explicit null is not allowed")
        return self


async def commit(db):
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "A unique value already exists or a reference is in use") from exc


@router.post(
    "/auth/register", response_model=UserProfile, status_code=201, operation_id="registerUser"
)
async def register(payload: UserCreate, db: DB, actor: Admin):
    if payload.stream_id and not await db.get(Stream, payload.stream_id):
        raise HTTPException(422, "Stream not found")
    user = User(
        **payload.model_dump(exclude={"password"}),
        created_by=actor.id,
        password_hash=await run_in_threadpool(get_password_hash, payload.password),
    )
    db.add(user)
    await commit(db)
    return user


@router.get("/users", response_model=list[UserProfile], operation_id="listUsers")
async def users(
    db: DB, actor: Admin, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return list(
        await db.scalars(select(User).order_by(User.name, User.id).limit(limit).offset(offset))
    )


@router.patch("/users/{user_id}", response_model=UserProfile, operation_id="updateUserAccess")
async def update_user(user_id: uuid.UUID, payload: UserUpdate, db: DB, actor: Admin):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user_id == actor.id and payload.is_active is False:
        raise HTTPException(409, "Cannot deactivate your own administrator account")
    if payload.stream_id and not await db.get(Stream, payload.stream_id):
        raise HTTPException(422, "Stream not found")
    if payload.discipline_ids is not None:
        if user.role != "teacher":
            raise HTTPException(422, "Discipline permissions apply to teachers")
        ids = set(payload.discipline_ids)
        if ids != set(await db.scalars(select(Discipline.id).where(Discipline.id.in_(ids)))):
            raise HTTPException(422, "Discipline not found")
        await db.execute(delete(TeacherDiscipline).where(TeacherDiscipline.teacher_id == user_id))
        db.add_all([TeacherDiscipline(teacher_id=user_id, discipline_id=d) for d in ids])
    for f, v in payload.model_dump(exclude_unset=True, exclude={"discipline_ids"}).items():
        setattr(user, f, v)
    await commit(db)
    return user


@router.get("/disciplines", response_model=list[NamedRead], operation_id="listDisciplines")
async def disciplines(db: DB, actor: Actor):
    return list(await db.scalars(select(Discipline).order_by(Discipline.name)))


@router.post(
    "/disciplines", response_model=NamedRead, status_code=201, operation_id="createDiscipline"
)
async def create_discipline(payload: NamedCreate, db: DB, actor: Admin):
    row = Discipline(name=payload.name)
    db.add(row)
    await commit(db)
    return row


@router.patch(
    "/disciplines/{discipline_id}", response_model=NamedRead, operation_id="renameDiscipline"
)
async def rename_discipline(discipline_id: uuid.UUID, payload: NamedCreate, db: DB, actor: Admin):
    row = await db.get(Discipline, discipline_id)
    if not row:
        raise HTTPException(404, "Discipline not found")
    row.name = payload.name
    await commit(db)
    return row


@router.get("/streams", response_model=list[NamedRead], operation_id="listStreams")
async def streams(db: DB, actor: Actor):
    return list(await db.scalars(select(Stream).order_by(Stream.name)))


@router.post("/streams", response_model=NamedRead, status_code=201, operation_id="createStream")
async def create_stream(payload: NamedCreate, db: DB, actor: Admin):
    row = Stream(name=payload.name)
    db.add(row)
    await commit(db)
    return row


@router.get("/works", response_model=list[WorkRead], operation_id="listWorks")
async def works(
    db: DB,
    actor: Actor,
    discipline: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    query = select(Work)
    if actor.role != "admin":
        query = query.where(Work.is_active.is_(True))
    if discipline:
        query = query.where(Work.discipline_id == discipline)
    return list(
        await db.scalars(query.order_by(Work.position_no, Work.id).limit(limit).offset(offset))
    )


@router.post("/works", response_model=WorkRead, status_code=201, operation_id="createWork")
async def create_work(payload: WorkCreate, db: DB, actor: Admin):
    if not await db.get(Discipline, payload.discipline_id):
        raise HTTPException(422, "Discipline not found")
    row = Work(**payload.model_dump())
    db.add(row)
    await commit(db)
    return row


@router.patch("/works/{work_id}", response_model=WorkRead, operation_id="updateWork")
async def update_work(work_id: uuid.UUID, payload: WorkUpdate, db: DB, actor: Admin):
    row = await db.get(Work, work_id)
    if not row:
        raise HTTPException(404, "Work not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await commit(db)
    return row
