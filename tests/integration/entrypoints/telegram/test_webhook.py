import asyncio
import json
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any, cast

import httpx2
import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Update
from fastapi import FastAPI
from fastapi.testclient import TestClient

from productivity_bot.adapters.singularity import SingularityClient
from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import Settings

WEBHOOK_SECRET = "test_webhook_secret"
WEBHOOK_HEADERS = {
    "X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET,
}


class FakeBotSession:
    def __init__(
        self,
        events: list[str],
        *,
        close_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.close_error = close_error
        self.closed = False

    async def close(self) -> None:
        self.events.append("session_close")
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeBot:
    def __init__(
        self,
        events: list[str],
        *,
        set_webhook_error: Exception | None = None,
        session_close_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.id = 123456
        self.session = FakeBotSession(events, close_error=session_close_error)
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
        self._dispatcher = Dispatcher()
        self.workflow_data = {"dependency": "dispatcher-context"}
        self.feed_update_result = feed_update_result
        self.feed_update_release = feed_update_release
        self.shutdown_error = shutdown_error
        self.feed_update_calls: list[tuple[object, Update]] = []
        self.silent_call_requests: list[tuple[object, TelegramMethod]] = []
        self.startup_calls: list[dict[str, object]] = []
        self.shutdown_calls: list[dict[str, object]] = []
        self.included_routers: list[Router] = []
        self.allowed_updates: list[str] = []

    def include_router(self, router: Router) -> Router:
        self.included_routers.append(router)
        return self._dispatcher.include_router(router)

    async def feed_update(
        self,
        bot: object,
        update: Update,
    ) -> Any:
        self.feed_update_calls.append((bot, update))
        if self.feed_update_release is not None:
            await asyncio.to_thread(self.feed_update_release.wait)
        if self.feed_update_result is not None:
            return self.feed_update_result
        if update.message is None:
            return None
        return await self._dispatcher.feed_update(cast(Bot, bot), update)

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
        self.allowed_updates = self._dispatcher.resolve_used_update_types()
        return self.allowed_updates


class FakeSingularityClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    async def aclose(self) -> None:
        self.events.append("singularity_close")
        self.closed = True


class TrackingMockTransport(httpx2.MockTransport):
    def __init__(
        self,
        handler: Callable[[httpx2.Request], Awaitable[httpx2.Response]],
    ) -> None:
        super().__init__(handler)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


def make_settings(*, webhook_base_url: str = "") -> Settings:
    return Settings(
        telegram_bot_token="123456:test-token",
        telegram_allowed_user_ids=frozenset({123}),
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
    session_close_error: Exception | None = None,
    singularity_client: SingularityClient | None = None,
) -> tuple[FastAPI, FakeBot, FakeDispatcher, list[str]]:
    events: list[str] = []
    bot = FakeBot(
        events,
        set_webhook_error=set_webhook_error,
        session_close_error=session_close_error,
    )
    dispatcher = FakeDispatcher(
        events,
        feed_update_result=feed_update_result,
        feed_update_release=feed_update_release,
        shutdown_error=shutdown_error,
    )
    application_singularity_client = (
        singularity_client
        if singularity_client is not None
        else cast(SingularityClient, FakeSingularityClient(events))
    )
    # noinspection invalid-cast
    app = create_app(
        make_settings(webhook_base_url=webhook_base_url),
        bot=cast(Bot, bot),
        dispatcher=cast(Dispatcher, dispatcher),
        singularity_client=application_singularity_client,
    )
    return app, bot, dispatcher, events


@pytest.mark.filterwarnings(
    "error::pydantic.warnings.UnsupportedFieldAttributeWarning",
)
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
    assert dispatched_update.bot is bot


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


def test_webhook_captures_text_message_in_singularity_and_confirms() -> None:
    singularity_requests: list[httpx2.Request] = []

    async def singularity_handler(request: httpx2.Request) -> httpx2.Response:
        singularity_requests.append(request)
        return httpx2.Response(
            201,
            json={"id": "T-123", "title": "Buy groceries"},
        )

    transport = TrackingMockTransport(singularity_handler)
    singularity_client = SingularityClient(
        "injected-token",
        transport=transport,
    )
    app, bot, dispatcher, _ = make_app(singularity_client=singularity_client)

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={
                "update_id": 42,
                "message": {
                    "message_id": 7,
                    "date": 1_754_000_000,
                    "from": {
                        "id": 123,
                        "is_bot": False,
                        "first_name": "Test user",
                    },
                    "chat": {"id": 123, "type": "private"},
                    "text": "Buy groceries",
                },
            },
        )

    assert response.status_code == 200
    assert len(singularity_requests) == 1
    singularity_request = singularity_requests[0]
    assert singularity_request.method == "POST"
    assert singularity_request.url == "https://api.singularity-app.com/v2/task"
    assert json.loads(singularity_request.content) == {"title": "Buy groceries"}
    assert len(dispatcher.silent_call_requests) == 1
    sent_bot, telegram_method = dispatcher.silent_call_requests[0]
    assert sent_bot is bot
    assert isinstance(telegram_method, SendMessage)
    assert telegram_method.chat_id == 123
    assert telegram_method.text == "Task captured"
    assert transport.closed is True


def test_webhook_ignores_non_text_message() -> None:
    singularity_requests: list[httpx2.Request] = []

    async def singularity_handler(request: httpx2.Request) -> httpx2.Response:
        singularity_requests.append(request)
        return httpx2.Response(500)

    transport = TrackingMockTransport(singularity_handler)
    singularity_client = SingularityClient(
        "injected-token",
        transport=transport,
    )
    app, _, dispatcher, _ = make_app(singularity_client=singularity_client)

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={
                "update_id": 42,
                "message": {
                    "message_id": 7,
                    "date": 1_754_000_000,
                    "chat": {"id": 123, "type": "private"},
                    "photo": [
                        {
                            "file_id": "photo-id",
                            "file_unique_id": "photo-unique-id",
                            "width": 100,
                            "height": 100,
                        }
                    ],
                },
            },
        )

    assert response.status_code == 200
    assert singularity_requests == []
    assert dispatcher.silent_call_requests == []
    assert transport.closed is True


