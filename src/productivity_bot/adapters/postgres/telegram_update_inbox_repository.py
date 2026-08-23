from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from productivity_bot.adapters.postgres.models import TelegramUpdateInboxModel
from productivity_bot.application.ports import (
    ClaimedUpdate,
    RecoveredUpdates,
    UpdateTransitionError,
)

type _TerminalStatus = Literal["succeeded", "failed", "uncertain"]

_SAFE_RECOVERY_ERROR = "Processing claim expired before an external mutation started"
_UNCERTAIN_RECOVERY_ERROR = (
    "Processing claim expired after an external mutation may have started"
)


class PostgresTelegramUpdateInboxRepository:
    """Persist and claim Telegram updates through PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def insert_update(
        self,
        update_id: int,
        payload: dict[str, Any],
    ) -> bool:
        statement = (
            insert(TelegramUpdateInboxModel)
            .values(update_id=update_id, payload=payload)
            .on_conflict_do_nothing(
                index_elements=[TelegramUpdateInboxModel.update_id],
            )
            .returning(TelegramUpdateInboxModel.update_id)
        )

        async with self._session_factory.begin() as session:
            result = await session.execute(statement)
            inserted_update_id = result.scalar_one_or_none()

        return inserted_update_id is not None

    async def claim_pending_update(self) -> ClaimedUpdate | None:
        statement = (
            select(TelegramUpdateInboxModel)
            .where(
                TelegramUpdateInboxModel.status == "pending",
                TelegramUpdateInboxModel.available_at <= func.now(),
            )
            .order_by(
                TelegramUpdateInboxModel.available_at,
                TelegramUpdateInboxModel.received_at,
                TelegramUpdateInboxModel.update_id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        claimed_update: ClaimedUpdate | None = None

        async with self._session_factory.begin() as session:
            result = await session.execute(statement)
            update_model = result.scalar_one_or_none()
            if update_model is not None:
                update_model.status = "processing"
                update_model.attempt_count += 1
                update_model.last_error = None
                update_model.claimed_at = datetime.now(UTC)
                update_model.external_mutation_started_at = None
                claimed_update = ClaimedUpdate(
                    update_id=update_model.update_id,
                    payload=update_model.payload,
                    attempt_count=update_model.attempt_count,
                )

        return claimed_update

    async def mark_external_mutation_started(
        self,
        update_id: int,
        attempt_count: int,
    ) -> None:
        statement = (
            update(TelegramUpdateInboxModel)
            .where(
                TelegramUpdateInboxModel.update_id == update_id,
                TelegramUpdateInboxModel.status == "processing",
                TelegramUpdateInboxModel.attempt_count == attempt_count,
                TelegramUpdateInboxModel.external_mutation_started_at.is_(None),
            )
            .values(external_mutation_started_at=func.now())
            .returning(TelegramUpdateInboxModel.update_id)
        )

        await self._execute_transition(
            statement,
            update_id=update_id,
            transition="mark external mutation started",
        )

    async def reschedule(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
        available_at: datetime,
    ) -> None:
        statement = (
            update(TelegramUpdateInboxModel)
            .where(
                TelegramUpdateInboxModel.update_id == update_id,
                TelegramUpdateInboxModel.status == "processing",
                TelegramUpdateInboxModel.attempt_count == attempt_count,
            )
            .values(
                status="pending",
                last_error=error,
                available_at=available_at,
            )
            .returning(TelegramUpdateInboxModel.update_id)
        )

        await self._execute_transition(
            statement,
            update_id=update_id,
            transition="reschedule",
        )

    async def mark_succeeded(self, update_id: int, attempt_count: int) -> None:
        await self._mark_terminal(
            update_id,
            attempt_count,
            status="succeeded",
            error=None,
        )

    async def mark_failed(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        await self._mark_terminal(
            update_id,
            attempt_count,
            status="failed",
            error=error,
        )

    async def mark_uncertain(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None:
        await self._mark_terminal(
            update_id,
            attempt_count,
            status="uncertain",
            error=error,
        )

    async def recover_abandoned_updates(
        self,
        claimed_before: datetime,
    ) -> RecoveredUpdates:
        safe_retry_statement = (
            update(TelegramUpdateInboxModel)
            .where(
                TelegramUpdateInboxModel.status == "processing",
                TelegramUpdateInboxModel.claimed_at <= claimed_before,
                TelegramUpdateInboxModel.external_mutation_started_at.is_(None),
            )
            .values(
                status="pending",
                available_at=func.now(),
                last_error=_SAFE_RECOVERY_ERROR,
            )
            .returning(TelegramUpdateInboxModel.update_id)
        )
        uncertain_statement = (
            update(TelegramUpdateInboxModel)
            .where(
                TelegramUpdateInboxModel.status == "processing",
                TelegramUpdateInboxModel.claimed_at <= claimed_before,
                TelegramUpdateInboxModel.external_mutation_started_at.is_not(None),
            )
            .values(
                status="uncertain",
                last_error=_UNCERTAIN_RECOVERY_ERROR,
            )
            .returning(TelegramUpdateInboxModel.update_id)
        )

        async with self._session_factory.begin() as session:
            safe_retry_result = await session.execute(safe_retry_statement)
            safe_retry_ids = safe_retry_result.scalars().all()
            uncertain_result = await session.execute(uncertain_statement)
            uncertain_ids = uncertain_result.scalars().all()

        return RecoveredUpdates(
            retried_count=len(safe_retry_ids),
            uncertain_count=len(uncertain_ids),
        )

    async def _mark_terminal(
        self,
        update_id: int,
        attempt_count: int,
        *,
        status: _TerminalStatus,
        error: str | None,
    ) -> None:
        statement = (
            update(TelegramUpdateInboxModel)
            .where(
                TelegramUpdateInboxModel.update_id == update_id,
                TelegramUpdateInboxModel.status == "processing",
                TelegramUpdateInboxModel.attempt_count == attempt_count,
            )
            .values(status=status, last_error=error)
            .returning(TelegramUpdateInboxModel.update_id)
        )

        await self._execute_transition(
            statement,
            update_id=update_id,
            transition=f"mark {status}",
        )

    async def _execute_transition(
        self,
        statement: Any,
        *,
        update_id: int,
        transition: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(statement)
            transitioned_update_id = result.scalar_one_or_none()
            if transitioned_update_id is None:
                raise UpdateTransitionError(
                    f"Telegram update {update_id} cannot {transition}; "
                    "expected the current processing attempt"
                )
