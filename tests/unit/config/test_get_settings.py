from collections.abc import Iterator

import pytest

from productivity_bot.config import get_settings


@pytest.fixture
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_get_settings_loads_values_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    clear_settings_cache: None,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-telegram-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "[123, 456]")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "env_webhook_secret")
    monkeypatch.setenv("SINGULARITY_API_TOKEN", "env-singularity-token")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://env-user:env-password@localhost/env-database",
    )
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://test.example.com")
    monkeypatch.setenv("TELEGRAM_UPDATE_WORKER_CONCURRENCY", "7")
    monkeypatch.setenv("TELEGRAM_UPDATE_WORKER_POLL_INTERVAL_SECONDS", "0.25")
    monkeypatch.setenv("TELEGRAM_UPDATE_WORKER_CLAIM_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("TELEGRAM_UPDATE_WORKER_RECOVERY_INTERVAL_SECONDS", "45")
    monkeypatch.setenv(
        "TELEGRAM_UPDATE_WORKER_SHUTDOWN_GRACE_PERIOD_SECONDS",
        "12.5",
    )

    settings = get_settings()

    assert settings.telegram_bot_token.get_secret_value() == "env-telegram-token"
    assert settings.telegram_allowed_user_ids == frozenset({123, 456})
    assert settings.telegram_webhook_secret.get_secret_value() == "env_webhook_secret"
    assert settings.singularity_api_token.get_secret_value() == "env-singularity-token"
    assert settings.database_url.get_secret_value() == (
        "postgresql+asyncpg://env-user:env-password@localhost/env-database"
    )
    assert settings.webhook_base_url == "https://test.example.com"
    assert settings.telegram_update_worker_concurrency == 7
    assert settings.telegram_update_worker_poll_interval_seconds == 0.25
    assert settings.telegram_update_worker_claim_timeout_seconds == 600.0
    assert settings.telegram_update_worker_recovery_interval_seconds == 45.0
    assert settings.telegram_update_worker_shutdown_grace_period_seconds == 12.5
