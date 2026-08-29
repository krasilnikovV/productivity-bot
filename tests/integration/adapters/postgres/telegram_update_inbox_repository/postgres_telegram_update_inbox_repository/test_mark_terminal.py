import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres import TelegramUpdateInboxModel
from productivity_bot.application.ports import UpdateTransitionError
from tests.integration.adapters.postgres.telegram_update_inbox_repository.helpers import (
    load_update,
    make_repository,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_status", "error"),
    [
        pytest.param("succeeded", None, id="succeeded"),
        pytest.param("failed", "known failure", id="failed"),
        pytest.param("uncertain", "external outcome unknown", id="uncertain"),
    ],
)
async def test_mark_terminal_transitions_from_processing(
    session_factory: async_sessionmaker[AsyncSession],
    target_status: str,
    error: str | None,
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(300, {"update_id": 300})
    claimed_update = await repository.claim_pending_update()
    assert claimed_update is not None
    async with session_factory.begin() as session:
        update = await session.get(TelegramUpdateInboxModel, 300)
        assert update is not None
        update.last_error = "processing detail"

    if target_status == "succeeded":
        await repository.mark_succeeded(300, claimed_update.attempt_count)
    elif target_status == "failed":
        assert error is not None
        await repository.mark_failed(300, claimed_update.attempt_count, error)
    else:
        assert error is not None
        await repository.mark_uncertain(300, claimed_update.attempt_count, error)

    update = await load_update(session_factory, 300)
    assert update.status == target_status
    assert update.last_error == error


@pytest.mark.asyncio
async def test_mark_terminal_rejects_pending_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(301, {"update_id": 301})

    with pytest.raises(UpdateTransitionError):
        await repository.mark_succeeded(301, 1)

    update = await load_update(session_factory, 301)
    assert update.status == "pending"
    assert update.attempt_count == 0
    assert update.last_error is None


@pytest.mark.asyncio
async def test_mark_terminal_rejects_repeated_transition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(302, {"update_id": 302})
    claimed_update = await repository.claim_pending_update()
    assert claimed_update is not None
    await repository.mark_failed(
        302,
        claimed_update.attempt_count,
        "original failure",
    )

    with pytest.raises(UpdateTransitionError):
        await repository.mark_uncertain(
            302,
            claimed_update.attempt_count,
            "replacement failure",
        )

    update = await load_update(session_factory, 302)
    assert update.status == "failed"
    assert update.attempt_count == 1
    assert update.last_error == "original failure"


@pytest.mark.asyncio
async def test_mark_terminal_rejects_unknown_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)

    with pytest.raises(UpdateTransitionError):
        await repository.mark_succeeded(999, 1)
