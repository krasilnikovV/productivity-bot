import asyncio
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Update

from productivity_bot.application.ports import (
    ClaimedUpdate,
    RecoveredUpdates,
    TaskMutationConfirmedError,
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
    UpdateTransitionError,
)
from productivity_bot.entrypoints.telegram.update_worker import (
    TelegramUpdateProcessingAttempt,
    TelegramUpdateWorker,
)


class FakeTelegramUpdateInboxRepository:
    def __init__(
        self,
        claims: list[ClaimedUpdate] | None = None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.claims = deque(claims or [])
        self.events = events
        self.claim_calls = 0
        self.claim_started = asyncio.Event()
        self.claim_release: asyncio.Event | None = None
        self.claim_errors: deque[Exception] = deque()
        self.marker_error: Exception | None = None
        self.reschedule_error: Exception | None = None
        self.succeeded_error: Exception | None = None
        self.uncertain_error: Exception | None = None
        self.reschedule_errors: deque[Exception] = deque()
        self.succeeded_errors: deque[Exception] = deque()
        self.failed_errors: deque[Exception] = deque()
        self.marker_calls: list[tuple[int, int]] = []
        self.reschedule_calls: list[tuple[int, int, str, datetime]] = []
        self.succeeded_calls: list[tuple[int, int]] = []
        self.failed_calls: list[tuple[int, int, str]] = []
        self.uncertain_calls: list[tuple[int, int, str]] = []
        self.recovery_calls: list[datetime] = []
        self.recovery_errors: dict[int, Exception] = {}

    async def claim_pending_update(self) -> ClaimedUpdate | None:
        self.claim_calls += 1
        self.claim_started.set()
        if self.claim_release is not None:
            await self.claim_release.wait()
        if self.claim_errors:
            raise self.claim_errors.popleft()
        return self.claims.popleft() if self.claims else None

    async def mark_external_mutation_started(
        self,
        update_id: int,
        attempt_count: int,
    ) -> None:
        self.marker_calls.append((update_id, attempt_count))
        if self.marker_error is not None:
            raise self.marker_error

    async def reschedule(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
        available_at: datetime,
    ) -> None:
        self.reschedule_calls.append(
            (update_id, attempt_count, error, available_at)
        )
        if self.reschedule_errors:
            raise self.reschedule_errors.popleft()
        if self.reschedule_error is not None:
            raise self.reschedule_error

    async def mark_succeeded(self, update_id: int, attempt_count: int) -> None:
        if self.events is not None:
            self.events.append("succeeded")
        self.succeeded_calls.append((update_id, attempt_count))
        if self.succeeded_errors:
            raise self.succeeded_errors.popleft()
        if self.succeeded_error is not None:
            raise self.succeeded_error

    async def mark_failed(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        self.failed_calls.append((update_id, attempt_count, error))
        if self.failed_errors:
            raise self.failed_errors.popleft()

    async def mark_uncertain(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        self.uncertain_calls.append((update_id, attempt_count, error))
        if self.uncertain_error is not None:
            raise self.uncertain_error

    async def recover_abandoned_updates(
        self,
        claimed_before: datetime,
    ) -> RecoveredUpdates:
        self.recovery_calls.append(claimed_before)
        call_number = len(self.recovery_calls)
        if error := self.recovery_errors.get(call_number):
            raise error
        return RecoveredUpdates(retried_count=0, uncertain_count=0)


class FakeBot:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.calls: list[TelegramMethod] = []

    async def __call__(self, method: TelegramMethod) -> object:
        if self.events is not None:
            self.events.append("reply")
        self.calls.append(method)
        if self.error is not None:
            raise self.error
        return True


class FakeDispatcher:
    def __init__(
        self,
        *,
        result: TelegramMethod | None = None,
        error: Exception | None = None,
        release: asyncio.Event | None = None,
        starts_mutation: bool = False,
    ) -> None:
        self.result = result
        self.error = error
        self.release = release
        self.starts_mutation = starts_mutation
        self.feed_calls: list[tuple[object, Update]] = []
        self.processing_attempts: list[TelegramUpdateProcessingAttempt] = []
        self.active_calls = 0
        self.maximum_active_calls = 0
        self.cancelled_calls = 0

    async def feed_update(
        self,
        bot: object,
        update: Update,
        *,
        processing_attempt: TelegramUpdateProcessingAttempt,
    ) -> Any:
        self.feed_calls.append((bot, update))
        self.processing_attempts.append(processing_attempt)
        self.active_calls += 1
        self.maximum_active_calls = max(
            self.maximum_active_calls,
            self.active_calls,
        )
        try:
            if self.starts_mutation:
                await processing_attempt.mark_external_mutation_started()
            if self.release is not None:
                await self.release.wait()
            if self.error is not None:
                raise self.error
            return self.result
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            raise
        finally:
            self.active_calls -= 1

def make_worker(
    repository: FakeTelegramUpdateInboxRepository,
    dispatcher: FakeDispatcher,
    *,
    concurrency: int = 1,
    poll_interval: float = 0.005,
    claim_timeout: timedelta = timedelta(minutes=5),
    recovery_interval: float = 60.0,
    shutdown_grace_period: float = 0.1,
    bot: FakeBot | None = None,
) -> TelegramUpdateWorker:
    return TelegramUpdateWorker(
        bot=cast(Bot, bot if bot is not None else FakeBot()),
        dispatcher=cast(Dispatcher, dispatcher),
        update_inbox_repository=repository,
        concurrency=concurrency,
        poll_interval=poll_interval,
        claim_timeout=claim_timeout,
        recovery_interval=recovery_interval,
        shutdown_grace_period=shutdown_grace_period,
    )


async def wait_until(
    condition: Callable[[], bool],
    *,
    timeout: float = 0.5,
) -> None:
    async def wait() -> None:
        while not condition():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=timeout)


@pytest.mark.asyncio
async def test_success_executes_telegram_method_and_marks_attempt_succeeded() -> None:
    claimed_update = ClaimedUpdate(42, {"update_id": 42}, 3)
    repository = FakeTelegramUpdateInboxRepository([claimed_update])
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = FakeDispatcher(result=telegram_method, starts_mutation=True)
    bot = FakeBot()
    worker = make_worker(repository, dispatcher, bot=bot)

    await worker.start()
    await wait_until(lambda: bool(repository.succeeded_calls))
    await worker.stop()

    assert repository.marker_calls == [(42, 3)]
    assert repository.succeeded_calls == [(42, 3)]
    assert len(dispatcher.feed_calls) == 1
    assert dispatcher.feed_calls[0][1].update_id == 42
    assert bot.calls == [telegram_method]
    assert repository.failed_calls == []
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_malformed_durable_payload_is_marked_failed_without_dispatch() -> None:
    repository = FakeTelegramUpdateInboxRepository([ClaimedUpdate(42, {}, 1)])
    dispatcher = FakeDispatcher()
    worker = make_worker(repository, dispatcher)

    await worker.start()
    await wait_until(lambda: bool(repository.failed_calls))
    await worker.stop()

    update_id, attempt_count, error = repository.failed_calls[0]
    assert (update_id, attempt_count) == (42, 1)
    assert error.startswith("Invalid durable Telegram update payload:")
    assert len(error) <= 1_000
    assert repository.marker_calls == []
    assert dispatcher.feed_calls == []


@pytest.mark.asyncio
async def test_post_marker_error_is_marked_uncertain() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 2)]
    )
    dispatcher = FakeDispatcher(
        error=RuntimeError("remote outcome unknown"),
        starts_mutation=True,
    )
    worker = make_worker(repository, dispatcher)

    await worker.start()
    await wait_until(lambda: bool(repository.uncertain_calls))
    await worker.stop()

    assert repository.marker_calls == [(42, 2)]
    assert repository.succeeded_calls == []
    assert repository.uncertain_calls[0][:2] == (42, 2)
    assert "remote outcome unknown" in repository.uncertain_calls[0][2]


