from collections.abc import Callable

import pytest

from productivity_bot.config import Settings


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def factory(
        *,
        webhook_base_url: str = "",
        telegram_update_worker_poll_interval_seconds: float = 1.0,
    ) -> Settings:
        return Settings(
            telegram_bot_token="123456:test-token",
            telegram_allowed_user_ids=frozenset({123}),
            telegram_webhook_secret="test_webhook_secret",
            singularity_api_token="test-singularity-token",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            webhook_base_url=webhook_base_url,
            telegram_update_worker_poll_interval_seconds=(
                telegram_update_worker_poll_interval_seconds
            ),
            _env_file=None,
        )

    return factory
