import asyncio
from collections.abc import Callable
from threading import Event
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, create_autospec

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from fastapi import FastAPI

from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import Settings

WEBHOOK_SECRET = "test_webhook_secret"
WEBHOOK_HEADERS = {
    "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
}
DEADLOCK_TIMEOUT = 5
AppFactory = Callable[..., tuple[FastAPI, AsyncMock, Mock, list[str]]]


def make_bot(
    events: list[str],
    *,
    set_webhook_error: Exception | None = None,
) -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.id = 123456
    bot_session = create_autospec(
        BaseSession,
        instance=True,
        spec_set=True,
    )
    bot.session = bot_session

    def set_webhook(**_: object) -> bool:
        events.append("set_webhook")
        if set_webhook_error is not None:
            raise set_webhook_error
        return True

    bot.set_webhook.side_effect = set_webhook
    bot_session.close.side_effect = lambda: events.append("session_close")
    return bot


def make_app_factory(
    make_settings: Callable[..., Settings],
) -> AppFactory:
    def factory(
        *,
        webhook_base_url: str = "",
        insert_started: Event | None = None,
        insert_release: Event | None = None,
        insert_error: Exception | None = None,
        set_webhook_error: Exception | None = None,
    ) -> tuple[FastAPI, AsyncMock, Mock, list[str]]:
        events: list[str] = []
        bot = make_bot(events, set_webhook_error=set_webhook_error)

        repository = create_autospec(
            TelegramUpdateInboxRepository,
            instance=True,
            spec_set=True,
        )
        repository.insert_update.return_value = True
        if insert_release is not None or insert_error is not None:

            async def insert_update(_: int, __: dict[str, Any]) -> bool:
                if insert_started is not None:
                    insert_started.set()
                if insert_release is not None:
                    await asyncio.to_thread(insert_release.wait)
                if insert_error is not None:
                    raise insert_error
                return True

            repository.insert_update.side_effect = insert_update
        app = create_app(
            make_settings(webhook_base_url=webhook_base_url),
            bot=cast(Bot, bot),
            telegram_update_inbox_repository=cast(
                TelegramUpdateInboxRepository,
                repository,
            ),
        )
        return app, bot, repository, events

    return factory
