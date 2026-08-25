import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any, cast

import pytest
from aiogram import Bot
from fastapi import FastAPI
from fastapi.testclient import TestClient

from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.bootstrap import application as application_module
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


class FakeTelegramUpdateInboxRepository:
    def __init__(
        self,
        events: list[str],
        *,
        insert_release: Event | None = None,
        insert_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.insert_release = insert_release
        self.insert_error = insert_error
        self.insert_started = Event()
        self.insert_calls: list[tuple[int, dict[str, Any]]] = []
        self.stored_updates: dict[int, dict[str, Any]] = {}
        self._insert_lock = asyncio.Lock()

    async def insert_update(
        self,
        update_id: int,
        payload: dict[str, Any],
    ) -> bool:
        self.insert_calls.append((update_id, payload))
        self.insert_started.set()
        if self.insert_release is not None:
            await asyncio.to_thread(self.insert_release.wait)
        if self.insert_error is not None:
            raise self.insert_error
        async with self._insert_lock:
            if update_id in self.stored_updates:
                return False
            self.stored_updates[update_id] = payload
            self.events.append("insert_committed")
            return True


class FakeDatabaseEngine:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.disposed = False

    async def dispose(self) -> None:
        self.events.append("database_dispose")
        self.disposed = True


def make_settings(*, webhook_base_url: str = "") -> Settings:
    return Settings(
        telegram_bot_token="123456:test-token",
        telegram_allowed_user_ids=frozenset({123}),
        telegram_webhook_secret=WEBHOOK_SECRET,
        singularity_api_token="not-used-by-http-process",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        webhook_base_url=webhook_base_url,
        _env_file=None,
    )


def make_app(
    *,
    webhook_base_url: str = "",
    insert_release: Event | None = None,
    insert_error: Exception | None = None,
    set_webhook_error: Exception | None = None,
    session_close_error: Exception | None = None,
) -> tuple[
    FastAPI,
    FakeBot,
    FakeTelegramUpdateInboxRepository,
    list[str],
]:
    events: list[str] = []
    bot = FakeBot(
        events,
        set_webhook_error=set_webhook_error,
        session_close_error=session_close_error,
    )
    repository = FakeTelegramUpdateInboxRepository(
        events,
        insert_release=insert_release,
        insert_error=insert_error,
    )
    app = create_app(
        make_settings(webhook_base_url=webhook_base_url),
        bot=cast(Bot, bot),
        telegram_update_inbox_repository=cast(
            TelegramUpdateInboxRepository,
            repository,
        ),
    )
    return app, bot, repository, events


@pytest.mark.filterwarnings(
    "error::pydantic.warnings.UnsupportedFieldAttributeWarning",
)
def test_webhook_durably_accepts_update_while_no_worker_is_running() -> None:
    app, _, repository, events = make_app()
    payload = {"update_id": 42}

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json=payload,
        )

    assert response.status_code == 200
    assert response.content == b""
    assert repository.stored_updates == {42: payload}
    assert events == ["insert_committed", "session_close"]


def test_webhook_waits_for_commit_before_acknowledging() -> None:
    insert_release = Event()
    app, _, repository, _ = make_app(insert_release=insert_release)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        response_future = executor.submit(
            client.post,
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={"update_id": 42},
        )
        try:
            assert repository.insert_started.wait(timeout=1)
            with pytest.raises(TimeoutError):
                response_future.result(timeout=0.1)
        finally:
            insert_release.set()
        response = response_future.result(timeout=1)

    assert response.status_code == 200


def test_webhook_acknowledges_duplicate_and_preserves_original_payload() -> None:
    app, _, repository, _ = make_app()
    original_payload = {"update_id": 42, "source": "original"}
    duplicate_payload = {"update_id": 42, "source": "duplicate"}

    with TestClient(app) as client:
        original_response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json=original_payload,
        )
        duplicate_response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json=duplicate_payload,
        )

    assert original_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert repository.stored_updates == {42: original_payload}


def test_webhook_returns_500_when_insert_fails() -> None:
    app, _, repository, _ = make_app(
        insert_error=RuntimeError("database unavailable")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={"update_id": 42},
        )

    assert response.status_code == 500
    assert repository.stored_updates == {}


@pytest.mark.parametrize("secret", [None, "incorrect_secret"])
def test_webhook_rejects_missing_or_incorrect_secret(secret: str | None) -> None:
    app, _, repository, _ = make_app()
    headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=headers,
            json={"update_id": 42},
        )

    assert response.status_code == 401
    assert repository.insert_calls == []


def test_webhook_rejects_malformed_update() -> None:
    app, _, repository, _ = make_app()

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={},
        )

    assert response.status_code == 422
    assert repository.insert_calls == []


def test_http_lifespan_registers_webhook_and_closes_bot() -> None:
    app, bot, _, events = make_app(webhook_base_url="https://example.com/")

    with TestClient(app):
        assert events == ["set_webhook"]

    assert bot.set_webhook_calls == [
        {
            "url": "https://example.com/telegram/webhook",
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": ["message"],
        }
    ]
    assert events == ["set_webhook", "session_close"]
    assert bot.session.closed is True


def test_http_lifespan_skips_registration_without_base_url() -> None:
    app, bot, _, events = make_app()

    with TestClient(app):
        assert events == []

    assert bot.set_webhook_calls == []
    assert events == ["session_close"]


def test_http_lifespan_disposes_app_owned_database_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    database_engine = FakeDatabaseEngine(events)
    database_urls: list[str] = []

    def fake_create_async_engine(database_url: str) -> FakeDatabaseEngine:
        database_urls.append(database_url)
        return database_engine

    monkeypatch.setattr(
        application_module,
        "create_async_engine",
        fake_create_async_engine,
    )
    bot = FakeBot(events)
    app = create_app(make_settings(), bot=cast(Bot, bot))

    with TestClient(app):
        assert database_engine.disposed is False

    assert database_urls == ["postgresql+asyncpg://test:test@localhost/test"]
    assert events == ["session_close", "database_dispose"]


def test_set_webhook_error_closes_http_resources() -> None:
    app, bot, _, events = make_app(
        webhook_base_url="https://example.com",
        set_webhook_error=RuntimeError("set webhook failed"),
    )

    with pytest.raises(RuntimeError, match="set webhook failed"), TestClient(app):
        pytest.fail("TestClient entered a failed application lifespan")

    assert bot.session.closed is True
    assert events == ["set_webhook", "session_close"]
