from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from productivity_bot.adapters.singularity.mapper import map_task
from productivity_bot.adapters.singularity.schemas import TaskResponse
from productivity_bot.domain.entities import TaskPriority


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
    task = map_task(TaskResponse.model_validate({"id": "T-1", "title": "Task", "priority": priority}))

    assert task.priority is expected


@pytest.mark.parametrize("field", ["start", "deadline"])
@pytest.mark.parametrize("value", [pytest.param(None, id="null"), pytest.param("", id="empty")])
def test_map_task_normalizes_empty_dates(field: str, value: str | None) -> None:
    task = map_task(TaskResponse.model_validate({"id": "T-1", "title": "Task", field: value}))

    assert getattr(task, field) is None


@pytest.mark.parametrize("field", ["start", "deadline"])
def test_map_task_defaults_missing_dates_to_none(field: str) -> None:
    task = map_task(TaskResponse.model_validate({"id": "T-1", "title": "Task"}))

    assert getattr(task, field) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            "2026-08-29",
            datetime(2026, 8, 29, tzinfo=UTC),
            id="date-only",
        ),
        pytest.param(
            "2026-08-29T09:15:30.123Z",
            datetime(2026, 8, 29, 9, 15, 30, 123000, tzinfo=UTC),
            id="utc-with-fractional-seconds",
        ),
        pytest.param(
            "2026-08-29T12:15:30+03:00",
            datetime(2026, 8, 29, 9, 15, 30, tzinfo=UTC),
            id="explicit-offset",
        ),
    ],
)
def test_map_task_parses_iso_dates(value: str, expected: datetime) -> None:
    task = map_task(
        TaskResponse.model_validate(
            {"id": "T-1", "title": "Task", "start": value, "deadline": value}
        )
    )

    assert task.start == expected
    assert task.deadline == expected
    assert task.start.tzinfo is UTC
    assert task.deadline.tzinfo is UTC


@pytest.mark.parametrize("priority", [3, -1, "1", 1.0, True])
def test_task_response_rejects_invalid_priority(priority: object) -> None:
    with pytest.raises(ValidationError) as error_info:
        TaskResponse.model_validate({"id": "T-1", "title": "Task", "priority": priority})

    assert error_info.value.errors()[0]["loc"] == ("priority",)


@pytest.mark.parametrize("field", ["start", "deadline"])
@pytest.mark.parametrize("value", ["not-a-date", 123])
def test_task_response_rejects_invalid_dates(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as error_info:
        TaskResponse.model_validate({"id": "T-1", "title": "Task", field: value})

    assert error_info.value.errors()[0]["loc"] == (field,)
