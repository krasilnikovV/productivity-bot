from collections.abc import Sequence

import pytest

from productivity_bot.application.use_cases import CaptureTask
from productivity_bot.domain.entities import Task


class FakeTaskRepository:
    def __init__(self, created_task: Task) -> None:
        self.created_task = created_task
        self.created_titles: list[str] = []

    async def create_task(self, title: str) -> Task:
        self.created_titles.append(title)
        return self.created_task

    async def list_active_tasks(self) -> Sequence[Task]:
        return []

    async def complete_task(self, task_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_capture_task_creates_task_with_original_message_text() -> None:
    created_task = Task(id="T-123", title="Call the doctor tomorrow.")
    repository = FakeTaskRepository(created_task)
    capture_task = CaptureTask(repository)

    result = await capture_task.execute("Call the doctor tomorrow.")

    assert repository.created_titles == ["Call the doctor tomorrow."]
    assert result == created_task
