from collections.abc import Iterable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from productivity_bot.domain.entities import Task, TaskPriority
from productivity_bot.utils.datetime import normalize_datetime_to_utc

_PRIORITY_ORDER = {
    TaskPriority.HIGH: 0,
    TaskPriority.NORMAL: 1,
    TaskPriority.LOW: 2,
}


def select_next_task(
    tasks: Iterable[Task], now: datetime, user_timezone: ZoneInfo
) -> Task | None:
    """Select the highest-ranked task available at ``now``."""

    reference = normalize_datetime_to_utc(now, user_timezone)
    candidates = (
        task
        for task in tasks
        if task.start is None
        or normalize_datetime_to_utc(task.start, user_timezone) <= reference
    )
    return min(
        candidates,
        key=lambda task: _selection_key(task, reference, user_timezone),
        default=None,
    )


def _selection_key(
    task: Task, now: datetime, user_timezone: ZoneInfo
) -> tuple[bool, int, bool, timedelta, bool, datetime, str]:
    deadline = (
        normalize_datetime_to_utc(task.deadline, user_timezone)
        if task.deadline is not None
        else None
    )
    start = (
        normalize_datetime_to_utc(task.start, user_timezone)
        if task.start is not None
        else None
    )

    return (
        deadline is None or deadline >= now,
        _PRIORITY_ORDER[task.priority],
        deadline is None,
        abs(deadline - now) if deadline is not None else timedelta(),
        start is None,
        start if start is not None else now,
        task.id,
    )
