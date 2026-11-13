import pytest
from httpx import ASGITransport, AsyncClient

from itmo_queue.core.config import Settings
from itmo_queue.main import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    # Closed local ports prove that liveness and startup require no services.
    settings = Settings(
        _env_file=None,
        environment="test",
        postgres_host="127.0.0.1",
        postgres_port=1,
        redis_url="redis://127.0.0.1:1/0",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


async def test_health_without_external_services(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_documentation(client, path):
    response = await client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/openapi.json" in response.text


@pytest.mark.asyncio
async def test_openapi(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")

    # Проверяем, что все наши новые маршруты появились в схеме
    expected_paths = {
        "/health",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/groups",
        "/api/v1/groups/{group_id}",
        "/api/v1/students",
        "/api/v1/students/{student_id}",
        "/api/v1/session-options",
        "/api/v1/queues",
        "/api/v1/queues/{queue_id}",
        "/api/v1/queues/{queue_id}/registration",
        "/api/v1/queues/{queue_id}/activate",
        "/api/v1/queues/{queue_id}/finish",
    }
    assert expected_paths == set(schema["paths"])


async def test_health_contract(client):
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/health"]["get"]
    assert operation["operationId"] == "getHealth"
    assert operation["summary"]
    assert operation["description"]
    assert operation["tags"] == ["Infrastructure"]
    assert "requestBody" not in operation
    assert not operation.get("parameters")
    assert set(operation["responses"]) == {"200"}
    model = schema["components"]["schemas"]["HealthResponse"]
    assert model["required"] == ["status"]
    assert model["properties"]["status"]["const"] == "ok"
    assert model["properties"]["status"]["description"]
    assert model["properties"]["status"]["examples"] == ["ok"]
