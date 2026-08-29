import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import call, patch

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from productivity_bot.application.ports import (
    ClaimedUpdate,
    RecoveredUpdates,
    TaskMutationConfirmedError,
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
    TaskReadError,
    UpdateTransitionError,
)
from tests.unit.entrypoints.telegram.update_worker.helpers import (
    ControlledDispatcher,
    fixed_datetime,
    make_bot,
    make_dispatcher,
    make_repository,
    make_worker,
    signal,
    wait_for_event,
)


@pytest.mark.asyncio
async def test_success_executes_telegram_method_and_marks_attempt_succeeded() -> None:
    claimed_update = ClaimedUpdate(42, {"update_id": 42}, 3)
    repository = make_repository([claimed_update])
    succeeded = asyncio.Event()
    repository.mark_succeeded.side_effect = signal(succeeded)
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = make_dispatcher(result=telegram_method, starts_mutation=True)
    bot = make_bot()
    worker = make_worker(repository, dispatcher, bot=bot)

    await worker.start()
    await wait_for_event(succeeded)
    await worker.stop()

    repository.mark_external_mutation_started.assert_awaited_once_with(42, 3)
    repository.mark_succeeded.assert_awaited_once_with(42, 3)
    dispatcher.feed_update.assert_awaited_once()
    assert dispatcher.feed_update.await_args.args[1].update_id == 42
    bot.assert_awaited_once_with(telegram_method)
    repository.mark_failed.assert_not_awaited()
    repository.mark_uncertain.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_durable_payload_is_marked_failed_without_dispatch() -> None:
    repository = make_repository([ClaimedUpdate(42, {}, 1)])
    failed = asyncio.Event()
    repository.mark_failed.side_effect = signal(failed)
    dispatcher = make_dispatcher()
    worker = make_worker(repository, dispatcher)

    await worker.start()
    await wait_for_event(failed)
    await worker.stop()

    update_id, attempt_count, error = repository.mark_failed.await_args.args
    assert (update_id, attempt_count) == (42, 1)
    assert error.startswith("Invalid durable Telegram update payload:")
    assert len(error) <= 1_000
    repository.mark_external_mutation_started.assert_not_awaited()
    dispatcher.feed_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_marker_error_is_marked_uncertain() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 2)])
    uncertain = asyncio.Event()
    repository.mark_uncertain.side_effect = signal(uncertain)
    dispatcher = make_dispatcher(
        error=RuntimeError("remote outcome unknown"),
        starts_mutation=True,
    )
    worker = make_worker(repository, dispatcher)

    await worker.start()
    await wait_for_event(uncertain)
    await worker.stop()

    repository.mark_external_mutation_started.assert_awaited_once_with(42, 2)
    repository.mark_succeeded.assert_not_awaited()
    uncertain_args = repository.mark_uncertain.await_args.args
    assert uncertain_args[:2] == (42, 2)
    assert "remote outcome unknown" in uncertain_args[2]


@pytest.mark.asyncio
async def test_telegram_method_error_leaves_attempt_succeeded() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 2)])
    succeeded = asyncio.Event()
    repository.mark_succeeded.side_effect = signal(succeeded)
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = make_dispatcher(result=telegram_method, starts_mutation=True)
    bot = make_bot(
        error=TelegramNetworkError(
            method=telegram_method,
            message="connection lost",
        )
    )
    worker = make_worker(repository, dispatcher, bot=bot)

    await worker.start()
    await wait_for_event(succeeded)
    await worker.stop()

    bot.assert_awaited_once_with(telegram_method)
    repository.mark_succeeded.assert_awaited_once_with(42, 2)
    repository.mark_uncertain.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_is_persisted_before_telegram_reply() -> None:
    events: list[str] = []
    reply_sent = asyncio.Event()
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    repository.mark_succeeded.side_effect = lambda *_: events.append("succeeded")
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = make_dispatcher(result=telegram_method, starts_mutation=True)
    worker = make_worker(
        repository,
        dispatcher,
        bot=make_bot(events=events, reply_sent=reply_sent),
    )

    await worker.start()
    await wait_for_event(reply_sent)
    await worker.stop()

    assert events == ["succeeded", "reply"]


