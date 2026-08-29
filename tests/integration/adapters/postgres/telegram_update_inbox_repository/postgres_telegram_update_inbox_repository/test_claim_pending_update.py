import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import TelegramUpdateInboxModel
from productivity_bot.application.ports import ClaimedUpdate
from tests.integration.adapters.postgres.telegram_update_inbox_repository.helpers import (
    add_update,
    load_update,
    make_repository,
)


@pytest.mark.asyncio
async def test_claim_pending_update_skips_future_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await add_update(
        session_factory,
        update_id=200,
        payload={"update_id": 200},
        available_at=datetime.now(UTC) + timedelta(days=1),
    )

    assert await repository.claim_pending_update() is None
    update = await load_update(session_factory, 200)
    assert update.status == "pending"
    assert update.attempt_count == 0


@pytest.mark.asyncio
async def test_claim_pending_update_marks_ready_update_processing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    payload = {"update_id": 201, "message": {"text": "Ready"}}
    await add_update(
        session_factory,
        update_id=201,
        payload=payload,
        last_error="previous attempt",
    )

    claimed_update = await repository.claim_pending_update()

    assert claimed_update == ClaimedUpdate(
        update_id=201,
        payload=payload,
        attempt_count=1,
    )
    update = await load_update(session_factory, 201)
    assert update.status == "processing"
    assert update.attempt_count == 1
    assert update.claimed_at is not None
    assert update.external_mutation_started_at is None
    assert update.last_error is None
    assert await repository.claim_pending_update() is None


@pytest.mark.asyncio
async def test_claim_pending_update_skips_row_locked_by_other_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(202, {"update_id": 202})
    await repository.insert_update(203, {"update_id": 203})

    async with session_factory.begin() as locking_session:
        locked_update = await locking_session.scalar(
            select(TelegramUpdateInboxModel)
            .where(TelegramUpdateInboxModel.update_id == 202)
            .with_for_update()
        )
        assert locked_update is not None

        claimed_update = await asyncio.wait_for(
            repository.claim_pending_update(),
            timeout=1,
        )

        assert claimed_update == ClaimedUpdate(
            update_id=203,
            payload={"update_id": 203},
            attempt_count=1,
        )
        locked_row = await load_update(session_factory, 202)
        assert locked_row.status == "pending"
        assert locked_row.attempt_count == 0

    reclaimed_update = await repository.claim_pending_update()

    assert reclaimed_update == ClaimedUpdate(
        update_id=202,
        payload={"update_id": 202},
        attempt_count=1,
    )