@pytest.mark.asyncio
async def test_telegram_method_error_leaves_attempt_succeeded() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 2)]
    )
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = FakeDispatcher(result=telegram_method, starts_mutation=True)
    bot = FakeBot(
        error=TelegramNetworkError(
            method=telegram_method,
            message="connection lost",
        )
    )
    worker = make_worker(repository, dispatcher, bot=bot)

    await worker.start()
    await wait_until(lambda: bool(repository.succeeded_calls))
    await worker.stop()

    assert bot.calls == [telegram_method]
    assert repository.succeeded_calls == [(42, 2)]
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_success_is_persisted_before_telegram_reply() -> None:
    events: list[str] = []
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)],
        events=events,
    )
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = FakeDispatcher(result=telegram_method, starts_mutation=True)
    worker = make_worker(repository, dispatcher, bot=FakeBot(events=events))

    await worker.start()
    await wait_until(lambda: len(events) == 2)
    await worker.stop()

    assert events == ["succeeded", "reply"]


@pytest.mark.asyncio
async def test_confirmed_mutation_retries_succeeded_transition_before_reply() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    repository.succeeded_errors.append(RuntimeError("database unavailable"))
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = FakeDispatcher(result=telegram_method, starts_mutation=True)
    bot = FakeBot()
    worker = make_worker(repository, dispatcher, bot=bot)

    await worker.start()
    await wait_until(lambda: len(repository.succeeded_calls) == 2)
    await wait_until(lambda: bool(bot.calls))
    await worker.stop()

    assert repository.succeeded_calls == [(42, 1), (42, 1)]
    assert repository.uncertain_calls == []
    assert bot.calls == [telegram_method]


