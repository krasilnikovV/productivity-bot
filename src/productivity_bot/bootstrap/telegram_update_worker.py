import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from productivity_bot.adapters.postgres import PostgresTelegramUpdateInboxRepository
from productivity_bot.adapters.singularity import SingularityAdapter, SingularityClient
from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.application.use_cases import CaptureTask
from productivity_bot.config import Settings
from productivity_bot.entrypoints.telegram.handlers import CaptureTaskHandler
from productivity_bot.entrypoints.telegram.update_worker import TelegramUpdateWorker

logger = logging.getLogger(__name__)


async def run_telegram_update_worker(
    settings: Settings,
    shutdown_event: asyncio.Event,
    *,
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
    singularity_client: SingularityClient | None = None,
    telegram_update_inbox_repository: TelegramUpdateInboxRepository | None = None,
    worker: TelegramUpdateWorker | None = None,
) -> None:
    """Build, run, and close the resources owned by the worker process."""

    application_bot = (
        bot
        if bot is not None
        else Bot(settings.telegram_bot_token.get_secret_value())
    )
    application_dispatcher = dispatcher if dispatcher is not None else Dispatcher()
    application_singularity_client = (
        singularity_client
        if singularity_client is not None
        else SingularityClient(settings.singularity_api_token.get_secret_value())
    )
    application_database_engine: AsyncEngine | None = None
    application_telegram_update_inbox_repository: TelegramUpdateInboxRepository
    if telegram_update_inbox_repository is None:
        application_database_engine = create_async_engine(
            settings.database_url.get_secret_value()
        )
        session_factory = async_sessionmaker(
            application_database_engine,
            expire_on_commit=False,
        )
        application_telegram_update_inbox_repository = (
            PostgresTelegramUpdateInboxRepository(session_factory)
        )
    else:
        application_telegram_update_inbox_repository = (
            telegram_update_inbox_repository
        )

    singularity_adapter = SingularityAdapter(application_singularity_client)
    capture_task_handler = CaptureTaskHandler(
        CaptureTask(singularity_adapter),
        allowed_user_ids=settings.telegram_allowed_user_ids,
    )
    application_dispatcher.include_router(capture_task_handler.router)
    application_worker = (
        worker
        if worker is not None
        else TelegramUpdateWorker(
            bot=application_bot,
            dispatcher=application_dispatcher,
            update_inbox_repository=application_telegram_update_inbox_repository,
            concurrency=settings.telegram_update_worker_concurrency,
            poll_interval=settings.telegram_update_worker_poll_interval_seconds,
            claim_timeout=timedelta(
                seconds=settings.telegram_update_worker_claim_timeout_seconds
            ),
            recovery_interval=(
                settings.telegram_update_worker_recovery_interval_seconds
            ),
            shutdown_grace_period=(
                settings.telegram_update_worker_shutdown_grace_period_seconds
            ),
        )
    )

    workflow_data = {
        "dispatcher": application_dispatcher,
        **application_dispatcher.workflow_data,
    }
    workflow_data.pop("bot", None)
    dispatcher_start_attempted = False
    worker_start_attempted = False
    try:
        dispatcher_start_attempted = True
        await application_dispatcher.emit_startup(
            bot=application_bot,
            **workflow_data,
        )
        worker_start_attempted = True
        await application_worker.start()
        logger.info("Telegram update worker started")
        await _wait_for_shutdown_or_worker_failure(
            shutdown_event,
            application_worker,
        )
    finally:
        try:
            if worker_start_attempted:
                await application_worker.stop()
        finally:
            try:
                if dispatcher_start_attempted:
                    await application_dispatcher.emit_shutdown(
                        bot=application_bot,
                        **workflow_data,
                    )
            finally:
                try:
                    # noinspection unresolved-references
                    await application_bot.session.close()
                finally:
                    try:
                        await application_singularity_client.aclose()
                    finally:
                        if application_database_engine is not None:
                            await application_database_engine.dispose()
        logger.info("Telegram update worker stopped")


async def _wait_for_shutdown_or_worker_failure(
    shutdown_event: asyncio.Event,
    worker: TelegramUpdateWorker,
) -> None:
    shutdown_task = asyncio.create_task(
        shutdown_event.wait(),
        name="telegram-update-worker-shutdown-signal",
    )
    worker_task = asyncio.create_task(
        worker.wait(),
        name="telegram-update-worker-failure-watch",
    )
    tasks = (shutdown_task, worker_task)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if worker_task in done:
            await worker_task
            raise RuntimeError("Telegram update worker stopped unexpectedly")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
