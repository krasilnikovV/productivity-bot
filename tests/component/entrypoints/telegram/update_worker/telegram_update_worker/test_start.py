from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher

from productivity_bot.application.ports import (
    ClaimedUpdate,
    RecoveredUpdates,
    TaskReadError,
)
from productivity_bot.application.use_cases import CaptureTask, GetNextAction
from productivity_bot.entrypoints.telegram.handlers import (
    CaptureTaskHandler,
    NextActionHandler,
)
from productivity_bot.entrypoints.telegram.update_worker import TelegramUpdateWorker
from tests.helpers.telegram import make_raw_message_update, wait_until


def make_bot() -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.id = 123456
    return bot


class FakeInboxRepository:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.claimed = False
        self.marker_calls: list[tuple[int, int]] = []
        self.reschedule_calls: list[tuple[int, int, str, datetime]] = []
        self.failed_calls: list[tuple[int, int, str]] = []
        self.uncertain_calls: list[tuple[int, int, str]] = []
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
        self.reschedule_calls.append((update_id, attempt_count, error, available_at))
        self.status = "pending"

    async def mark_succeeded(self, update_id: int, attempt_count: int) -> None:
        self.status = "succeeded"

    async def mark_failed(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        self.failed_calls.append((update_id, attempt_count, error))
        self.status = "failed"

    async def mark_uncertain(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        self.uncertain_calls.append((update_id, attempt_count, error))
        self.status = "uncertain"

    async def recover_abandoned_updates(
        self,
        claimed_before: datetime,
    ) -> RecoveredUpdates:
        return RecoveredUpdates(retried_count=0, uncertain_count=0)


@pytest.mark.asyncio
async def test_unauthorized_update_is_consumed_without_mutation_marker() -> None:
    repository = FakeInboxRepository()
    bot = make_bot()
    dispatcher = Dispatcher()
    capture_task = AsyncMock(spec=CaptureTask)
    dispatcher.include_router(
        CaptureTaskHandler(
            cast(CaptureTask, capture_task),
            allowed_user_ids=frozenset({123}),
        ).router
    )
    payload = make_raw_message_update(
        update_id=42,
        sender_id=456,
        sender_name="Unauthorized user",
        text="Buy groceries",
    )
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
        await wait_until(lambda: repository.status == "succeeded", timeout=0.5)
    finally:
        await worker.stop()

    assert repository.marker_calls == []
    capture_task.execute.assert_not_awaited()
    bot.assert_not_awaited()


@pytest.mark.asyncio
async def test_retryable_next_action_read_error_is_rescheduled_without_marker() -> None:
    repository = FakeInboxRepository()
    bot = make_bot()
    dispatcher = Dispatcher()
    get_next_action = AsyncMock(spec=GetNextAction)
    get_next_action.execute.side_effect = TaskReadError(
        "Singularity active-task read failed",
        retryable=True,
    )
    dispatcher.include_router(
        NextActionHandler(
            cast(GetNextAction, get_next_action),
            allowed_user_ids=frozenset({123}),
        ).router
    )
    await repository.insert_update(
        42,
        make_raw_message_update(
            update_id=42,
            sender_id=123,
            sender_name="Test user",
            text="/next",
        ),
    )
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
        await wait_until(lambda: bool(repository.reschedule_calls), timeout=0.5)
    finally:
        await worker.stop()

    assert repository.marker_calls == []
    assert repository.reschedule_calls[0][:2] == (42, 1)
    assert repository.failed_calls == []
    assert repository.uncertain_calls == []
    bot.assert_not_awaited()
