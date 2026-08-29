from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from productivity_bot.application.use_cases import GetNextAction
from productivity_bot.domain.entities import Task, TaskPriority

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class FakeTaskRepository:
    def __init__(self, active_tasks: Sequence[Task]) -> None:
        self.active_tasks = active_tasks
        self.list_active_tasks_calls = 0

    async def list_active_tasks(self) -> Sequence[Task]:
        self.list_active_tasks_calls += 1
        return self.active_tasks


@pytest.mark.asyncio
async def test_get_next_action_returns_selector_candidate_after_one_read() -> None:
    high_priority_task = Task(
        id="high-priority",
        title="High priority",
        priority=TaskPriority.HIGH,
    )
    normal_priority_task = Task(
        id="normal-priority",
        title="Normal priority",
        deadline=NOW + timedelta(minutes=1),
    )
    repository = FakeTaskRepository([normal_priority_task, high_priority_task])

    result = await GetNextAction(repository).execute(NOW)

    assert result is high_priority_task
    assert repository.list_active_tasks_calls == 1


@pytest.mark.asyncio
async def test_get_next_action_returns_none_when_no_active_tasks_exist() -> None:
    repository = FakeTaskRepository([])

    result = await GetNextAction(repository).execute(NOW)

    assert result is None


@pytest.mark.asyncio
async def test_get_next_action_uses_the_supplied_reference_time() -> None:
    start = NOW + timedelta(hours=1)
    scheduled_task = Task(id="scheduled", title="Scheduled task", start=start)
    repository = FakeTaskRepository([scheduled_task])
    get_next_action = GetNextAction(repository)

    before_start = await get_next_action.execute(NOW)
    at_start = await get_next_action.execute(start)

    assert before_start is None
    assert at_start is scheduled_task
