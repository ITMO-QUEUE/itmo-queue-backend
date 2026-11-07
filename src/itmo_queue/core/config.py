from functools import lru_cache
from urllib.parse import urlparse

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    name: str = "itmo_queue"

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ITMO.Queue"
    environment: str = "development"
    log_level: str = "INFO"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "itmo_queue"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None

    @property
    def database(self) -> DBConfig:
        if self.DATABASE_URL:
            parsed = urlparse(self.DATABASE_URL)
            return DBConfig(
                host=parsed.hostname or "localhost",
                port=parsed.port or 5432,
                user=parsed.username or "postgres",
                password=parsed.password or "postgres",
                name=parsed.path.lstrip("/") if parsed.path else "itmo_queue",
            )
        return DBConfig(
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            user=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            name=self.POSTGRES_DB,
        )

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return self.database.url

    @property
    def redis(self) -> RedisConfig:
        if self.REDIS_URL:
            parsed = urlparse(self.REDIS_URL)
            return RedisConfig(
                host=parsed.hostname or "localhost",
                port=parsed.port or 6379,
                db=int(parsed.path.lstrip("/")) if parsed.path and parsed.path != "/" else 0,
            )
        return RedisConfig(host=self.REDIS_HOST, port=self.REDIS_PORT, db=self.REDIS_DB)

    @property
    def redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        return self.redis.url


@lru_cache
def get_settings() -> Settings:
    return Settings()
