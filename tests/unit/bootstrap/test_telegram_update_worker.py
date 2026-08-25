import asyncio
from typing import cast

import pytest
from aiogram import Bot, Dispatcher, Router

from productivity_bot.adapters.singularity import SingularityClient
from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.bootstrap.telegram_update_worker import (
    run_telegram_update_worker,
)
from productivity_bot.config import Settings
from productivity_bot.entrypoints.telegram.update_worker import TelegramUpdateWorker


class FakeBotSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def close(self) -> None:
        self.events.append("bot_close")


class FakeBot:
    def __init__(self, events: list[str]) -> None:
        self.session = FakeBotSession(events)


class FakeDispatcher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.workflow_data = {"dependency": "value"}
        self.included_routers: list[Router] = []

    def include_router(self, router: Router) -> Router:
        self.included_routers.append(router)
        return router

    async def emit_startup(self, **kwargs: object) -> None:
        self.events.append("dispatcher_startup")

    async def emit_shutdown(self, **kwargs: object) -> None:
        self.events.append("dispatcher_shutdown")


class FakeSingularityClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def aclose(self) -> None:
        self.events.append("singularity_close")


class FakeWorker:
    def __init__(
        self,
        events: list[str],
        *,
        start_error: Exception | None = None,
        wait_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.start_error = start_error
        self.wait_error = wait_error
        self.release = asyncio.Event()

    async def start(self) -> None:
        self.events.append("worker_start")
        if self.start_error is not None:
            raise self.start_error

    async def wait(self) -> None:
        if self.wait_error is not None:
            raise self.wait_error
        await self.release.wait()

    async def stop(self) -> None:
        self.events.append("worker_stop")
        self.release.set()


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="123456:test-token",
        telegram_allowed_user_ids=frozenset({123}),
        telegram_webhook_secret="test_webhook_secret",
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        _env_file=None,
    )


async def run_with_fakes(
    events: list[str],
    shutdown_event: asyncio.Event,
    worker: FakeWorker,
) -> None:
    await run_telegram_update_worker(
        make_settings(),
        shutdown_event,
        bot=cast(Bot, FakeBot(events)),
        dispatcher=cast(Dispatcher, FakeDispatcher(events)),
        singularity_client=cast(SingularityClient, FakeSingularityClient(events)),
        telegram_update_inbox_repository=cast(
            TelegramUpdateInboxRepository,
            object(),
        ),
        worker=cast(TelegramUpdateWorker, worker),
    )


@pytest.mark.asyncio
async def test_worker_runtime_starts_and_closes_resources_in_order() -> None:
    events: list[str] = []
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    await run_with_fakes(events, shutdown_event, FakeWorker(events))

    assert events == [
        "dispatcher_startup",
        "worker_start",
        "worker_stop",
        "dispatcher_shutdown",
        "bot_close",
        "singularity_close",
    ]


@pytest.mark.asyncio
async def test_initial_recovery_failure_closes_resources() -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="initial recovery failed"):
        await run_with_fakes(
            events,
            asyncio.Event(),
            FakeWorker(
                events,
                start_error=RuntimeError("initial recovery failed"),
            ),
        )

    assert events == [
        "dispatcher_startup",
        "worker_start",
        "worker_stop",
        "dispatcher_shutdown",
        "bot_close",
        "singularity_close",
    ]


@pytest.mark.asyncio
async def test_fatal_worker_failure_propagates_after_cleanup() -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="processing loop failed"):
        await run_with_fakes(
            events,
            asyncio.Event(),
            FakeWorker(
                events,
                wait_error=RuntimeError("processing loop failed"),
            ),
        )

    assert events[-4:] == [
        "worker_stop",
        "dispatcher_shutdown",
        "bot_close",
        "singularity_close",
    ]
