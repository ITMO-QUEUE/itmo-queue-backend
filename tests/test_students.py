import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from itmo_queue.main import app

BASE_URL = "http://test/api/v1"
ADMIN_HEADERS = {"x-admin-token": "super_secret_admin_token"}


@pytest.mark.asyncio
async def test_create_and_get_student():
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            login = f"test_student_{unique_id}"

            response = await client.post(
                f"{BASE_URL}/students",
                json={
                    "name": "Тест Студент",
                    "login": login,
                    "isu": f"999{unique_id}",
                    "password": "password123",
                },
                headers=ADMIN_HEADERS,
            )
            assert response.status_code == 201
            student_id = response.json()["id"]

            get_response = await client.get(
                f"{BASE_URL}/students/{student_id}",
                headers=ADMIN_HEADERS,
            )
            assert get_response.status_code == 200


@pytest.mark.asyncio
async def test_create_student_no_rights():
    """Проверка, что без токена возвращается 403"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"{BASE_URL}/students",
                json={
                    "name": "Хакер",
                    "login": "hacker",
                    "isu": "000",
                    "password": "password123",
                },
            )
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_student_error():
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

            await client.post(
                f"{BASE_URL}/students",
                json=payload,
                headers=ADMIN_HEADERS,
            )
            response = await client.post(
                f"{BASE_URL}/students",
                json=payload,
                headers=ADMIN_HEADERS,
            )
            assert response.status_code == 409


@pytest.mark.asyncio
async def test_soft_delete_student():
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
                headers=ADMIN_HEADERS,
            )
            student_id = res.json()["id"]

            del_res = await client.delete(
                f"{BASE_URL}/students/{student_id}",
                headers=ADMIN_HEADERS,
            )
            assert del_res.status_code == 204

            get_res = await client.get(
                f"{BASE_URL}/students/{student_id}",
                headers=ADMIN_HEADERS,
            )
            assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_patch_null_values():
    """Проверка, что PATCH с явным null возвращает 422"""
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
                headers=ADMIN_HEADERS,
            )
            student_id = res.json()["id"]

            # Пытаемся передать null в имя
            response = await client.patch(
                f"{BASE_URL}/students/{student_id}",
                json={"name": None},
                headers=ADMIN_HEADERS,
            )
            # Pydantic должен заблокировать null
            assert response.status_code == 422