@pytest.mark.asyncio
async def test_confirmed_mutation_retries_succeeded_transition_before_reply() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    reply_sent = asyncio.Event()

    def mark_succeeded(*_: object) -> None:
        if repository.mark_succeeded.await_count == 1:
            raise RuntimeError("database unavailable")

    repository.mark_succeeded.side_effect = mark_succeeded
    telegram_method = SendMessage(chat_id=123, text="Task captured")
    dispatcher = make_dispatcher(result=telegram_method, starts_mutation=True)
    bot = make_bot(reply_sent=reply_sent)
    worker = make_worker(repository, dispatcher, bot=bot)

    await worker.start()
    await wait_for_event(reply_sent)
    await worker.stop()

    assert repository.mark_succeeded.await_args_list == [
        call(42, 1),
        call(42, 1),
    ]
    repository.mark_uncertain.assert_not_awaited()
    bot.assert_awaited_once_with(telegram_method)


@pytest.mark.asyncio
async def test_unsupported_update_succeeds_without_mutation_marker() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    succeeded = asyncio.Event()
    repository.mark_succeeded.side_effect = signal(succeeded)
    worker = make_worker(repository, make_dispatcher())

    await worker.start()
    await wait_for_event(succeeded)
    await worker.stop()

    repository.mark_external_mutation_started.assert_not_awaited()
    repository.mark_succeeded.assert_awaited_once_with(42, 1)


@pytest.mark.asyncio
async def test_pre_mutation_processing_error_is_terminal_failed() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    failed = asyncio.Event()
    repository.mark_failed.side_effect = signal(failed)
    worker = make_worker(
        repository,
        make_dispatcher(error=ValueError("deterministic validation failed")),
    )

    await worker.start()
    await wait_for_event(failed)
    await worker.stop()

    repository.mark_external_mutation_started.assert_not_awaited()
    assert repository.mark_failed.await_args.args[:2] == (42, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatcher_error", "starts_mutation", "expected_transition"),
    [
        (
            TaskReadError("read temporarily unavailable", retryable=True),
            False,
            "reschedule",
        ),
        (TaskReadError("read rejected", retryable=False), False, "failed"),
        (
            TaskMutationNotAppliedError("not sent", retryable=True),
            True,
            "reschedule",
        ),
        (
            TaskMutationNotAppliedError("rejected", retryable=False),
            True,
            "failed",
        ),
        (TaskMutationOutcomeUnknownError("unknown"), True, "uncertain"),
    ],
    ids=[
        "retryable-read-error",
        "non-retryable-read-error",
        "retryable-not-applied-mutation",
        "non-retryable-not-applied-mutation",
        "unknown-mutation-outcome",
    ],
)
async def test_typed_outcomes_use_the_claimed_update_and_attempt_for_each_transition(
    dispatcher_error: Exception | None,
    starts_mutation: bool,
    expected_transition: str,
) -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 7)])
    transition_completed = asyncio.Event()
    transition_methods = {
        "succeeded": repository.mark_succeeded,
        "failed": repository.mark_failed,
        "uncertain": repository.mark_uncertain,
        "reschedule": repository.reschedule,
    }
    transition_methods[expected_transition].side_effect = signal(transition_completed)
    worker = make_worker(
        repository,
        make_dispatcher(
            error=dispatcher_error,
            starts_mutation=starts_mutation,
        ),
    )

    await worker.start()
    await wait_for_event(transition_completed)
    await worker.stop()

    assert {
        name: method.await_count > 0 for name, method in transition_methods.items()
    } == {name: name == expected_transition for name in transition_methods}
    assert transition_methods[expected_transition].await_args.args[:2] == (42, 7)
    if starts_mutation:
        repository.mark_external_mutation_started.assert_awaited_once_with(42, 7)
    else:
        repository.mark_external_mutation_started.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatcher_error", "expected_transition"),
    [
        pytest.param(
            TaskMutationNotAppliedError("rejected", retryable=False),
            "failed",
            id="terminal-rejection",
        ),
        pytest.param(
            TaskMutationNotAppliedError("not sent", retryable=True),
            "reschedule",
            id="safe-retry",
        ),
        pytest.param(
            TaskMutationOutcomeUnknownError("unknown"),
            "uncertain",
            id="unknown-mutation-outcome",
        ),
    ],
)
async def test_processing_transition_is_retried_with_the_same_attempt(
    dispatcher_error: Exception,
    expected_transition: str,
) -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    transition_completed = asyncio.Event()
    transition_methods = {
        "failed": repository.mark_failed,
        "reschedule": repository.reschedule,
        "uncertain": repository.mark_uncertain,
    }
    transition = transition_methods[expected_transition]

    def persist_transition(*_: object) -> None:
        if transition.await_count == 1:
            raise RuntimeError("database unavailable")
        transition_completed.set()

    transition.side_effect = persist_transition
    worker = make_worker(
        repository,
        make_dispatcher(
            error=dispatcher_error,
            starts_mutation=True,
        ),
    )

    await worker.start()
    await wait_for_event(transition_completed)
    await worker.stop()

    repository.mark_external_mutation_started.assert_awaited_once_with(42, 1)
    assert transition.await_count == 2
    assert transition.await_args_list[0] == transition.await_args_list[1]
    for name, method in transition_methods.items():
        if name != expected_transition:
            method.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_transition_is_not_retried_and_worker_processes_next_update() -> None:
    repository = make_repository(
        [
            ClaimedUpdate(42, {"update_id": 42}, 1),
            ClaimedUpdate(43, {"update_id": 43}, 1),
        ]
    )
    next_update_succeeded = asyncio.Event()

    def mark_succeeded(update_id: int, _: int) -> None:
        if update_id == 42:
            raise UpdateTransitionError("attempt has been superseded")
        next_update_succeeded.set()

    repository.mark_succeeded.side_effect = mark_succeeded
    dispatcher = make_dispatcher()
    worker = make_worker(repository, dispatcher)

    await worker.start()
    await wait_for_event(next_update_succeeded)
    await worker.stop()

    assert repository.mark_succeeded.await_args_list == [call(42, 1), call(43, 1)]
    assert dispatcher.feed_update.await_count == 2


