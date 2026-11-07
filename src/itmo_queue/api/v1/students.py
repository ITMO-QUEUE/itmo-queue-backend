import uuid
from typing import Annotated  # <-- ДОБАВЛЕНО
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.db.session import get_db_session
from itmo_queue.db.models.student import Student
from itmo_queue.api.schemas.student import StudentCreate, StudentUpdate, StudentRead, StudentList
from itmo_queue.api.deps import get_current_admin


def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')


router = APIRouter(prefix="/students", tags=["students"])

# Создаем алиас для сессии, чтобы линтер не ругался на Depends в аргументах
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.post(
    "",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createStudent",
    dependencies=[Depends(get_current_admin)],
    responses={
        403: {"description": "Access denied. Admin rights required."},
        409: {"description": "Login or ISU already exists"}
    }
)
async def create_student(payload: StudentCreate, session: SessionDep):  # <-- ИСПОЛЬЗУЕМ SessionDep
    student = Student(
        name=payload.name,
        login=payload.login,
        isu=payload.isu,
        password_hash=get_password_hash(payload.password)
    )
    try:
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return student
    except IntegrityError as err:  # <-- ДОБАВЛЕНО as err
        await session.rollback()
        raise HTTPException(status_code=409, detail="Login or ISU already exists") from err  # <-- ДОБАВЛЕНО from err


@router.get(
    "",
    response_model=StudentList,
    operation_id="listStudents",
    dependencies=[Depends(get_current_admin)],
    responses={403: {"description": "Access denied. Admin rights required."}}
)
async def list_students(
        session: SessionDep,
        limit: int = Query(20, ge=1, le=100, description="Page size"),
        offset: int = Query(0, ge=0, description="Offset"),
        search: str | None = Query(None, description="Search by name, login, or ISU"),
):
    # Исправлено сравнение с True (E712)
    query = select(Student).where(Student.is_active.is_(True))

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            or_(
                Student.name.ilike(search_filter),
                Student.login.ilike(search_filter),
                Student.isu.ilike(search_filter)
            )
        )

    total_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(total_query)

    result = await session.scalars(
        query.order_by(Student.created_at.desc()).limit(limit).offset(offset)
    )
    return {"items": result.all(), "total": total, "limit": limit, "offset": offset}


@router.get(
    "/{student_id}",
    response_model=StudentRead,
    operation_id="getStudent",
    dependencies=[Depends(get_current_admin)],
    responses={
        403: {"description": "Access denied. Admin rights required."},
        404: {"description": "Student not found"}
    }
)
async def get_student(student_id: uuid.UUID, session: SessionDep):
    student = await session.get(Student, student_id)
    if not student or not student.is_active:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.patch(
    "/{student_id}",
    response_model=StudentRead,
    operation_id="updateStudent",
    dependencies=[Depends(get_current_admin)],
    responses={
        403: {"description": "Access denied. Admin rights required."},
        404: {"description": "Student not found"},
        409: {"description": "Login or ISU already exists"}
    }
)
async def update_student(
        student_id: uuid.UUID,
        payload: StudentUpdate,
        session: SessionDep
):
    student = await session.get(Student, student_id)
    if not student or not student.is_active:
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
    dependencies=[Depends(get_current_admin)],
    responses={
        403: {"description": "Access denied. Admin rights required."},
        404: {"description": "Student not found"}
    }
)
async def delete_student(student_id: uuid.UUID, session: SessionDep):
    student = await session.get(Student, student_id)
    if not student or not student.is_active:
        raise HTTPException(status_code=404, detail="Student not found")

    student.is_active = False
    await session.commit()