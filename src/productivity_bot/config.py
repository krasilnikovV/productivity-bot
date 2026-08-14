from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_webhook_secret: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    singularity_api_token: str
    database_url: str
    webhook_base_url: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
