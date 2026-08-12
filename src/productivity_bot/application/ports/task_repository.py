from collections.abc import Sequence
from typing import Protocol

from productivity_bot.domain.entities import Task


class TaskRepository(Protocol):
    async def create_task(self, title: str) -> Task: ...

    async def list_active_tasks(self) -> Sequence[Task]: ...

    async def complete_task(self, task_id: str) -> None: ...
