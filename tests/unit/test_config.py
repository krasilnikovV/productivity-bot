from collections.abc import Iterator

import pytest
from pydantic import ValidationError

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
        telegram_webhook_secret="test_webhook_secret",
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        _env_file=None,
    )

    assert settings.webhook_base_url == ""


def test_get_settings_loads_values_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-telegram-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "env_webhook_secret")
    monkeypatch.setenv("SINGULARITY_API_TOKEN", "env-singularity-token")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://env-user:env-password@localhost/env-database",
    )
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://test.example.com")

    settings = get_settings()

    assert settings.telegram_bot_token == "env-telegram-token"
    assert settings.telegram_webhook_secret == "env_webhook_secret"
    assert settings.singularity_api_token == "env-singularity-token"
    assert settings.database_url == (
        "postgresql+asyncpg://env-user:env-password@localhost/env-database"
    )
    assert settings.webhook_base_url == "https://test.example.com"


@pytest.mark.parametrize(
    "webhook_secret",
    ["", "a" * 257, "invalid secret", "<replace-with-random-secret>"],
)
def test_telegram_webhook_secret_rejects_invalid_values(
    webhook_secret: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            telegram_bot_token="test-telegram-token",
            telegram_webhook_secret=webhook_secret,
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            _env_file=None,
        )


def test_telegram_webhook_secret_is_required() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "telegram_bot_token": "test-telegram-token",
                "singularity_api_token": "test-singularity-token",
                "database_url": "postgresql+asyncpg://test:test@localhost/test",
            }
        )
