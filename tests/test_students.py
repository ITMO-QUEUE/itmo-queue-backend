import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager
from itmo_queue.main import app

BASE_URL = "http://test/api/v1"
ADMIN_HEADERS = {"x-admin-token": "super_secret_admin_token"}


@pytest.mark.asyncio
async def test_create_and_get_student():
    """Успешное создание и получение студента (с правами админа)"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Генерируем уникальные логин и ИСУ для этого запуска
            unique_id = str(uuid.uuid4())[:8]
            login = f"test_student_{unique_id}"
            isu = f"999{unique_id}"

            response = await client.post(
                f"{BASE_URL}/students",
                json={"name": "Тест Студент", "login": login, "isu": isu, "password": "password123"},
                headers=ADMIN_HEADERS
            )
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "Тест Студент"
            assert data["isu"] == isu
            assert "password" not in data
            assert data["role"] == "student"
            student_id = data["id"]

            get_response = await client.get(f"{BASE_URL}/students/{student_id}", headers=ADMIN_HEADERS)
            assert get_response.status_code == 200
            assert get_response.json()["login"] == login


@pytest.mark.asyncio
async def test_create_student_no_rights():
    """Ошибка 403: Создание без прав администратора"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"{BASE_URL}/students",
                json={"name": "Хакер", "login": "hacker", "isu": "000000", "password": "password123"}
            )
            assert response.status_code == 403
            assert response.json()["detail"] == "Access denied. Admin rights required."


@pytest.mark.asyncio
async def test_duplicate_student_error():
    """Ошибка 409: Дублирование логина или ИСУ"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            payload = {"name": "Дубликат", "login": f"dup_{unique_id}", "isu": f"111{unique_id}",
                       "password": "password123"}

            await client.post(f"{BASE_URL}/students", json=payload, headers=ADMIN_HEADERS)

            # Пытаемся создать точно такого же - ожидаем 409
            response = await client.post(f"{BASE_URL}/students", json=payload, headers=ADMIN_HEADERS)
            assert response.status_code == 409
            assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_soft_delete_student():
    """Мягкое удаление: аккаунт отключается, но остается в базе"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]

            res = await client.post(
                f"{BASE_URL}/students",
                json={"name": "На удаление", "login": f"del_{unique_id}", "isu": f"222{unique_id}",
                      "password": "password123"},
                headers=ADMIN_HEADERS
            )
            assert res.status_code == 201
            student_id = res.json()["id"]

            del_res = await client.delete(f"{BASE_URL}/students/{student_id}", headers=ADMIN_HEADERS)
            assert del_res.status_code == 204

            get_res = await client.get(f"{BASE_URL}/students/{student_id}", headers=ADMIN_HEADERS)
            assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_search_and_pagination():
    """Проверка поиска и пагинации"""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_id = str(uuid.uuid4())[:8]
            alpha_login = f"alpha_{unique_id}"

            await client.post(f"{BASE_URL}/students",
                              json={"name": "Альфа", "login": alpha_login, "isu": f"333{unique_id}",
                                    "password": "pass123"}, headers=ADMIN_HEADERS)
            await client.post(f"{BASE_URL}/students",
                              json={"name": "Бета", "login": f"beta_{unique_id}", "isu": f"444{unique_id}",
                                    "password": "pass123"}, headers=ADMIN_HEADERS)

            # Ищем по уникальному логину Альфы
            response = await client.get(f"{BASE_URL}/students?search={alpha_login}", headers=ADMIN_HEADERS)
            assert response.status_code == 200
            data = response.json()

            assert len(data["items"]) >= 1
            assert data["items"][0]["login"] == alpha_login
            assert data["limit"] == 20