@pytest.mark.asyncio
async def test_unsupported_update_succeeds_without_mutation_marker() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    worker = make_worker(repository, FakeDispatcher())

    await worker.start()
    await wait_until(lambda: bool(repository.succeeded_calls))
    await worker.stop()

    assert repository.marker_calls == []
    assert repository.succeeded_calls == [(42, 1)]


@pytest.mark.asyncio
async def test_pre_mutation_processing_error_is_terminal_failed() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    worker = make_worker(
        repository,
        FakeDispatcher(error=ValueError("deterministic validation failed")),
    )

    await worker.start()
    await wait_until(lambda: bool(repository.failed_calls))
    await worker.stop()

    assert repository.marker_calls == []
    assert repository.failed_calls[0][:2] == (42, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [False, True])
async def test_known_not_applied_mutation_is_classified_safely(
    retryable: bool,
) -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    worker = make_worker(
        repository,
        FakeDispatcher(
            error=TaskMutationNotAppliedError("not applied", retryable=retryable),
            starts_mutation=True,
        ),
    )

    await worker.start()
    if retryable:
        await wait_until(lambda: bool(repository.reschedule_calls))
    else:
        await wait_until(lambda: bool(repository.failed_calls))
    await worker.stop()

    assert repository.marker_calls == [(42, 1)]
    assert bool(repository.reschedule_calls) is retryable
    assert bool(repository.failed_calls) is not retryable
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_terminal_rejection_retries_failed_transition() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    repository.failed_errors.append(RuntimeError("database unavailable"))
    worker = make_worker(
        repository,
        FakeDispatcher(
            error=TaskMutationNotAppliedError("rejected", retryable=False),
            starts_mutation=True,
        ),
    )

    await worker.start()
    await wait_until(lambda: len(repository.failed_calls) == 2)
    await worker.stop()

    assert repository.failed_calls[0] == repository.failed_calls[1]
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_safe_retry_retries_reschedule_transition() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    repository.reschedule_errors.append(RuntimeError("database unavailable"))
    worker = make_worker(
        repository,
        FakeDispatcher(
            error=TaskMutationNotAppliedError("not sent", retryable=True),
            starts_mutation=True,
        ),
    )

    await worker.start()
    await wait_until(lambda: len(repository.reschedule_calls) == 2)
    await worker.stop()

    assert repository.reschedule_calls[0] == repository.reschedule_calls[1]
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_unknown_mutation_outcome_is_uncertain() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    worker = make_worker(
        repository,
        FakeDispatcher(
            error=TaskMutationOutcomeUnknownError("unknown"),
            starts_mutation=True,
        ),
    )

    await worker.start()
    await wait_until(lambda: bool(repository.uncertain_calls))
    await worker.stop()

    assert repository.marker_calls == [(42, 1)]
    assert repository.uncertain_calls[0][:2] == (42, 1)


