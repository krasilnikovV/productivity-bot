import asyncio
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import Mock, create_autospec

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from sqlalchemy.ext.asyncio import AsyncEngine

from productivity_bot.adapters.singularity import SingularityClient
from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.bootstrap import telegram_update_worker as worker_module
from productivity_bot.bootstrap.telegram_update_worker import (
    run_telegram_update_worker,
)
from productivity_bot.config import Settings
from productivity_bot.entrypoints.telegram.update_worker import TelegramUpdateWorker


async def run_with_mocks(
    events: list[str],
    settings: Settings,
    shutdown_event: asyncio.Event,
    *,
    start_error: Exception | None = None,
    wait_error: Exception | None = None,
) -> Mock:
    bot = create_autospec(Bot, instance=True)
    bot_session = create_autospec(BaseSession, instance=True, spec_set=True)
    bot.session = bot_session
    bot_session.close.side_effect = lambda: events.append("bot_close")

    dispatcher = create_autospec(Dispatcher, instance=True)
    dispatcher.workflow_data = {"dependency": "value"}
    dispatcher.emit_startup.side_effect = lambda **_: events.append(
        "dispatcher_startup"
    )
    dispatcher.emit_shutdown.side_effect = lambda **_: events.append(
        "dispatcher_shutdown"
    )

    singularity_client = create_autospec(
        SingularityClient,
        instance=True,
        spec_set=True,
    )
    singularity_client.aclose.side_effect = lambda: events.append("singularity_close")
    repository = create_autospec(
        TelegramUpdateInboxRepository,
        instance=True,
        spec_set=True,
    )
    worker = create_autospec(
        TelegramUpdateWorker,
        instance=True,
        spec_set=True,
    )
    release = asyncio.Event()

    def start() -> None:
        events.append("worker_start")
        if start_error is not None:
            raise start_error

    async def wait() -> None:
        if wait_error is not None:
            raise wait_error
        await release.wait()

    def stop() -> None:
        events.append("worker_stop")
        release.set()

    worker.start.side_effect = start
    worker.wait.side_effect = wait
    worker.stop.side_effect = stop

    await run_telegram_update_worker(
        settings,
        shutdown_event,
        bot=bot,
        dispatcher=dispatcher,
        singularity_client=singularity_client,
        telegram_update_inbox_repository=repository,
        worker=worker,
    )
    return dispatcher


@pytest.mark.asyncio
async def test_worker_runtime_starts_and_closes_resources_in_order(
    make_settings: Callable[..., Settings],
) -> None:
    events: list[str] = []
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    dispatcher = await run_with_mocks(events, make_settings(), shutdown_event)

    assert events == [
        "dispatcher_startup",
        "worker_start",
        "worker_stop",
        "dispatcher_shutdown",
        "bot_close",
        "singularity_close",
    ]
    assert dispatcher.include_router.call_count == 2


@pytest.mark.asyncio
async def test_initial_recovery_failure_closes_resources(
    make_settings: Callable[..., Settings],
) -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="initial recovery failed"):
        await run_with_mocks(
            events,
            make_settings(),
            asyncio.Event(),
            start_error=RuntimeError("initial recovery failed"),
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
@pytest.mark.parametrize(
    "start_error",
    [None, RuntimeError("initial recovery failed")],
    ids=["shutdown", "startup-failure"],
)
async def test_worker_runtime_builds_and_closes_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
    start_error: Exception | None,
) -> None:
    settings = make_settings(telegram_update_worker_poll_interval_seconds=0.25)
    shutdown_event = asyncio.Event()
    if start_error is None:
        shutdown_event.set()

    bot = create_autospec(Bot, instance=True)
    bot_session = create_autospec(BaseSession, instance=True, spec_set=True)
    bot.session = bot_session
    dispatcher = create_autospec(Dispatcher, instance=True)
    dispatcher.workflow_data = {}
    singularity_client = create_autospec(
        SingularityClient,
        instance=True,
        spec_set=True,
    )
    database_engine = create_autospec(AsyncEngine, instance=True, spec_set=True)
    session_factory = Mock()
    repository = create_autospec(
        TelegramUpdateInboxRepository,
        instance=True,
        spec_set=True,
    )
    worker = create_autospec(
        TelegramUpdateWorker,
        instance=True,
        spec_set=True,
    )
    worker.start.side_effect = start_error
    worker.wait.side_effect = asyncio.Event().wait

    bot_constructor = Mock(return_value=bot)
    dispatcher_constructor = Mock(return_value=dispatcher)
    singularity_client_constructor = Mock(return_value=singularity_client)
    database_engine_constructor = Mock(return_value=database_engine)
    session_factory_constructor = Mock(return_value=session_factory)
    repository_constructor = Mock(return_value=repository)
    worker_constructor = Mock(return_value=worker)
    monkeypatch.setattr(worker_module, "Bot", bot_constructor)
    monkeypatch.setattr(worker_module, "Dispatcher", dispatcher_constructor)
    monkeypatch.setattr(
        worker_module,
        "SingularityClient",
        singularity_client_constructor,
    )
    monkeypatch.setattr(
        worker_module,
        "create_async_engine",
        database_engine_constructor,
    )
    monkeypatch.setattr(
        worker_module,
        "async_sessionmaker",
        session_factory_constructor,
    )
    monkeypatch.setattr(
        worker_module,
        "PostgresTelegramUpdateInboxRepository",
        repository_constructor,
    )
    monkeypatch.setattr(worker_module, "TelegramUpdateWorker", worker_constructor)

    if start_error is None:
        await run_telegram_update_worker(settings, shutdown_event)
    else:
        with pytest.raises(RuntimeError, match="initial recovery failed"):
            await run_telegram_update_worker(settings, shutdown_event)

    bot_constructor.assert_called_once_with("123456:test-token")
    dispatcher_constructor.assert_called_once_with()
    singularity_client_constructor.assert_called_once_with("test-singularity-token")
    database_engine_constructor.assert_called_once_with(
        "postgresql+asyncpg://test:test@localhost/test"
    )
    session_factory_constructor.assert_called_once_with(
        database_engine,
        expire_on_commit=False,
    )
    repository_constructor.assert_called_once_with(session_factory)
    worker_constructor.assert_called_once_with(
        bot=bot,
        dispatcher=dispatcher,
        update_inbox_repository=repository,
        concurrency=settings.telegram_update_worker_concurrency,
        poll_interval=0.25,
        claim_timeout=timedelta(
            seconds=settings.telegram_update_worker_claim_timeout_seconds
        ),
        recovery_interval=settings.telegram_update_worker_recovery_interval_seconds,
        shutdown_grace_period=(
            settings.telegram_update_worker_shutdown_grace_period_seconds
        ),
    )
    assert dispatcher.include_router.call_count == 2
    worker.start.assert_awaited_once_with()
    worker.stop.assert_awaited_once_with()
    dispatcher.emit_shutdown.assert_awaited_once()
    bot_session.close.assert_awaited_once_with()
    singularity_client.aclose.assert_awaited_once_with()
    database_engine.dispose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fatal_worker_failure_propagates_after_cleanup(
    make_settings: Callable[..., Settings],
) -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="processing loop failed"):
        await run_with_mocks(
            events,
            make_settings(),
            asyncio.Event(),
            wait_error=RuntimeError("processing loop failed"),
        )

    assert events[-4:] == [
        "worker_stop",
        "dispatcher_shutdown",
        "bot_close",
        "singularity_close",
    ]
