import pytest
from pydantic import ValidationError

from productivity_bot.config import Settings


def valid_settings_values(**overrides: object) -> dict[str, object]:
    values = {
        "telegram_bot_token": "test-telegram-token",
        "telegram_allowed_user_ids": frozenset({123}),
        "telegram_webhook_secret": "test_webhook_secret",
        "singularity_api_token": "test-singularity-token",
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
    }
    values.update(overrides)
    return values


def test_webhook_base_url_defaults_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEBHOOK_BASE_URL", raising=False)

    settings = Settings(**valid_settings_values(), _env_file=None)

    assert settings.webhook_base_url == ""
    assert settings.telegram_update_worker_concurrency == 4
    assert settings.telegram_update_worker_poll_interval_seconds == 1.0
    assert settings.telegram_update_worker_claim_timeout_seconds == 300.0
    assert settings.telegram_update_worker_recovery_interval_seconds == 30.0
    assert settings.telegram_update_worker_shutdown_grace_period_seconds == 10.0


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


def test_settings_validation_error_hides_invalid_secret() -> None:
    invalid_secret = "invalid secret value"

    with pytest.raises(ValidationError) as error_info:
        Settings(
            **valid_settings_values(telegram_webhook_secret=invalid_secret),
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
            **valid_settings_values(telegram_webhook_secret=webhook_secret),
            _env_file=None,
        )


def test_telegram_webhook_secret_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    values = valid_settings_values()
    del values["telegram_webhook_secret"]

    with pytest.raises(ValidationError):
        Settings(**values, _env_file=None)


def test_telegram_allowed_user_ids_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    values = valid_settings_values()
    del values["telegram_allowed_user_ids"]

    with pytest.raises(ValidationError):
        Settings(**values, _env_file=None)


@pytest.mark.parametrize(
    "allowed_user_ids",
    [frozenset(), frozenset({0})],
)
def test_telegram_allowed_user_ids_rejects_invalid_values(
    allowed_user_ids: frozenset[int],
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            **valid_settings_values(telegram_allowed_user_ids=allowed_user_ids),
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
@pytest.mark.parametrize("invalid_value", [0])
def test_worker_settings_reject_non_positive_values(
    field_name: str,
    invalid_value: int,
) -> None:
    values = valid_settings_values()
    values[field_name] = invalid_value

    with pytest.raises(ValidationError):
        Settings(**values, _env_file=None)