@pytest.mark.asyncio
async def test_confirmed_mutation_with_invalid_result_succeeds_without_reply() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    bot = FakeBot()
    worker = make_worker(
        repository,
        FakeDispatcher(
            error=TaskMutationConfirmedError("invalid response"),
            starts_mutation=True,
        ),
        bot=bot,
    )

    await worker.start()
    await wait_until(lambda: bool(repository.succeeded_calls))
    await worker.stop()

    assert repository.succeeded_calls == [(42, 1)]
    assert repository.uncertain_calls == []
    assert bot.calls == []


@pytest.mark.asyncio
async def test_marker_failure_reschedules_without_dispatching() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 4)]
    )
    repository.marker_error = RuntimeError("database unavailable")
    dispatcher = FakeDispatcher(starts_mutation=True)
    worker = make_worker(repository, dispatcher, poll_interval=0.05)
    before_processing = datetime.now(UTC)

    await worker.start()
    await wait_until(lambda: bool(repository.reschedule_calls))
    await worker.stop()

    update_id, attempt_count, error, available_at = repository.reschedule_calls[0]
    assert (update_id, attempt_count) == (42, 4)
    assert "database unavailable" in error
    assert available_at >= before_processing + timedelta(seconds=0.05)
    assert len(dispatcher.feed_calls) == 1
    assert dispatcher.processing_attempts[0].external_mutation_started is False


@pytest.mark.asyncio
async def test_initial_and_periodic_recovery_use_claim_timeout() -> None:
    repository = FakeTelegramUpdateInboxRepository()
    dispatcher = FakeDispatcher()
    claim_timeout = timedelta(seconds=30)
    worker = make_worker(
        repository,
        dispatcher,
        claim_timeout=claim_timeout,
        recovery_interval=0.01,
    )
    before_start = datetime.now(UTC)

    await worker.start()
    await wait_until(lambda: len(repository.recovery_calls) >= 2)
    await worker.stop()

    after_stop = datetime.now(UTC)
    assert before_start - claim_timeout <= repository.recovery_calls[0]
    assert repository.recovery_calls[0] <= after_stop - claim_timeout


@pytest.mark.asyncio
async def test_initial_recovery_failure_prevents_processing_loops_from_starting() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    repository.recovery_errors[1] = RuntimeError("database unavailable")
    dispatcher = FakeDispatcher()
    worker = make_worker(repository, dispatcher)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker.start()

    await worker.stop()
    assert repository.claim_calls == 0
    assert dispatcher.feed_calls == []


@pytest.mark.asyncio
async def test_periodic_recovery_and_claim_errors_do_not_stop_worker() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    repository.claim_errors.append(RuntimeError("claim failed"))
    repository.recovery_errors[2] = RuntimeError("recovery failed")
    dispatcher = FakeDispatcher()
    worker = make_worker(
        repository,
        dispatcher,
        poll_interval=0.005,
        recovery_interval=0.005,
    )

    await worker.start()
    await wait_until(lambda: bool(repository.succeeded_calls))
    await wait_until(lambda: len(repository.recovery_calls) >= 3)
    await worker.stop()

    assert repository.succeeded_calls == [(42, 1)]


