from collections.abc import Iterator

import pytest

from productivity_bot.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_webhook_base_url_defaults_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEBHOOK_BASE_URL", raising=False)

    settings = Settings(
        telegram_bot_token="test-telegram-token",
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        _env_file=None,
    )

    assert settings.webhook_base_url == ""


def test_get_settings_loads_values_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-telegram-token")
    monkeypatch.setenv("SINGULARITY_API_TOKEN", "env-singularity-token")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://env-user:env-password@localhost/env-database",
    )
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://test.example.com")

    settings = get_settings()

    assert settings.telegram_bot_token == "env-telegram-token"
    assert settings.singularity_api_token == "env-singularity-token"
    assert settings.database_url == (
        "postgresql+asyncpg://env-user:env-password@localhost/env-database"
    )
    assert settings.webhook_base_url == "https://test.example.com"
