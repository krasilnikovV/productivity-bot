from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from fastapi import FastAPI

from productivity_bot.config import Settings
from productivity_bot.entrypoints.http.routers.health import router as health_router
from productivity_bot.entrypoints.telegram.webhook import (
    TELEGRAM_WEBHOOK_PATH,
    TelegramWebhookHandler,
)


def create_app(
    settings: Settings,
    *,
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
) -> FastAPI:
    application_bot = bot if bot is not None else Bot(settings.telegram_bot_token)
    application_dispatcher = dispatcher if dispatcher is not None else Dispatcher()
    telegram_webhook = TelegramWebhookHandler(
        bot=application_bot,
        dispatcher=application_dispatcher,
        webhook_secret=settings.telegram_webhook_secret,
    )

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        workflow_data = {
            "app": fastapi_app,
            "dispatcher": application_dispatcher,
            **application_dispatcher.workflow_data,
        }
        workflow_data.pop("bot", None)
        try:
            await application_dispatcher.emit_startup(
                bot=application_bot,
                **workflow_data,
            )
            if settings.webhook_base_url:
                await application_bot.set_webhook(
                    url=(settings.webhook_base_url.rstrip("/") + TELEGRAM_WEBHOOK_PATH),
                    secret_token=settings.telegram_webhook_secret,
                    allowed_updates=application_dispatcher.resolve_used_update_types(),
                )
            yield
        finally:
            try:
                await telegram_webhook.wait_closed()
            finally:
                try:
                    await application_dispatcher.emit_shutdown(
                        bot=application_bot,
                        **workflow_data,
                    )
                finally:
                    # noinspection unresolved-references
                    await application_bot.session.close()

    application = FastAPI(title="Productivity Bot", lifespan=lifespan)

    # Include FastAPI Routers
    application.include_router(health_router)
    application.include_router(telegram_webhook.router)

    return application
