from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Эта настройка говорит Pydantic читать переменные из файла .env (если он есть)
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Общие настройки приложения
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Настройки PostgreSQL
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "itmo_queue"

    # Возможность задать полный URL напрямую (удобно для CI)
    DATABASE_URL: Optional[str] = None

    # Настройки Redis (если они уже были у вас в проекте - оставьте как было)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_URL: Optional[str] = None

    @property
    def database_url(self) -> str:
        """
        Возвращает строку подключения к базе.
        Если передана переменная DATABASE_URL (например, в GitHub Actions), используется она.
        Иначе собирает строку из отдельных частей.
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL

        # Обрати внимание на драйвер +asyncpg
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def redis_url(self) -> str:
        """
        Аналогично для Redis.
        """
        if self.REDIS_URL:
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    """
    Возвращает объект настроек.
    Благодаря @lru_cache файл .env читается только один раз при старте.
    """
    return Settings()