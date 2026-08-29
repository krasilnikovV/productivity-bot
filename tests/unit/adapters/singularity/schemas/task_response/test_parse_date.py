from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from productivity_bot.adapters.singularity.schemas import TaskResponse


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {"start": "2026-08-29", "deadline": "2026-08-29"},
            datetime(2026, 8, 29, tzinfo=UTC),
            id="date-only",
        ),
        pytest.param(
            {
                "start": "2026-08-29T09:15:30.123Z",
                "deadline": "2026-08-29T09:15:30.123Z",
            },
            datetime(2026, 8, 29, 9, 15, 30, 123000, tzinfo=UTC),
            id="utc-with-fractional-seconds",
        ),
        pytest.param(
            {
                "start": "2026-08-29T12:15:30+03:00",
                "deadline": "2026-08-29T12:15:30+03:00",
            },
            datetime(2026, 8, 29, 9, 15, 30, tzinfo=UTC),
            id="explicit-offset",
        ),
    ],
)
def test_task_response_normalizes_dates_to_utc(
    payload: dict[str, str],
    expected: datetime,
) -> None:
    response = TaskResponse.model_validate({"id": "T-1", "title": "Task", **payload})

    assert response.start == expected
    assert response.deadline == expected


def test_task_response_normalizes_null_and_empty_dates_to_none() -> None:
    response = TaskResponse.model_validate(
        {"id": "T-1", "title": "Task", "start": None, "deadline": ""}
    )

    assert response.start is None
    assert response.deadline is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"start": "not-a-date"}, id="invalid-start"),
        pytest.param({"deadline": 123}, id="invalid-deadline"),
    ],
)
def test_task_response_rejects_invalid_dates(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskResponse.model_validate({"id": "T-1", "title": "Task", **payload})
