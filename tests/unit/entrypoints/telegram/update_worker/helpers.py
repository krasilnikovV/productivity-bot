import asyncio
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, create_autospec

from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod
from aiogram.types import Update

from productivity_bot.application.ports import (
    ClaimedUpdate,
    RecoveredUpdates,
    TelegramUpdateInboxRepository,
)
from productivity_bot.entrypoints.telegram.update_worker import (
    TelegramUpdateProcessingAttempt,
    TelegramUpdateWorker,
)

_DEADLOCK_TIMEOUT = 5.0


def fixed_datetime(now: datetime) -> type[datetime]:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    return FixedDatetime


def make_repository(
    claims: list[ClaimedUpdate] | None = None,
    *,
    claim_errors: list[Exception] | None = None,
    claim_started: asyncio.Event | None = None,
) -> Mock:
    repository = create_autospec(
        TelegramUpdateInboxRepository,
        instance=True,
        spec_set=True,
    )
    pending_claims = deque(claims or [])
    pending_claim_errors = deque(claim_errors or [])

    def claim_pending_update() -> ClaimedUpdate | None:
        if claim_started is not None:
            claim_started.set()
        if pending_claim_errors:
            raise pending_claim_errors.popleft()
        return pending_claims.popleft() if pending_claims else None

    repository.claim_pending_update.side_effect = claim_pending_update
    repository.recover_abandoned_updates.return_value = RecoveredUpdates(
        retried_count=0,
        uncertain_count=0,
    )
    return repository


def make_bot(
    *,
    error: Exception | None = None,
    events: list[str] | None = None,
    reply_sent: asyncio.Event | None = None,
) -> AsyncMock:
    bot = AsyncMock(spec=Bot)

    def send_method(_: TelegramMethod) -> bool:
        if events is not None:
            events.append("reply")
        if reply_sent is not None:
            reply_sent.set()
        if error is not None:
            raise error
        return True

    bot.side_effect = send_method
    return bot


def make_dispatcher(
    *,
    result: TelegramMethod | None = None,
    error: Exception | None = None,
    starts_mutation: bool = False,
) -> Mock:
    dispatcher = create_autospec(Dispatcher, instance=True, spec_set=True)

    async def feed_update(
        _: object,
        __: Update,
        **kwargs: Any,
    ) -> TelegramMethod | None:
        processing_attempt = cast(
            TelegramUpdateProcessingAttempt,
            kwargs["processing_attempt"],
        )
        if starts_mutation:
            await processing_attempt.mark_external_mutation_started()
        if error is not None:
            raise error
        return result

    dispatcher.feed_update.side_effect = feed_update
    return dispatcher


class ControlledDispatcher:
    """Hold dispatcher calls open to exercise worker concurrency and cancellation."""

    def __init__(
        self,
        release: asyncio.Event,
        *,
        starts_mutation: bool = False,
        started: asyncio.Event | None = None,
        expected_started_calls: int = 1,
    ) -> None:
        self.release = release
        self.starts_mutation = starts_mutation
        self.started = started
        self.expected_started_calls = expected_started_calls
        self.feed_call_count = 0
        self.active_calls = 0
        self.maximum_active_calls = 0
        self.cancelled_calls = 0

    async def feed_update(
        self,
        _: object,
        __: Update,
        *,
        processing_attempt: TelegramUpdateProcessingAttempt,
    ) -> None:
        self.feed_call_count += 1
        if (
            self.started is not None
            and self.feed_call_count == self.expected_started_calls
        ):
            self.started.set()
        self.active_calls += 1
        self.maximum_active_calls = max(self.maximum_active_calls, self.active_calls)
        try:
            if self.starts_mutation:
                await processing_attempt.mark_external_mutation_started()
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            raise
        finally:
            self.active_calls -= 1


def make_worker(
    repository: Mock,
    dispatcher: Mock | ControlledDispatcher,
    *,
    concurrency: int = 1,
    poll_interval: float = 0.005,
    claim_timeout: timedelta = timedelta(minutes=5),
    recovery_interval: float = 60.0,
    shutdown_grace_period: float = 0.1,
    bot: AsyncMock | None = None,
) -> TelegramUpdateWorker:
    return TelegramUpdateWorker(
        bot=cast(Bot, bot if bot is not None else make_bot()),
        dispatcher=cast(Dispatcher, dispatcher),
        update_inbox_repository=cast(TelegramUpdateInboxRepository, repository),
        concurrency=concurrency,
        poll_interval=poll_interval,
        claim_timeout=claim_timeout,
        recovery_interval=recovery_interval,
        shutdown_grace_period=shutdown_grace_period,
    )


async def wait_for_event(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=_DEADLOCK_TIMEOUT)


def signal(event: asyncio.Event) -> Callable[..., None]:
    def side_effect(*_: object) -> None:
        event.set()

    return side_effect
