import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.schemas.group import (
    GroupCreate,
    GroupList,
    GroupRead,
    GroupUpdate,
)
from itmo_queue.db.models.group import Group
from itmo_queue.db.session import get_db_session

router = APIRouter(prefix="/groups", tags=["groups"])

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.post(
    "",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createGroup",
    responses={409: {"description": "Group with this name already exists"}},
)
async def create_group(payload: GroupCreate, session: SessionDep):
    group = Group(**payload.model_dump())
    try:
        session.add(group)
        await session.commit()
        await session.refresh(group)
        return group
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Group with this name already exists") from err


@router.get("", response_model=GroupList, operation_id="listGroups")
async def list_groups(
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset"),
):
    total = await session.scalar(select(func.count()).select_from(Group))
    result = await session.scalars(
        select(Group).order_by(Group.created_at.desc()).limit(limit).offset(offset)
    )
    return {
        "items": result.all(),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{group_id}",
    response_model=GroupRead,
    operation_id="getGroup",
    responses={404: {"description": "Group not found"}},
)
async def get_group(group_id: uuid.UUID, session: SessionDep):
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.patch(
    "/{group_id}",
    response_model=GroupRead,
    operation_id="updateGroup",
    responses={
        404: {"description": "Group not found"},
        409: {"description": "Group with this name already exists"},
    },
)
async def update_group(group_id: uuid.UUID, payload: GroupUpdate, session: SessionDep):
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)

    try:
        await session.commit()
        await session.refresh(group)
        return group
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Group with this name already exists") from err


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteGroup",
    responses={404: {"description": "Group not found"}},
)
async def delete_group(group_id: uuid.UUID, session: SessionDep):
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await session.delete(group)
    await session.commit()
