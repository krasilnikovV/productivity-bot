import pytest

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
