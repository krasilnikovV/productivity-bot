from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ClaimedUpdate:
    update_id: int
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class RecoveredUpdates:
    retried_count: int
    uncertain_count: int


class UpdateTransitionError(Exception):
    """Raised when an inbox update cannot make the requested transition."""


class TelegramUpdateInboxRepository(Protocol):
    async def insert_update(self, update_id: int, payload: dict[str, Any]) -> bool: ...

    async def claim_pending_update(self) -> ClaimedUpdate | None: ...

    async def mark_external_mutation_started(
        self,
        update_id: int,
        attempt_count: int,
    ) -> None: ...

    async def reschedule(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
        available_at: datetime,
    ) -> None: ...

    async def mark_succeeded(self, update_id: int, attempt_count: int) -> None: ...

    async def mark_failed(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None: ...

    async def mark_uncertain(
        self,
        update_id: int,
        attempt_count: int,
        error: str,
    ) -> None: ...

    async def recover_abandoned_updates(
        self,
        claimed_before: datetime,
    ) -> RecoveredUpdates: ...
