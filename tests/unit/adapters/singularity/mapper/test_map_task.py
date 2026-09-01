from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from productivity_bot.adapters.singularity.mapper import map_task
from productivity_bot.adapters.singularity.schemas import TaskResponse
from productivity_bot.domain.entities import TaskPriority

USER_TIMEZONE = ZoneInfo("Europe/Moscow")


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        pytest.param(0, TaskPriority.HIGH, id="high"),
        pytest.param(1, TaskPriority.NORMAL, id="normal"),
        pytest.param(2, TaskPriority.LOW, id="low"),
    ],
)
def test_map_task_maps_singularity_priority(
    priority: int,
    expected: TaskPriority,
) -> None:
    task = map_task(
        TaskResponse.model_validate(
            {"id": "T-1", "title": "Task", "priority": priority}
        ),
        USER_TIMEZONE,
    )

    assert task.priority is expected


def test_map_task_interprets_naive_dates_in_the_user_timezone() -> None:
    task = map_task(
        TaskResponse.model_validate(
            {
                "id": "T-1",
                "title": "Task",
                "start": "2026-08-29T14:00:00",
                "deadline": "2026-08-29T14:00:00",
            }
        ),
        USER_TIMEZONE,
    )

    assert task.start == datetime(2026, 8, 29, 11, 0, tzinfo=UTC)
    assert task.deadline == datetime(2026, 8, 29, 11, 0, tzinfo=UTC)
