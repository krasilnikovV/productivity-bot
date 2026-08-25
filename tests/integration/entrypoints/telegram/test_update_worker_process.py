import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod

from productivity_bot.application.ports import ClaimedUpdate, RecoveredUpdates
from productivity_bot.application.use_cases import CaptureTask
from productivity_bot.domain.entities import Task
from productivity_bot.entrypoints.telegram.handlers import CaptureTaskHandler
from productivity_bot.entrypoints.telegram.update_worker import TelegramUpdateWorker
from productivity_bot.entrypoints.telegram.webhook import TelegramWebhookHandler


class FakeTaskRepository:
    def __init__(self) -> None:
        self.created_titles: list[str] = []

    async def create_task(self, title: str) -> Task:
        self.created_titles.append(title)
        return Task(id="T-123", title=title)

    async def list_active_tasks(self) -> Sequence[Task]:
        return []

    async def complete_task(self, task_id: str) -> None:
        return None


class FakeBot:
    def __init__(self) -> None:
        self.id = 123456
        self.calls: list[TelegramMethod] = []

    async def __call__(self, method: TelegramMethod) -> object:
        self.calls.append(method)
        return True


class FakeInboxRepository:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.claimed = False
        self.marker_calls: list[tuple[int, int]] = []
        self.status = "empty"

    async def insert_update(
        self,
        update_id: int,
        payload: dict[str, Any],
    ) -> bool:
        if self.payload is not None:
            return False
        self.payload = payload
        self.status = "pending"
        return True

    async def claim_pending_update(self) -> ClaimedUpdate | None:
        if self.payload is None or self.claimed:
            return None
        self.claimed = True
        self.status = "processing"
        return ClaimedUpdate(
            update_id=int(self.payload["update_id"]),
            payload=self.payload,
            attempt_count=1,
        )

    async def mark_external_mutation_started(
        self,
        update_id: int,
        attempt_count: int,
    ) -> None:
        self.marker_calls.append((update_id, attempt_count))

    async def reschedule(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
        available_at: datetime,
    ) -> None:
        self.status = "pending"

    async def mark_succeeded(self, update_id: int, attempt_count: int) -> None:
        self.status = "succeeded"

    async def mark_failed(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        self.status = "failed"

    async def mark_uncertain(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        self.status = "uncertain"

    async def recover_abandoned_updates(
        self,
        claimed_before: datetime,
    ) -> RecoveredUpdates:
        return RecoveredUpdates(retried_count=0, uncertain_count=0)


@pytest.mark.asyncio
async def test_worker_started_later_processes_durably_accepted_update() -> None:
    repository = FakeInboxRepository()
    bot = FakeBot()
    dispatcher = Dispatcher()
    task_repository = FakeTaskRepository()
    handler = CaptureTaskHandler(
        CaptureTask(task_repository),
        allowed_user_ids=frozenset({123}),
    )
    dispatcher.include_router(handler.router)
    webhook = TelegramWebhookHandler(
        bot=cast(Bot, bot),
        webhook_secret="secret",
        update_inbox_repository=repository,
    )
    payload = {
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
    }

    response = await webhook.handle(payload, "secret")

    assert response.status_code == 200
    assert repository.status == "pending"
    assert task_repository.created_titles == []

    worker = TelegramUpdateWorker(
        bot=cast(Bot, bot),
        dispatcher=dispatcher,
        update_inbox_repository=repository,
        concurrency=1,
        poll_interval=0.001,
        claim_timeout=timedelta(minutes=5),
        recovery_interval=60.0,
        shutdown_grace_period=0.1,
    )
    await worker.start()
    try:
        async def wait_for_success() -> None:
            while repository.status != "succeeded":
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_success(), timeout=0.5)
    finally:
        await worker.stop()

    assert repository.marker_calls == [(42, 1)]
    assert task_repository.created_titles == ["Buy groceries"]
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_unauthorized_update_is_consumed_without_mutation_marker() -> None:
    repository = FakeInboxRepository()
    bot = FakeBot()
    dispatcher = Dispatcher()
    task_repository = FakeTaskRepository()
    dispatcher.include_router(
        CaptureTaskHandler(
            CaptureTask(task_repository),
            allowed_user_ids=frozenset({123}),
        ).router
    )
    payload = {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "date": 1_754_000_000,
            "from": {
                "id": 456,
                "is_bot": False,
                "first_name": "Unauthorized user",
            },
            "chat": {"id": 456, "type": "private"},
            "text": "Buy groceries",
        },
    }
    await repository.insert_update(42, payload)
    worker = TelegramUpdateWorker(
        bot=cast(Bot, bot),
        dispatcher=dispatcher,
        update_inbox_repository=repository,
        concurrency=1,
        poll_interval=0.001,
        claim_timeout=timedelta(minutes=5),
        recovery_interval=60.0,
        shutdown_grace_period=0.1,
    )

    await worker.start()
    try:
        async def wait_for_success() -> None:
            while repository.status != "succeeded":
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait_for_success(), timeout=0.5)
    finally:
        await worker.stop()

    assert repository.marker_calls == []
    assert task_repository.created_titles == []
    assert bot.calls == []
