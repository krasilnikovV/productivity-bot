from collections.abc import Callable
from typing import cast
from unittest.mock import create_autospec

import pytest
from aiogram import Bot
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from productivity_bot.bootstrap import application as application_module
from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import Settings
from tests.component.entrypoints.telegram.webhook.helpers import (
    WEBHOOK_SECRET,
    AppFactory,
    make_app_factory,
    make_bot,
)


@pytest.fixture
def make_app(
    make_settings: Callable[..., Settings],
) -> AppFactory:
    return make_app_factory(make_settings)


def test_http_lifespan_registers_webhook_and_closes_bot(
    make_app: AppFactory,
) -> None:
    app, bot, _, events = make_app(webhook_base_url="https://example.com/")

    with TestClient(app):
        assert events == ["set_webhook"]

    bot.set_webhook.assert_awaited_once_with(
        url="https://example.com/telegram/webhook",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=["message"],
    )
    assert events == ["set_webhook", "session_close"]
    bot.session.close.assert_awaited_once_with()


def test_http_lifespan_skips_registration_without_base_url(
    make_app: AppFactory,
) -> None:
    app, bot, _, events = make_app()

    with TestClient(app):
        assert events == []

    bot.set_webhook.assert_not_awaited()
    assert events == ["session_close"]


def test_http_lifespan_disposes_app_owned_database_engine(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
) -> None:
    events: list[str] = []
    database_engine = create_autospec(
        AsyncEngine,
        instance=True,
        spec_set=True,
    )
    database_engine.dispose.side_effect = lambda: events.append("database_dispose")
    database_urls: list[str] = []

    def fake_create_async_engine(database_url: str) -> AsyncEngine:
        database_urls.append(database_url)
        return cast(AsyncEngine, database_engine)

    monkeypatch.setattr(
        application_module,
        "create_async_engine",
        fake_create_async_engine,
    )
    bot = make_bot(events)
    app = create_app(make_settings(), bot=cast(Bot, bot))

    with TestClient(app):
        database_engine.dispose.assert_not_awaited()

    assert database_urls == ["postgresql+asyncpg://test:test@localhost/test"]
    assert events == ["session_close", "database_dispose"]
    database_engine.dispose.assert_awaited_once_with()


def test_set_webhook_error_closes_http_resources(make_app: AppFactory) -> None:
    app, bot, _, events = make_app(
        webhook_base_url="https://example.com",
        set_webhook_error=RuntimeError("set webhook failed"),
    )

    with pytest.raises(RuntimeError, match="set webhook failed"), TestClient(app):
        pytest.fail("TestClient entered a failed application lifespan")

    bot.session.close.assert_awaited_once_with()
    assert events == ["set_webhook", "session_close"]
