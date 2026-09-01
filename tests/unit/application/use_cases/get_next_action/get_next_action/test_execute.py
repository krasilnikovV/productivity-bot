from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, create_autospec
from zoneinfo import ZoneInfo

import pytest

import productivity_bot.application.use_cases.get_next_action as get_next_action_module
from productivity_bot.application.ports import TaskRepository
from productivity_bot.application.use_cases import GetNextAction
from productivity_bot.domain.entities import Task, TaskPriority

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
USER_TIMEZONE = ZoneInfo("Europe/Moscow")


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
    repository = create_autospec(TaskRepository, instance=True, spec_set=True)
    repository.list_active_tasks.return_value = [normal_priority_task, high_priority_task]

    result = await GetNextAction(repository, USER_TIMEZONE).execute(NOW)

    assert result is high_priority_task
    repository.list_active_tasks.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_next_action_returns_none_when_no_active_tasks_exist() -> None:
    repository = create_autospec(TaskRepository, instance=True, spec_set=True)
    repository.list_active_tasks.return_value = []

    result = await GetNextAction(repository, USER_TIMEZONE).execute(NOW)

    assert result is None


@pytest.mark.asyncio
async def test_get_next_action_uses_the_supplied_reference_time() -> None:
    start = NOW + timedelta(hours=1)
    scheduled_task = Task(id="scheduled", title="Scheduled task", start=start)
    repository = create_autospec(TaskRepository, instance=True, spec_set=True)
    repository.list_active_tasks.return_value = [scheduled_task]
    get_next_action = GetNextAction(repository, USER_TIMEZONE)

    before_start = await get_next_action.execute(NOW)
    at_start = await get_next_action.execute(start)

    assert before_start is None
    assert at_start is scheduled_task


@pytest.mark.asyncio
async def test_get_next_action_forwards_its_timezone_to_the_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Task(id="task", title="Task")
    repository = create_autospec(TaskRepository, instance=True, spec_set=True)
    repository.list_active_tasks.return_value = [task]
    user_timezone = ZoneInfo("America/New_York")
    selector = Mock(return_value=task)

    monkeypatch.setattr(get_next_action_module, "select_next_task", selector)
    result = await GetNextAction(repository, user_timezone).execute(NOW)

    assert result is task
    repository.list_active_tasks.assert_awaited_once_with()
    selector.assert_called_once_with([task], NOW, user_timezone)
