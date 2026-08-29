import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import TelegramUpdateInboxModel
from tests.integration.adapters.postgres.telegram_update_inbox_repository.helpers import (
    load_update,
    make_repository,
)


@pytest.mark.asyncio
async def test_insert_update_returns_true_and_duplicate_preserves_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    original_payload = {"update_id": 100, "message": {"text": "Original"}}

    assert await repository.insert_update(100, original_payload) is True
    original_update = await load_update(session_factory, 100)

    assert await repository.insert_update(100, {"update_id": 100}) is False
    duplicate_update = await load_update(session_factory, 100)

    assert duplicate_update.payload == original_payload
    assert duplicate_update.status == "pending"
    assert duplicate_update.attempt_count == 0
    assert duplicate_update.received_at == original_update.received_at


@pytest.mark.asyncio
async def test_concurrent_insert_update_deduplicates_update_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    payloads: tuple[dict[str, Any], ...] = (
        {"update_id": 101, "source": "first"},
        {"update_id": 101, "source": "second"},
    )

    results = await asyncio.gather(
        *(repository.insert_update(101, payload) for payload in payloads)
    )

    assert sorted(results) == [False, True]
    winning_payload = payloads[results.index(True)]
    update = await load_update(session_factory, 101)
    assert update.payload == winning_payload
    async with session_factory() as session:
        row_count = await session.scalar(
            select(func.count()).select_from(TelegramUpdateInboxModel)
        )
    assert row_count == 1
