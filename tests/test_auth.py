import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from itmo_queue.api.deps import SESSION_COOKIE, session_key
from itmo_queue.core.config import Settings
from itmo_queue.core.redis import get_redis
from itmo_queue.core.security import get_password_hash
from itmo_queue.db.session import get_db_session
from itmo_queue.main import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
async def auth_context():
    user = SimpleNamespace(
        id=uuid.uuid4(),
        name="Admin",
        login="admin",
        role="admin",
        is_active=True,
        password_hash=get_password_hash("password"),
    )
    redis = MemoryRedis()

    class Database:
        async def scalar(self, query):
            return user if query.compile().params["login_1"] == user.login else None

        async def get(self, model, user_id):
            return user if user_id == user.id else None

    app = create_app(Settings(_env_file=None, SESSION_COOKIE_SECURE=False, SESSION_TTL_SECONDS=60))
    app.dependency_overrides[get_db_session] = lambda: Database()
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, user, redis


async def test_session_login_authorization_and_logout(auth_context):
    client, user, redis = auth_context
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.post("/api/v1/auth/login", json={"login": "admin", "password": "bad"})
    ).status_code == 401
    response = await client.post(
        "/api/v1/auth/login", json={"login": "admin", "password": "password"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=60" in cookie
    token = client.cookies[SESSION_COOKIE]
    assert redis.values == {session_key(token): str(user.id)}
    assert redis.ttls[session_key(token)] == 60
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    user.role = "student"
    assert (await client.get("/api/v1/students")).status_code == 403
    user.role = "admin"
    user.is_active = False
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    user.is_active = True
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert not redis.values
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_login_rotates_session_and_expiration_revokes_access(auth_context):
    client, _, redis = auth_context
    payload = {"login": "admin", "password": "password"}
    await client.post("/api/v1/auth/login", json=payload)
    old_token = client.cookies[SESSION_COOKIE]
    await client.post("/api/v1/auth/login", json=payload)
    new_token = client.cookies[SESSION_COOKIE]
    assert old_token != new_token
    assert session_key(old_token) not in redis.values
    await redis.delete(session_key(new_token))
    assert (await client.get("/api/v1/auth/me")).status_code == 401
