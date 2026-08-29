from unittest.mock import create_autospec

import pytest

from productivity_bot.application.ports import TaskRepository
from productivity_bot.application.use_cases import CaptureTask
from productivity_bot.domain.entities import Task


@pytest.mark.asyncio
async def test_capture_task_creates_task_with_normalized_message_text() -> None:
    created_task = Task(id="T-123", title="Call the doctor tomorrow.")
    repository = create_autospec(TaskRepository, instance=True, spec_set=True)
    repository.create_task.return_value = created_task
    capture_task = CaptureTask(repository)

    result = await capture_task.execute("  Call the doctor tomorrow.\n")

    repository.create_task.assert_awaited_once_with("Call the doctor tomorrow.")
    assert result == created_task


@pytest.mark.asyncio
@pytest.mark.parametrize("message_text", ["", " ", "\t\n"])
async def test_capture_task_rejects_blank_message_text(message_text: str) -> None:
    repository = create_autospec(TaskRepository, instance=True, spec_set=True)
    capture_task = CaptureTask(repository)

    with pytest.raises(ValueError, match="Task title must not be empty"):
        await capture_task.execute(message_text)

    repository.create_task.assert_not_awaited()
