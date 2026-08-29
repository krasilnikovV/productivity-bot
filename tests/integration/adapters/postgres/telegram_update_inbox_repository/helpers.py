from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import (
    PostgresTelegramUpdateInboxRepository,
    TelegramUpdateInboxModel,
)
from productivity_bot.application.ports import TelegramUpdateInboxRepository


def make_repository(
    session_factory: async_sessionmaker[AsyncSession],
) -> TelegramUpdateInboxRepository:
    repository: TelegramUpdateInboxRepository = (
        PostgresTelegramUpdateInboxRepository(session_factory)
    )
    return repository


async def load_update(
    session_factory: async_sessionmaker[AsyncSession],
    update_id: int,
) -> TelegramUpdateInboxModel:
    async with session_factory() as session:
        update = await session.get(TelegramUpdateInboxModel, update_id)
        assert update is not None
        session.expunge(update)
        return update


async def add_update(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    update_id: int,
    payload: dict[str, Any],
    available_at: datetime | None = None,
    last_error: str | None = None,
) -> None:
    values: dict[str, Any] = {
        "update_id": update_id,
        "payload": payload,
        "last_error": last_error,
    }
    if available_at is not None:
        values["available_at"] = available_at

    async with session_factory.begin() as session:
        session.add(TelegramUpdateInboxModel(**values))
