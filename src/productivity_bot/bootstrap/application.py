from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from productivity_bot.adapters.postgres import PostgresTelegramUpdateInboxRepository
from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.config import Settings
from productivity_bot.entrypoints.http.routers.health import router as health_router
from productivity_bot.entrypoints.telegram.webhook import (
    TELEGRAM_ALLOWED_UPDATE_TYPES,
    TELEGRAM_WEBHOOK_PATH,
    TelegramWebhookHandler,
)


def create_app(
    settings: Settings,
    *,
    bot: Bot | None = None,
    telegram_update_inbox_repository: TelegramUpdateInboxRepository | None = None,
) -> FastAPI:
    application_bot = (
        bot
        if bot is not None
        else Bot(settings.telegram_bot_token.get_secret_value())
    )
    application_database_engine: AsyncEngine | None = None
    application_telegram_update_inbox_repository: TelegramUpdateInboxRepository
    if telegram_update_inbox_repository is None:
        application_database_engine = create_async_engine(
            settings.database_url.get_secret_value(),
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
    telegram_webhook_secret = settings.telegram_webhook_secret.get_secret_value()
    telegram_webhook = TelegramWebhookHandler(
        bot=application_bot,
        webhook_secret=telegram_webhook_secret,
        update_inbox_repository=application_telegram_update_inbox_repository,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if settings.webhook_base_url:
                await application_bot.set_webhook(
                    url=(settings.webhook_base_url.rstrip("/") + TELEGRAM_WEBHOOK_PATH),
                    secret_token=telegram_webhook_secret,
                    allowed_updates=list(TELEGRAM_ALLOWED_UPDATE_TYPES),
                )
            yield
        finally:
            try:
                # noinspection unresolved-references
                await application_bot.session.close()
            finally:
                if application_database_engine is not None:
                    await application_database_engine.dispose()

    application = FastAPI(title="Productivity Bot", lifespan=lifespan)

    # Include FastAPI Routers
    application.include_router(health_router)
    application.include_router(telegram_webhook.router)

    return application
