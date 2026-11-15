import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from itmo_queue.api.deps import get_current_user
from itmo_queue.db.models.user import User
from itmo_queue.main import app


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
