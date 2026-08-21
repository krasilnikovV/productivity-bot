from productivity_bot.application.ports import TaskRepository
from productivity_bot.domain.entities import Task


class CaptureTask:
    def __init__(self, task_repository: TaskRepository) -> None:
        self._task_repository: TaskRepository = task_repository

    async def execute(self, message_text: str) -> Task:
        title = message_text.strip()
        if not title:
            raise ValueError("Task title must not be empty")

        return await self._task_repository.create_task(title)
