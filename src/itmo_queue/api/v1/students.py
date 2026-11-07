import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.schemas.student import (
    StudentCreate,
    StudentList,
    StudentRead,
    StudentUpdate,
)
from itmo_queue.core.security import get_password_hash
from itmo_queue.db.models.user import User
from itmo_queue.db.session import get_db_session

router = APIRouter(prefix="/students", tags=["students"])

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.post(
    "",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createStudent",
    responses={
        409: {"description": "Login or ISU already exists"},
    },
)
async def create_student(payload: StudentCreate, session: SessionDep):
    # TODO: Добавить проверку прав администратора
    student = User(
        name=payload.name,
        login=payload.login,
        isu=payload.isu,
        password_hash=get_password_hash(payload.password),
        role="student",  # Принудительно ставим роль студент
    )
    try:
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return student
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Login or ISU already exists") from err


@router.get(
    "",
    response_model=StudentList,
    operation_id="listStudents",
)
async def list_students(
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset"),
    search: str | None = Query(None, description="Search by name, login, or ISU"),
):
    # TODO: Добавить проверку прав администратора

    # Ищем только тех, у кого роль student и кто активен
    query = select(User).where(User.role == "student", User.is_active.is_(True))

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            or_(
                User.name.ilike(search_filter),
                User.login.ilike(search_filter),
                User.isu.ilike(search_filter),
            )
        )

    total_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(total_query)

    result = await session.scalars(
        query.order_by(User.created_at.desc()).limit(limit).offset(offset)
    )
    return {
        "items": result.all(),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{student_id}",
    response_model=StudentRead,
    operation_id="getStudent",
    responses={
        404: {"description": "Student not found"},
    },
)
async def get_student(student_id: uuid.UUID, session: SessionDep):
    # TODO: Добавить проверку прав администратора
    student = await session.get(User, student_id)
    # Проверяем, что это именно студент и он активен
    if not student or not student.is_active or student.role != "student":
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.patch(
    "/{student_id}",
    response_model=StudentRead,
    operation_id="updateStudent",
    responses={
        404: {"description": "Student not found"},
        409: {"description": "Login or ISU already exists"},
    },
)
async def update_student(student_id: uuid.UUID, payload: StudentUpdate, session: SessionDep):
    # TODO: Добавить проверку прав администратора
    student = await session.get(User, student_id)
    if not student or not student.is_active or student.role != "student":
        raise HTTPException(status_code=404, detail="Student not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "password" in update_data:
        student.password_hash = get_password_hash(update_data.pop("password"))

    for field, value in update_data.items():
        setattr(student, field, value)

    try:
        await session.commit()
        await session.refresh(student)
        return student
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Login or ISU already exists") from err


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteStudent",
    responses={
        404: {"description": "Student not found"},
    },
)
async def delete_student(student_id: uuid.UUID, session: SessionDep):
    # TODO: Добавить проверку прав администратора
    student = await session.get(User, student_id)
    if not student or not student.is_active or student.role != "student":
        raise HTTPException(status_code=404, detail="Student not found")

    student.is_active = False
    await session.commit()
