import asyncio

import pytest

from productivity_bot import telegram_update_worker as process_module
from productivity_bot.config import Settings


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="123456:test-token",
        telegram_allowed_user_ids=frozenset({123}),
        telegram_webhook_secret="test_webhook_secret",
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_process_runner_uses_injected_shutdown_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    shutdown_event = asyncio.Event()
    calls: list[tuple[Settings, asyncio.Event]] = []

    async def fake_run_worker(
        actual_settings: Settings,
        actual_shutdown_event: asyncio.Event,
    ) -> None:
        calls.append((actual_settings, actual_shutdown_event))

    monkeypatch.setattr(
        process_module,
        "run_telegram_update_worker",
        fake_run_worker,
    )

    await process_module.run_worker_process(
        settings=settings,
        shutdown_event=shutdown_event,
    )

    assert calls == [(settings, shutdown_event)]