@pytest.mark.asyncio
async def test_confirmed_mutation_with_invalid_result_succeeds_without_reply() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    succeeded = asyncio.Event()
    repository.mark_succeeded.side_effect = signal(succeeded)
    bot = make_bot()
    worker = make_worker(
        repository,
        make_dispatcher(
            error=TaskMutationConfirmedError("invalid response"),
            starts_mutation=True,
        ),
        bot=bot,
    )

    await worker.start()
    await wait_for_event(succeeded)
    await worker.stop()

    repository.mark_succeeded.assert_awaited_once_with(42, 1)
    repository.mark_uncertain.assert_not_awaited()
    bot.assert_not_awaited()


@pytest.mark.asyncio
async def test_marker_failure_reschedules_without_dispatching() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 4)])
    rescheduled = asyncio.Event()
    repository.reschedule.side_effect = signal(rescheduled)
    repository.mark_external_mutation_started.side_effect = RuntimeError(
        "database unavailable"
    )
    dispatcher = make_dispatcher(starts_mutation=True)
    worker = make_worker(repository, dispatcher, poll_interval=0.05)
    processing_time = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    with patch(
        "productivity_bot.entrypoints.telegram.update_worker.datetime",
        fixed_datetime(processing_time),
    ):
        await worker.start()
        await wait_for_event(rescheduled)
        await worker.stop()

    update_id, attempt_count, error, available_at = (
        repository.reschedule.await_args.args
    )
    assert (update_id, attempt_count) == (42, 4)
    assert "database unavailable" in error
    assert available_at == processing_time + timedelta(seconds=0.05)
    dispatcher.feed_update.assert_awaited_once()
    processing_attempt = dispatcher.feed_update.await_args.kwargs["processing_attempt"]
    assert processing_attempt.external_mutation_started is False


