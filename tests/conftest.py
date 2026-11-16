import uuid
from datetime import UTC, datetime

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.db.models.user import User
from itmo_queue.main import app
from itmo_queue.models import Discipline, Queue, QueueStage, QueueWork, Work


@pytest.fixture
async def db_session():
    """Фикстура для получения сессии базы данных внутри тестов."""
    async with LifespanManager(app):
        # Берем фабрику сессий прямо из состояния приложения
        async with app.state.session_factory() as session:
            yield session


@pytest.fixture
async def student_client(db_session: AsyncSession):
    """Создает клиента, авторизованного как студент, и мокает проверку токена."""
    student = User(
        name="Test Student",
        login=f"student_{uuid.uuid4().hex[:6]}",
        password_hash="hashed",
        role="student",
    )
    db_session.add(student)
    await db_session.commit()
    await db_session.refresh(student)

    # Мокаем авторизацию, чтобы не ходить в Redis
    app.dependency_overrides[get_current_user] = lambda: student

    # Клиент использует уже запущенное приложение (Lifespan запущен в db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, student

    app.dependency_overrides.clear()


@pytest.fixture
async def setup_queue(db_session, student_client):
    """Вспомогательная фикстура для создания очереди и работы (доступна всем тестам)."""
    client, student = student_client

    discipline = Discipline(name=f"Disc {uuid.uuid4().hex[:4]}")
    db_session.add(discipline)
    await db_session.flush()

    now = datetime.now(UTC)

    queue = Queue(
        discipline_id=discipline.id,
        created_by=student.id,
        title="Test Queue",
        planned_start=now,
        planned_end=now,
        stage=QueueStage.active,
        registration_open=True,
        quota=2,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(queue)

    work = Work(discipline_id=discipline.id, title="Lab 1", position_no=1, is_active=True)
    db_session.add(work)
    await db_session.flush()

    queue_work = QueueWork(queue_id=queue.id, work_id=work.id, priority=1)
    db_session.add(queue_work)
    await db_session.commit()

    return queue, work
