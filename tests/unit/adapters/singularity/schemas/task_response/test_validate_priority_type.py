import pytest
from pydantic import ValidationError

from productivity_bot.adapters.singularity.schemas import TaskResponse


@pytest.mark.parametrize("priority", [3, -1, "1", 1.0, True])
def test_task_response_rejects_invalid_priority(priority: object) -> None:
    with pytest.raises(ValidationError):
        TaskResponse.model_validate({"id": "T-1", "title": "Task", "priority": priority})