@pytest.mark.parametrize(
    ("sender_id", "chat_id", "chat_type"),
    [
        pytest.param(456, 456, "private", id="unlinked-user"),
        pytest.param(123, -100, "group", id="group-chat"),
    ],
)
def test_webhook_does_not_capture_unauthorized_message(
    sender_id: int,
    chat_id: int,
    chat_type: str,
) -> None:
    singularity_requests: list[httpx2.Request] = []

    async def singularity_handler(request: httpx2.Request) -> httpx2.Response:
        singularity_requests.append(request)
        return httpx2.Response(500)

    transport = TrackingMockTransport(singularity_handler)
    singularity_client = SingularityClient(
        "injected-token",
        transport=transport,
    )
    app, _, dispatcher, _ = make_app(singularity_client=singularity_client)

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={
                "update_id": 42,
                "message": {
                    "message_id": 7,
                    "date": 1_754_000_000,
                    "from": {
                        "id": sender_id,
                        "is_bot": False,
                        "first_name": "Test user",
                    },
                    "chat": {"id": chat_id, "type": chat_type},
                    "text": "Buy groceries",
                },
            },
        )

    assert response.status_code == 200
    assert singularity_requests == []
    assert dispatcher.silent_call_requests == []
    assert transport.closed is True


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
    error = response.json()["detail"][0]
    assert error["type"] == "missing"
    assert error["loc"] == ["body", "update_id"]
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
    assert events == [
        "startup",
        "set_webhook",
        "shutdown",
        "session_close",
        "singularity_close",
    ]
    assert bot.session.closed is True
    assert len(dispatcher.included_routers) == 1
    assert dispatcher.allowed_updates == ["message"]
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
    assert events == ["startup", "shutdown", "session_close", "singularity_close"]


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

    assert events == [
        "startup",
        "set_webhook",
        "shutdown",
        "session_close",
        "singularity_close",
    ]
    assert bot.session.closed is True


def test_session_closes_when_dispatcher_shutdown_fails() -> None:
    app, bot, _, events = make_app(
        shutdown_error=RuntimeError("shutdown failed"),
    )

    with pytest.raises(RuntimeError, match="shutdown failed"), TestClient(app):
        pass

    assert events == ["startup", "shutdown", "session_close", "singularity_close"]
    assert bot.session.closed is True


def test_singularity_client_closes_when_bot_session_close_fails() -> None:
    app, bot, _, events = make_app(
        session_close_error=RuntimeError("session close failed"),
    )

    with pytest.raises(RuntimeError, match="session close failed"), TestClient(app):
        pass

    assert events == ["startup", "shutdown", "session_close", "singularity_close"]
    assert bot.session.closed is True
