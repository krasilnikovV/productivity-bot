import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Update
from fastapi import FastAPI
from fastapi.testclient import TestClient

from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import Settings

WEBHOOK_SECRET = "test_webhook_secret"
WEBHOOK_HEADERS = {
    "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
}


class FakeBotSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    async def close(self) -> None:
        self.events.append("session_close")
        self.closed = True


class FakeBot:
    def __init__(
        self,
        events: list[str],
        *,
        set_webhook_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.session = FakeBotSession(events)
        self.set_webhook_error = set_webhook_error
        self.set_webhook_calls: list[dict[str, object]] = []

    async def set_webhook(self, **kwargs: object) -> bool:
        self.events.append("set_webhook")
        self.set_webhook_calls.append(kwargs)
        if self.set_webhook_error is not None:
            raise self.set_webhook_error
        return True


class FakeDispatcher:
    def __init__(
        self,
        events: list[str],
        *,
        feed_update_result: TelegramMethod | None = None,
        feed_update_release: Event | None = None,
        shutdown_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.workflow_data = {"dependency": "dispatcher-context"}
        self.feed_update_result = feed_update_result
        self.feed_update_release = feed_update_release
        self.shutdown_error = shutdown_error
        self.feed_update_calls: list[tuple[object, Update]] = []
        self.silent_call_requests: list[tuple[object, TelegramMethod]] = []
        self.startup_calls: list[dict[str, object]] = []
        self.shutdown_calls: list[dict[str, object]] = []
        self.allowed_updates = ["message", "callback_query"]

    async def feed_update(
        self,
        bot: object,
        update: Update,
    ) -> TelegramMethod | None:
        self.feed_update_calls.append((bot, update))
        if self.feed_update_release is not None:
            await asyncio.to_thread(self.feed_update_release.wait)
        return self.feed_update_result

    async def silent_call_request(
        self,
        bot: object,
        result: TelegramMethod,
    ) -> None:
        self.silent_call_requests.append((bot, result))

    async def emit_startup(self, **kwargs: object) -> None:
        self.events.append("startup")
        self.startup_calls.append(kwargs)

    async def emit_shutdown(self, **kwargs: object) -> None:
        self.events.append("shutdown")
        self.shutdown_calls.append(kwargs)
        if self.shutdown_error is not None:
            raise self.shutdown_error

    def resolve_used_update_types(self) -> list[str]:
        return self.allowed_updates


def make_settings(*, webhook_base_url: str = "") -> Settings:
    return Settings(
        telegram_bot_token="123456:test-token",
        telegram_webhook_secret=WEBHOOK_SECRET,
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        webhook_base_url=webhook_base_url,
        _env_file=None,
    )


def make_app(
    *,
    webhook_base_url: str = "",
    feed_update_result: TelegramMethod | None = None,
    feed_update_release: Event | None = None,
    set_webhook_error: Exception | None = None,
    shutdown_error: Exception | None = None,
) -> tuple[FastAPI, FakeBot, FakeDispatcher, list[str]]:
    events: list[str] = []
    bot = FakeBot(events, set_webhook_error=set_webhook_error)
    dispatcher = FakeDispatcher(
        events,
        feed_update_result=feed_update_result,
        feed_update_release=feed_update_release,
        shutdown_error=shutdown_error,
    )
    app = create_app(
        make_settings(webhook_base_url=webhook_base_url),
        bot=cast(Bot, bot),
        dispatcher=cast(Dispatcher, dispatcher),
    )
    return app, bot, dispatcher, events


def test_webhook_dispatches_valid_update() -> None:
    app, bot, dispatcher, _ = make_app()

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={"update_id": 42},
        )

    assert response.status_code == 200
    assert response.content == b""
    assert len(dispatcher.feed_update_calls) == 1
    dispatched_bot, dispatched_update = dispatcher.feed_update_calls[0]
    assert dispatched_bot is bot
    assert dispatched_update.update_id == 42


def test_webhook_acknowledges_before_update_processing_finishes() -> None:
    feed_update_release = Event()
    app, _, dispatcher, _ = make_app(feed_update_release=feed_update_release)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        response_future = executor.submit(
            client.post,
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={"update_id": 42},
        )
        try:
            response = response_future.result(timeout=1)
        finally:
            feed_update_release.set()

    assert response.status_code == 200
    assert len(dispatcher.feed_update_calls) == 1


def test_webhook_executes_returned_telegram_method() -> None:
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    app, bot, dispatcher, _ = make_app(feed_update_result=telegram_method)

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={"update_id": 42},
        )

    assert response.status_code == 200
    assert dispatcher.silent_call_requests == [(bot, telegram_method)]


@pytest.mark.parametrize("secret", [None, "incorrect_secret"])
def test_webhook_rejects_missing_or_incorrect_secret(
    secret: str | None,
) -> None:
    app, _, dispatcher, _ = make_app()
    headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=headers,
            json={"update_id": 42},
        )

    assert response.status_code == 401
    assert dispatcher.feed_update_calls == []


def test_webhook_rejects_malformed_update() -> None:
    app, _, dispatcher, _ = make_app()

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={},
        )

    assert response.status_code == 422
    assert dispatcher.feed_update_calls == []


def test_lifespan_registers_webhook_and_closes_resources() -> None:
    app, bot, dispatcher, events = make_app(
        webhook_base_url="https://example.com/",
    )

    with TestClient(app):
        assert events == ["startup", "set_webhook"]

    assert bot.set_webhook_calls == [
        {
            "url": "https://example.com/telegram/webhook",
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": dispatcher.allowed_updates,
        }
    ]
    assert events == ["startup", "set_webhook", "shutdown", "session_close"]
    assert bot.session.closed is True
    assert dispatcher.startup_calls == [
        {
            "bot": bot,
            "app": app,
            "dispatcher": dispatcher,
            "dependency": "dispatcher-context",
        }
    ]
    assert dispatcher.shutdown_calls == dispatcher.startup_calls


def test_lifespan_skips_webhook_registration_without_base_url() -> None:
    app, bot, _, events = make_app()

    with TestClient(app):
        assert events == ["startup"]

    assert bot.set_webhook_calls == []
    assert events == ["startup", "shutdown", "session_close"]


def test_set_webhook_error_fails_startup_and_closes_resources() -> None:
    app, bot, _, events = make_app(
        webhook_base_url="https://example.com",
        set_webhook_error=RuntimeError("set webhook failed"),
    )

    with (
        pytest.raises(
            RuntimeError,
            match="set webhook failed",
        ),
        TestClient(app),
    ):
        pytest.fail("TestClient entered a failed application lifespan")

    assert events == ["startup", "set_webhook", "shutdown", "session_close"]
    assert bot.session.closed is True


def test_session_closes_when_dispatcher_shutdown_fails() -> None:
    app, bot, _, events = make_app(
        shutdown_error=RuntimeError("shutdown failed"),
    )

    with pytest.raises(RuntimeError, match="shutdown failed"), TestClient(app):
        pass

    assert events == ["startup", "shutdown", "session_close"]
    assert bot.session.closed is True
