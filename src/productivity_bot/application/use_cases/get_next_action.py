from datetime import datetime
from zoneinfo import ZoneInfo

from productivity_bot.application.ports import TaskRepository
from productivity_bot.domain.entities import Task
from productivity_bot.domain.services import select_next_task


class GetNextAction:
    def __init__(
        self, task_repository: TaskRepository, user_timezone: ZoneInfo
    ) -> None:
        self._task_repository: TaskRepository = task_repository
        self._user_timezone = user_timezone

    async def execute(self, now: datetime) -> Task | None:
        tasks = await self._task_repository.list_active_tasks()
        return select_next_task(tasks, now, self._user_timezone)
