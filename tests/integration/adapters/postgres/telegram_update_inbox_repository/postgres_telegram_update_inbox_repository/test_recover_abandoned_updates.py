from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import TelegramUpdateInboxModel
from productivity_bot.application.ports import RecoveredUpdates
from tests.integration.adapters.postgres.telegram_update_inbox_repository.helpers import (
    load_update,
    make_repository,
)


@pytest.mark.asyncio
async def test_recover_abandoned_updates_retries_only_safe_attempts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)

    await repository.insert_update(500, {"update_id": 500})
    safe_attempt = await repository.claim_pending_update()
    assert safe_attempt is not None

    await repository.insert_update(501, {"update_id": 501})
    uncertain_attempt = await repository.claim_pending_update()
    assert uncertain_attempt is not None
    await repository.mark_external_mutation_started(
        501,
        uncertain_attempt.attempt_count,
    )

    await repository.insert_update(502, {"update_id": 502})
    fresh_attempt = await repository.claim_pending_update()
    assert fresh_attempt is not None

    abandoned_at = datetime.now(UTC) - timedelta(hours=2)
    async with session_factory.begin() as session:
        updates = await session.scalars(
            select(TelegramUpdateInboxModel).where(
                TelegramUpdateInboxModel.update_id.in_((500, 501))
            )
        )
        for update in updates:
            update.claimed_at = abandoned_at

    result = await repository.recover_abandoned_updates(
        datetime.now(UTC) - timedelta(hours=1)
    )

    assert result == RecoveredUpdates(
        retried_count=1,
        uncertain_count=1,
    )
    safe_update = await load_update(session_factory, 500)
    uncertain_update = await load_update(session_factory, 501)
    fresh_update = await load_update(session_factory, 502)
    assert safe_update.status == "pending"
    assert safe_update.last_error == (
        "Processing claim expired before an external mutation started"
    )
    assert uncertain_update.status == "uncertain"
    assert uncertain_update.last_error == (
        "Processing claim expired after an external mutation may have started"
    )
    assert fresh_update.status == "processing"
