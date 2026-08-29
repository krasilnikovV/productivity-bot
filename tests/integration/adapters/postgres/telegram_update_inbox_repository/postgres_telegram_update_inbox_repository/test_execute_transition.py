from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import TelegramUpdateInboxModel
from productivity_bot.application.ports import UpdateTransitionError
from tests.integration.adapters.postgres.telegram_update_inbox_repository.helpers import (
    load_update,
    make_repository,
)


@pytest.mark.asyncio
async def test_reclaimed_attempt_rejects_transition_from_old_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(503, {"update_id": 503})
    old_attempt = await repository.claim_pending_update()
    assert old_attempt is not None

    async with session_factory.begin() as session:
        update = await session.get(TelegramUpdateInboxModel, 503)
        assert update is not None
        update.claimed_at = datetime.now(UTC) - timedelta(hours=2)

    await repository.recover_abandoned_updates(datetime.now(UTC) - timedelta(hours=1))
    new_attempt = await repository.claim_pending_update()
    assert new_attempt is not None
    assert new_attempt.attempt_count == old_attempt.attempt_count + 1

    with pytest.raises(UpdateTransitionError):
        await repository.mark_succeeded(503, old_attempt.attempt_count)

    update = await load_update(session_factory, 503)
    assert update.status == "processing"
    assert update.attempt_count == new_attempt.attempt_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transition",
    [
        pytest.param("mark_external_mutation_started", id="mutation-marker"),
        pytest.param("reschedule", id="reschedule"),
    ],
)
async def test_reclaimed_attempt_rejects_nonterminal_transition_from_old_worker(
    session_factory: async_sessionmaker[AsyncSession],
    transition: str,
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(504, {"update_id": 504})
    old_attempt = await repository.claim_pending_update()
    assert old_attempt is not None

    async with session_factory.begin() as session:
        update = await session.get(TelegramUpdateInboxModel, 504)
        assert update is not None
        update.claimed_at = datetime.now(UTC) - timedelta(hours=2)

    await repository.recover_abandoned_updates(datetime.now(UTC) - timedelta(hours=1))
    new_attempt = await repository.claim_pending_update()
    assert new_attempt is not None

    with pytest.raises(UpdateTransitionError):
        if transition == "mark_external_mutation_started":
            await repository.mark_external_mutation_started(
                504,
                old_attempt.attempt_count,
            )
        else:
            await repository.reschedule(
                504,
                old_attempt.attempt_count,
                "stale worker retry",
                datetime.now(UTC) + timedelta(hours=1),
            )

    update = await load_update(session_factory, 504)
    assert update.status == "processing"
    assert update.attempt_count == new_attempt.attempt_count
    assert update.external_mutation_started_at is None
    assert update.last_error is None
