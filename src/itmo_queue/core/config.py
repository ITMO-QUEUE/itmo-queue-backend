from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class RedisURL:
    """Класс-обертка для удовлетворения тестов, ожидающих атрибуты URL."""

    def __init__(self, url_str: str):
        self._url_str = url_str
        parsed = urlparse(url_str)
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path
        self.db = parsed.path.lstrip("/") if parsed.path else "0"

    def __str__(self) -> str:
        return self._url_str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ITMO.Queue"
    environment: str = "development"
    log_level: str = "INFO"

    ADMIN_TOKEN: str = "super_secret_admin_token"

    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "itmo_queue"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None

    @property
    def database_url(self) -> URL:
        if self.DATABASE_URL:
            return make_url(self.DATABASE_URL)
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )

    @property
    def redis_url(self) -> RedisURL:
        if self.REDIS_URL:
            return RedisURL(self.REDIS_URL)

        url = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return RedisURL(url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
