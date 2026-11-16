import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from itmo_queue.api.deps import get_current_admin
from itmo_queue.main import app

BASE_URL = "http://test/api/v1"


# Фикстура, которая подменяет проверку прав админа
@pytest.fixture
def mock_admin():
    async def override_get_current_admin():
        return True

    app.dependency_overrides[get_current_admin] = override_get_current_admin
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_and_get_student(mock_admin):
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            response = await client.post(
                f"{BASE_URL}/students",
                json={
                    "name": "Тест Студент",
                    "login": f"test_{unique_id}",
                    "isu": f"999{unique_id}",
                    "password": "password123",
                },
            )
            assert response.status_code == 201
            student_id = response.json()["id"]

            get_response = await client.get(f"{BASE_URL}/students/{student_id}")
            assert get_response.status_code == 200


@pytest.mark.asyncio
async def test_create_student_no_rights():
    """Без фикстуры mock_admin токена нет -> 401/403"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"{BASE_URL}/students",
                json={"name": "Хакер", "login": "hacker", "isu": "000", "password": "password123"},
            )
            assert response.status_code in [401, 403]  # У Григория возвращается 401, если нет куки


@pytest.mark.asyncio
async def test_duplicate_student_error(mock_admin):
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            payload = {
                "name": "Дубликат",
                "login": f"dup_{unique_id}",
                "isu": f"111{unique_id}",
                "password": "password123",
            }

            await client.post(f"{BASE_URL}/students", json=payload)
            response = await client.post(f"{BASE_URL}/students", json=payload)
            assert response.status_code == 409


@pytest.mark.asyncio
async def test_soft_delete_student(mock_admin):
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            res = await client.post(
                f"{BASE_URL}/students",
                json={
                    "name": "На удаление",
                    "login": f"del_{unique_id}",
                    "isu": f"222{unique_id}",
                    "password": "password123",
                },
            )
            assert res.status_code == 201
            student_id = res.json()["id"]

            del_res = await client.delete(f"{BASE_URL}/students/{student_id}")
            assert del_res.status_code == 204

            get_res = await client.get(f"{BASE_URL}/students/{student_id}")
            assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_patch_null_values(mock_admin):
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            res = await client.post(
                f"{BASE_URL}/students",
                json={
                    "name": "Для патча",
                    "login": f"patch_{unique_id}",
                    "isu": f"333{unique_id}",
                    "password": "password123",
                },
            )
            student_id = res.json()["id"]

            response = await client.patch(f"{BASE_URL}/students/{student_id}", json={"name": None})
            assert response.status_code == 422
