from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.application.ports import ClaimedUpdate
from tests.integration.adapters.postgres.telegram_update_inbox_repository.helpers import (
    load_update,
    make_repository,
)


@pytest.mark.asyncio
async def test_reschedule_returns_safe_attempt_to_pending(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(400, {"update_id": 400})
    claimed_update = await repository.claim_pending_update()
    assert claimed_update is not None
    retry_at = datetime.now(UTC) + timedelta(hours=1)

    await repository.reschedule(
        400,
        claimed_update.attempt_count,
        "connection failed before request was sent",
        retry_at,
    )

    update = await load_update(session_factory, 400)
    assert update.status == "pending"
    assert update.available_at == retry_at
    assert update.last_error == "connection failed before request was sent"
    assert update.attempt_count == 1
    assert await repository.claim_pending_update() is None


@pytest.mark.asyncio
async def test_reschedule_allows_known_safe_retry_after_mutation_phase_started(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(401, {"update_id": 401})
    claimed_update = await repository.claim_pending_update()
    assert claimed_update is not None
    await repository.mark_external_mutation_started(
        401,
        claimed_update.attempt_count,
    )

    retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await repository.reschedule(
        401,
        claimed_update.attempt_count,
        "transport confirmed that the request was not sent",
        retry_at,
    )

    update = await load_update(session_factory, 401)
    assert update.status == "pending"
    assert update.available_at == retry_at
    assert update.last_error == "transport confirmed that the request was not sent"
    assert update.external_mutation_started_at is not None

    reclaimed_update = await repository.claim_pending_update()

    assert reclaimed_update == ClaimedUpdate(
        update_id=401,
        payload={"update_id": 401},
        attempt_count=claimed_update.attempt_count + 1,
    )
    update = await load_update(session_factory, 401)
    assert update.external_mutation_started_at is None