@pytest.mark.asyncio
async def test_attempt_count_fences_every_processing_transition() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 7)]
    )
    repository.succeeded_error = UpdateTransitionError("stale attempt")
    dispatcher = FakeDispatcher()
    worker = make_worker(repository, dispatcher)

    await worker.start()
    await wait_until(lambda: bool(repository.succeeded_calls))
    await worker.stop()

    assert repository.marker_calls == []
    assert repository.succeeded_calls == [(42, 7)]
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_configured_concurrency_bounds_feed_update_calls() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(update_id, {"update_id": update_id}, 1) for update_id in range(3)]
    )
    release = asyncio.Event()
    dispatcher = FakeDispatcher(release=release, starts_mutation=True)
    worker = make_worker(repository, dispatcher, concurrency=2)

    await worker.start()
    await wait_until(lambda: len(dispatcher.feed_calls) == 2)

    assert dispatcher.maximum_active_calls == 2
    assert len(dispatcher.feed_calls) == 2

    release.set()
    await wait_until(lambda: len(repository.succeeded_calls) == 3)
    await worker.stop()
    assert dispatcher.maximum_active_calls == 2


@pytest.mark.asyncio
async def test_claim_finishing_after_shutdown_is_rescheduled_without_dispatch() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    repository.claim_release = asyncio.Event()
    dispatcher = FakeDispatcher()
    worker = make_worker(repository, dispatcher, shutdown_grace_period=0.2)

    await worker.start()
    await repository.claim_started.wait()
    stop_task = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    repository.claim_release.set()
    await stop_task

    assert repository.marker_calls == []
    assert dispatcher.feed_calls == []
    assert repository.reschedule_calls[0][:2] == (42, 1)
    assert "shutdown" in repository.reschedule_calls[0][2]


@pytest.mark.asyncio
async def test_shutdown_allows_in_flight_update_to_finish_within_grace() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    release = asyncio.Event()
    dispatcher = FakeDispatcher(release=release)
    worker = make_worker(repository, dispatcher, shutdown_grace_period=0.2)

    await worker.start()
    await wait_until(lambda: len(dispatcher.feed_calls) == 1)
    stop_task = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()

    release.set()
    await stop_task
    assert repository.succeeded_calls == [(42, 1)]
    assert dispatcher.cancelled_calls == 0


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_update_after_grace() -> None:
    repository = FakeTelegramUpdateInboxRepository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)]
    )
    dispatcher = FakeDispatcher(release=asyncio.Event(), starts_mutation=True)
    worker = make_worker(repository, dispatcher, shutdown_grace_period=0.01)

    await worker.start()
    await wait_until(lambda: len(dispatcher.feed_calls) == 1)
    await asyncio.wait_for(worker.stop(), timeout=0.1)

    assert dispatcher.cancelled_calls == 1
    assert repository.marker_calls == [(42, 1)]
    assert repository.succeeded_calls == []
    assert repository.uncertain_calls == []


@pytest.mark.asyncio
async def test_idle_shutdown_does_not_wait_for_poll_interval() -> None:
    repository = FakeTelegramUpdateInboxRepository()
    dispatcher = FakeDispatcher()
    worker = make_worker(
        repository,
        dispatcher,
        poll_interval=60.0,
        recovery_interval=60.0,
    )

    await worker.start()
    await wait_until(lambda: repository.claim_calls > 0)
    await asyncio.wait_for(worker.stop(), timeout=0.1)


@pytest.mark.asyncio
async def test_wait_surfaces_unexpected_processing_loop_failure() -> None:
    class FailingWorker(TelegramUpdateWorker):
        async def _processing_loop(self) -> None:
            raise RuntimeError("processing loop crashed")

    repository = FakeTelegramUpdateInboxRepository()
    worker = FailingWorker(
        bot=cast(Bot, FakeBot()),
        dispatcher=cast(Dispatcher, FakeDispatcher()),
        update_inbox_repository=repository,
        concurrency=1,
        poll_interval=1.0,
        claim_timeout=timedelta(minutes=5),
        recovery_interval=60.0,
        shutdown_grace_period=0.1,
    )

    await worker.start()
    try:
        with pytest.raises(RuntimeError, match="failed unexpectedly") as error_info:
            await worker.wait()
    finally:
        await worker.stop()

    assert isinstance(error_info.value.__cause__, RuntimeError)
    assert str(error_info.value.__cause__) == "processing loop crashed"
