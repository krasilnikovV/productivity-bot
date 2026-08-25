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
        telegram_allowed_user_ids=frozenset({123}),
        telegram_webhook_secret="test_webhook_secret",
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        _env_file=None,
    )

    assert settings.webhook_base_url == ""
    assert settings.telegram_update_worker_concurrency == 4
    assert settings.telegram_update_worker_poll_interval_seconds == 1.0
    assert settings.telegram_update_worker_claim_timeout_seconds == 300.0
    assert settings.telegram_update_worker_recovery_interval_seconds == 30.0
    assert settings.telegram_update_worker_shutdown_grace_period_seconds == 10.0


def test_get_settings_loads_values_from_environment(
    monkeypatch: pytest.MonkeyPatch,
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


def test_settings_repr_masks_secrets() -> None:
    secrets = {
        "telegram-token-value",
        "telegram_webhook_secret_value",
        "singularity-token-value",
        "postgresql+asyncpg://user:password@localhost/database",
    }
    settings = Settings(
        telegram_bot_token="telegram-token-value",
        telegram_allowed_user_ids=frozenset({123}),
        telegram_webhook_secret="telegram_webhook_secret_value",
        singularity_api_token="singularity-token-value",
        database_url="postgresql+asyncpg://user:password@localhost/database",
        _env_file=None,
    )

    settings_repr = repr(settings)

    assert all(secret not in settings_repr for secret in secrets)
    assert settings_repr.count("**********") == len(secrets)


def test_settings_validation_error_hides_invalid_secret() -> None:
    invalid_secret = "invalid secret value"

    with pytest.raises(ValidationError) as error_info:
        Settings(
            telegram_bot_token="test-telegram-token",
            telegram_allowed_user_ids=frozenset({123}),
            telegram_webhook_secret=invalid_secret,
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            _env_file=None,
        )

    assert invalid_secret not in str(error_info.value)


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
            telegram_allowed_user_ids=frozenset({123}),
            telegram_webhook_secret=webhook_secret,
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            _env_file=None,
        )


def test_telegram_webhook_secret_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    with pytest.raises(ValidationError):
        Settings(
            telegram_bot_token="test-telegram-token",
            telegram_allowed_user_ids=frozenset({123}),
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            _env_file=None,
        )


def test_telegram_allowed_user_ids_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    with pytest.raises(ValidationError):
        Settings(
            telegram_bot_token="test-telegram-token",
            telegram_webhook_secret="test_webhook_secret",
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "allowed_user_ids",
    [frozenset(), frozenset({0}), frozenset({-1})],
)
def test_telegram_allowed_user_ids_rejects_invalid_values(
    allowed_user_ids: frozenset[int],
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            telegram_bot_token="test-telegram-token",
            telegram_allowed_user_ids=allowed_user_ids,
            telegram_webhook_secret="test_webhook_secret",
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "telegram_update_worker_concurrency",
        "telegram_update_worker_poll_interval_seconds",
        "telegram_update_worker_claim_timeout_seconds",
        "telegram_update_worker_recovery_interval_seconds",
        "telegram_update_worker_shutdown_grace_period_seconds",
    ],
)
@pytest.mark.parametrize("invalid_value", [0, -1])
def test_worker_settings_reject_non_positive_values(
    field_name: str,
    invalid_value: int,
) -> None:
    values = {
        "telegram_bot_token": "test-telegram-token",
        "telegram_allowed_user_ids": frozenset({123}),
        "telegram_webhook_secret": "test_webhook_secret",
        "singularity_api_token": "test-singularity-token",
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
        field_name: invalid_value,
    }

    with pytest.raises(ValidationError):
        Settings(**values, _env_file=None)
