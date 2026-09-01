from datetime import datetime
from zoneinfo import ZoneInfo

from productivity_bot.adapters.singularity.schemas import TaskResponse
from productivity_bot.domain.entities import Task, TaskPriority
from productivity_bot.utils.datetime import normalize_datetime_to_utc

_TASK_PRIORITIES = {
    0: TaskPriority.HIGH,
    1: TaskPriority.NORMAL,
    2: TaskPriority.LOW,
}


def map_task(response: TaskResponse, user_timezone: ZoneInfo) -> Task:
    return Task(
        id=response.id,
        title=response.title,
        start=_normalize_optional_datetime(response.start, user_timezone),
        deadline=_normalize_optional_datetime(response.deadline, user_timezone),
        priority=_TASK_PRIORITIES[response.priority],
    )


def _normalize_optional_datetime(
    value: datetime | None,
    user_timezone: ZoneInfo,
) -> datetime | None:
    if value is None:
        return None
    return normalize_datetime_to_utc(value, user_timezone)
