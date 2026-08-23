import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

from productivity_bot.adapters.postgres import (
    Base,
    PostgresTelegramUpdateInboxRepository,
    TelegramUpdateInboxModel,
)
from productivity_bot.application.ports import (
    ClaimedUpdate,
    RecoveredUpdates,
    TelegramUpdateInboxRepository,
    UpdateTransitionError,
)

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/productivity_bot"
)


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    schema_name = f"test_telegram_update_inbox_{uuid4().hex}"
    engine = create_async_engine(
        os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        connect_args={"server_settings": {"search_path": schema_name}},
    )
    schema_created = False

    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema_name))
            await connection.run_sync(Base.metadata.create_all)
        schema_created = True
        yield engine
    finally:
        try:
            if schema_created:
                async with engine.begin() as connection:
                    await connection.execute(DropSchema(schema_name, cascade=True))
        finally:
            await engine.dispose()


@pytest.fixture
def session_factory(
    postgres_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(postgres_engine, expire_on_commit=False)


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
async def test_concurrent_claims_return_different_updates_and_release_locks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = make_repository(session_factory)
    await repository.insert_update(202, {"update_id": 202})
    await repository.insert_update(203, {"update_id": 203})

    claimed_results = await asyncio.gather(
        repository.claim_pending_update(),
        repository.claim_pending_update(),
    )

    claimed_updates = [update for update in claimed_results if update is not None]
    assert len(claimed_updates) == 2
    claimed_ids = {update.update_id for update in claimed_updates}
    assert claimed_ids == {202, 203}

    async with session_factory.begin() as session:
        locked_updates = await session.scalars(
            select(TelegramUpdateInboxModel)
            .where(TelegramUpdateInboxModel.update_id.in_(claimed_ids))
            .with_for_update(nowait=True)
        )
        assert {update.update_id for update in locked_updates} == claimed_ids

    for update_id in claimed_ids:
        update = await load_update(session_factory, update_id)
        assert update.status == "processing"
        assert update.attempt_count == 1


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

    retry_at = datetime.now(UTC) + timedelta(minutes=5)
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
