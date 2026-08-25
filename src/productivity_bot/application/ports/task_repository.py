from collections.abc import Sequence
from typing import Protocol

from productivity_bot.domain.entities import Task


class TaskMutationNotAppliedError(Exception):
    """A task mutation is known not to have been applied."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__(message)


class TaskMutationOutcomeUnknownError(Exception):
    """A task mutation may have been applied, but its result is unknown."""


class TaskMutationConfirmedError(Exception):
    """A task mutation succeeded, but its result could not be consumed."""


class TaskRepository(Protocol):
    async def create_task(self, title: str) -> Task: ...

    async def list_active_tasks(self) -> Sequence[Task]: ...

    async def complete_task(self, task_id: str) -> None: ...
