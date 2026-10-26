from itmo_queue.core.config import Settings


def test_settings_load_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Queue test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("POSTGRES_HOST", "database")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:/%word")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/2")
    settings = Settings(_env_file=None)
    assert settings.app_name == "Queue test"
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url.host == "database"
    assert settings.database_url.password == "p@ss:/%word"
    assert settings.redis_url.host == "cache"
    assert settings.redis_url.path == "/2"
