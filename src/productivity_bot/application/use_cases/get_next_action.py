from datetime import datetime

from productivity_bot.application.ports import TaskRepository
from productivity_bot.domain.entities import Task
from productivity_bot.domain.services import select_next_task


class GetNextAction:
    def __init__(self, task_repository: TaskRepository) -> None:
        self._task_repository: TaskRepository = task_repository

    async def execute(self, now: datetime) -> Task | None:
        tasks = await self._task_repository.list_active_tasks()
        return select_next_task(tasks, now)