@pytest.mark.asyncio
async def test_initial_and_periodic_recovery_use_claim_timeout() -> None:
    repository = make_repository()
    dispatcher = make_dispatcher()
    claim_timeout = timedelta(seconds=30)
    periodic_recovery_completed = asyncio.Event()

    def recover_abandoned_updates(_: datetime) -> RecoveredUpdates:
        if repository.recover_abandoned_updates.await_count == 2:
            periodic_recovery_completed.set()
        return RecoveredUpdates(retried_count=0, uncertain_count=0)

    repository.recover_abandoned_updates.side_effect = recover_abandoned_updates
    worker = make_worker(
        repository,
        dispatcher,
        claim_timeout=claim_timeout,
        recovery_interval=0.01,
    )
    recovery_time = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    with patch(
        "productivity_bot.entrypoints.telegram.update_worker.datetime",
        fixed_datetime(recovery_time),
    ):
        await worker.start()
        await wait_for_event(periodic_recovery_completed)
        await worker.stop()

    claimed_before_values = [
        call.args[0] for call in repository.recover_abandoned_updates.await_args_list
    ]
    assert claimed_before_values
    assert all(
        claimed_before == recovery_time - claim_timeout
        for claimed_before in claimed_before_values
    )


@pytest.mark.asyncio
async def test_initial_recovery_failure_prevents_processing_loops_from_starting() -> (
    None
):
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    repository.recover_abandoned_updates.side_effect = RuntimeError(
        "database unavailable"
    )
    dispatcher = make_dispatcher()
    worker = make_worker(repository, dispatcher)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker.start()

    await worker.stop()
    repository.claim_pending_update.assert_not_awaited()
    dispatcher.feed_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_periodic_recovery_and_claim_errors_do_not_stop_worker() -> None:
    repository = make_repository(
        [ClaimedUpdate(42, {"update_id": 42}, 1)],
        claim_errors=[RuntimeError("claim failed")],
    )

    periodic_recovery_completed = asyncio.Event()

    def recover_abandoned_updates(_: datetime) -> RecoveredUpdates:
        if repository.recover_abandoned_updates.await_count == 2:
            raise RuntimeError("recovery failed")
        if repository.recover_abandoned_updates.await_count == 3:
            periodic_recovery_completed.set()
        return RecoveredUpdates(retried_count=0, uncertain_count=0)

    repository.recover_abandoned_updates.side_effect = recover_abandoned_updates
    dispatcher = make_dispatcher()
    succeeded = asyncio.Event()
    repository.mark_succeeded.side_effect = signal(succeeded)
    worker = make_worker(
        repository,
        dispatcher,
        poll_interval=0.005,
        recovery_interval=0.005,
    )

    await worker.start()
    await wait_for_event(succeeded)
    await wait_for_event(periodic_recovery_completed)
    await worker.stop()

    repository.mark_succeeded.assert_awaited_once_with(42, 1)


@pytest.mark.asyncio
async def test_configured_concurrency_bounds_feed_update_calls() -> None:
    repository = make_repository(
        [
            ClaimedUpdate(update_id, {"update_id": update_id}, 1)
            for update_id in range(3)
        ]
    )
    release = asyncio.Event()
    both_dispatches_started = asyncio.Event()
    dispatcher = ControlledDispatcher(
        release,
        starts_mutation=True,
        started=both_dispatches_started,
        expected_started_calls=2,
    )
    succeeded = asyncio.Event()

    def mark_succeeded(*_: object) -> None:
        if repository.mark_succeeded.await_count == 3:
            succeeded.set()

    repository.mark_succeeded.side_effect = mark_succeeded
    worker = make_worker(repository, dispatcher, concurrency=2)

    await worker.start()
    await wait_for_event(both_dispatches_started)

    assert dispatcher.maximum_active_calls == 2
    assert dispatcher.feed_call_count == 2

    release.set()
    await wait_for_event(succeeded)
    await worker.stop()
    assert dispatcher.maximum_active_calls == 2
