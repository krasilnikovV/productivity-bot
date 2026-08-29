import asyncio

import pytest

from productivity_bot.application.ports import ClaimedUpdate
from tests.unit.entrypoints.telegram.update_worker.helpers import (
    _DEADLOCK_TIMEOUT,
    ControlledDispatcher,
    make_dispatcher,
    make_repository,
    make_worker,
    wait_for_event,
)


@pytest.mark.asyncio
async def test_claim_finishing_after_shutdown_is_rescheduled_without_dispatch() -> None:
    repository = make_repository()
    claim_started = asyncio.Event()
    claim_release = asyncio.Event()

    async def claim_pending_update() -> ClaimedUpdate:
        claim_started.set()
        await claim_release.wait()
        return ClaimedUpdate(42, {"update_id": 42}, 1)

    repository.claim_pending_update.side_effect = claim_pending_update
    dispatcher = make_dispatcher()
    worker = make_worker(repository, dispatcher, shutdown_grace_period=0.2)

    await worker.start()
    await claim_started.wait()
    stop_task = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    claim_release.set()
    await stop_task

    repository.mark_external_mutation_started.assert_not_awaited()
    dispatcher.feed_update.assert_not_awaited()
    assert repository.reschedule.await_args.args[:2] == (42, 1)
    assert "shutdown" in repository.reschedule.await_args.args[2]


@pytest.mark.asyncio
async def test_shutdown_allows_in_flight_update_to_finish_within_grace() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    release = asyncio.Event()
    dispatch_started = asyncio.Event()
    dispatcher = ControlledDispatcher(release, started=dispatch_started)
    worker = make_worker(repository, dispatcher, shutdown_grace_period=0.2)

    await worker.start()
    await wait_for_event(dispatch_started)
    stop_task = asyncio.create_task(worker.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()

    release.set()
    await stop_task
    repository.mark_succeeded.assert_awaited_once_with(42, 1)
    assert dispatcher.cancelled_calls == 0


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_update_after_grace() -> None:
    repository = make_repository([ClaimedUpdate(42, {"update_id": 42}, 1)])
    dispatch_started = asyncio.Event()
    dispatcher = ControlledDispatcher(
        asyncio.Event(),
        starts_mutation=True,
        started=dispatch_started,
    )
    worker = make_worker(repository, dispatcher, shutdown_grace_period=0.01)

    await worker.start()
    await wait_for_event(dispatch_started)
    await asyncio.wait_for(worker.stop(), timeout=_DEADLOCK_TIMEOUT)

    assert dispatcher.cancelled_calls == 1
    repository.mark_external_mutation_started.assert_awaited_once_with(42, 1)
    repository.mark_succeeded.assert_not_awaited()
    repository.mark_uncertain.assert_not_awaited()


@pytest.mark.asyncio
async def test_idle_shutdown_does_not_wait_for_poll_interval() -> None:
    claim_started = asyncio.Event()
    repository = make_repository(claim_started=claim_started)
    dispatcher = make_dispatcher()
    worker = make_worker(
        repository,
        dispatcher,
        poll_interval=60.0,
        recovery_interval=60.0,
    )

    await worker.start()
    await wait_for_event(claim_started)
    await asyncio.wait_for(worker.stop(), timeout=_DEADLOCK_TIMEOUT)
