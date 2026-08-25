import re
from functools import lru_cache

from pydantic import Field, PositiveFloat, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    telegram_allowed_user_ids: frozenset[PositiveInt] = Field(min_length=1)
    telegram_webhook_secret: SecretStr
    singularity_api_token: SecretStr
    database_url: SecretStr
    webhook_base_url: str = ""
    telegram_update_worker_concurrency: PositiveInt = 4
    telegram_update_worker_poll_interval_seconds: PositiveFloat = 1.0
    telegram_update_worker_claim_timeout_seconds: PositiveFloat = 300.0
    telegram_update_worker_recovery_interval_seconds: PositiveFloat = 30.0
    telegram_update_worker_shutdown_grace_period_seconds: PositiveFloat = 10.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
    )

    @field_validator("telegram_webhook_secret")
    @classmethod
    def validate_telegram_webhook_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret) is None:
            raise ValueError("Telegram webhook secret has an invalid format")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
