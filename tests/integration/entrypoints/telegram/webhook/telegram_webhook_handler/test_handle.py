import asyncio
import json
from collections.abc import Callable
from typing import cast

import httpx2
import pytest
from aiogram import Bot
from aiogram.methods import SendMessage, TelegramMethod
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import (
    PostgresTelegramUpdateInboxRepository,
    TelegramUpdateInboxModel,
)
from productivity_bot.adapters.singularity import SingularityClient
from productivity_bot.bootstrap.application import create_app
from productivity_bot.bootstrap.telegram_update_worker import run_telegram_update_worker
from productivity_bot.config import Settings
from tests.helpers.telegram import make_raw_message_update

WEBHOOK_SECRET = "test_webhook_secret"


class FakeBotSession:
    async def close(self) -> None:
        return None


class FakeBot:
    def __init__(self) -> None:
        self.id = 123456
        self.session = FakeBotSession()
        self.sent_methods: list[TelegramMethod] = []
        self.method_sent = asyncio.Event()

    async def __call__(self, method: TelegramMethod) -> bool:
        self.sent_methods.append(method)
        self.method_sent.set()
        return True


async def load_update(
    session_factory: async_sessionmaker[AsyncSession],
    update_id: int,
) -> TelegramUpdateInboxModel:
    async with session_factory() as session:
        update = await session.get(TelegramUpdateInboxModel, update_id)
        assert update is not None
        return update


@pytest.mark.asyncio
async def test_webhook_commit_is_processed_by_a_later_worker_instance(
    session_factory: async_sessionmaker[AsyncSession],
    postgres_engine: AsyncEngine,
    make_settings: Callable[..., Settings],
) -> None:
    payload = make_raw_message_update(
        update_id=42,
        sender_id=123,
        sender_name="Test user",
        text="Buy groceries",
    )
    webhook_repository = PostgresTelegramUpdateInboxRepository(session_factory)
    app = create_app(
        make_settings(telegram_update_worker_poll_interval_seconds=0.01),
        bot=cast(Bot, FakeBot()),
        telegram_update_inbox_repository=webhook_repository,
    )

    await postgres_engine.dispose()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
                json=payload,
            )
    finally:
        await postgres_engine.dispose()

    assert response.status_code == 200
    assert response.content == b""
    stored_update = await load_update(session_factory, 42)
    assert stored_update.payload == payload
    assert stored_update.status == "pending"
    assert stored_update.attempt_count == 0

    singularity_request_received = asyncio.Event()

    async def singularity_handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/task"
        assert json.loads(request.content) == {"title": "Buy groceries"}
        singularity_request_received.set()
        return httpx2.Response(
            201,
            json={"id": "T-123", "title": "Buy groceries"},
        )

    worker_repository = PostgresTelegramUpdateInboxRepository(session_factory)
    shutdown_event = asyncio.Event()
    worker_task = asyncio.create_task(
        run_telegram_update_worker(
            make_settings(telegram_update_worker_poll_interval_seconds=0.01),
            shutdown_event,
            bot=cast(Bot, FakeBot()),
            singularity_client=SingularityClient(
                "test-singularity-token",
                transport=httpx2.MockTransport(singularity_handler),
            ),
            telegram_update_inbox_repository=worker_repository,
        )
    )
    try:
        await asyncio.wait_for(singularity_request_received.wait(), timeout=2)
    finally:
        shutdown_event.set()
        await asyncio.wait_for(worker_task, timeout=2)

    processed_update = await load_update(session_factory, 42)
    assert processed_update.status == "succeeded"
    assert processed_update.attempt_count == 1
    assert processed_update.external_mutation_started_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("update_id", "tasks", "expected_reply"),
    [
        pytest.param(
            43,
            [{"id": "T-123", "title": "Book dentist appointment"}],
            "Next task: Book dentist appointment",
            id="selected-task",
        ),
        pytest.param(
            44,
            [],
            "No available tasks",
            id="empty-tasks",
        ),
    ],
)
async def test_webhook_next_command_is_processed_without_mutation_marker(
    session_factory: async_sessionmaker[AsyncSession],
    postgres_engine: AsyncEngine,
    make_settings: Callable[..., Settings],
    update_id: int,
    tasks: list[dict[str, str]],
    expected_reply: str,
) -> None:
    payload = make_raw_message_update(
        update_id=update_id,
        sender_id=123,
        sender_name="Test user",
        text="/next",
    )
    webhook_repository = PostgresTelegramUpdateInboxRepository(session_factory)
    app = create_app(
        make_settings(telegram_update_worker_poll_interval_seconds=0.01),
        bot=cast(Bot, FakeBot()),
        telegram_update_inbox_repository=webhook_repository,
    )

    await postgres_engine.dispose()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
                json=payload,
            )
    finally:
        await postgres_engine.dispose()

    assert response.status_code == 200

    requests: list[httpx2.Request] = []

    async def singularity_handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v2/task"
        assert dict(request.url.params) == {
            "checked": "0",
            "isNote": "false",
            "includeRemoved": "false",
            "includeArchived": "false",
            "includeAllRecurrenceInstances": "true",
            "fields": "id,title,start,deadline,priority",
            "maxCount": "1000",
            "offset": "0",
        }
        return httpx2.Response(200, json={"tasks": tasks})

    worker_repository = PostgresTelegramUpdateInboxRepository(session_factory)
    worker_bot = FakeBot()
    shutdown_event = asyncio.Event()
    worker_task = asyncio.create_task(
        run_telegram_update_worker(
            make_settings(telegram_update_worker_poll_interval_seconds=0.01),
            shutdown_event,
            bot=cast(Bot, worker_bot),
            singularity_client=SingularityClient(
                "test-singularity-token",
                transport=httpx2.MockTransport(singularity_handler),
            ),
            telegram_update_inbox_repository=worker_repository,
        )
    )
    try:
        await asyncio.wait_for(worker_bot.method_sent.wait(), timeout=2)
    finally:
        shutdown_event.set()
        await asyncio.wait_for(worker_task, timeout=2)

    processed_update = await load_update(session_factory, update_id)
    assert processed_update.status == "succeeded"
    assert processed_update.attempt_count == 1
    assert processed_update.external_mutation_started_at is None
    assert len(requests) == 1
    assert len(worker_bot.sent_methods) == 1
    sent_method = worker_bot.sent_methods[0]
    assert isinstance(sent_method, SendMessage)
    assert sent_method.chat_id == 123
    assert sent_method.text == expected_reply
