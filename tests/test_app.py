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


async def test_openapi(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert set(schema["paths"]) == {"/health"}
    response_schema = schema["paths"]["/health"]["get"]["responses"]["200"]["content"]
    assert response_schema["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/HealthResponse"
    )
