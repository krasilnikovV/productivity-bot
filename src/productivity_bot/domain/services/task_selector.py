from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from productivity_bot.domain.entities import Task, TaskPriority

_PRIORITY_ORDER = {
    TaskPriority.HIGH: 0,
    TaskPriority.NORMAL: 1,
    TaskPriority.LOW: 2,
}


def select_next_task(tasks: Iterable[Task], now: datetime) -> Task | None:
    """Select the highest-ranked task available at ``now``."""

    reference = _as_utc(now)
    candidates = (
        task
        for task in tasks
        if task.start is None or _as_utc(task.start) <= reference
    )
    return min(
        candidates,
        key=lambda task: _selection_key(task, reference),
        default=None,
    )


def _selection_key(
    task: Task, now: datetime
) -> tuple[bool, int, bool, timedelta, bool, datetime, str]:
    deadline = _as_utc(task.deadline) if task.deadline is not None else None
    start = _as_utc(task.start) if task.start is not None else None

    return (
        deadline is None or deadline >= now,
        _PRIORITY_ORDER[task.priority],
        deadline is None,
        abs(deadline - now) if deadline is not None else timedelta(),
        start is None,
        start if start is not None else now,
        task.id,
    )


def _as_utc(value: datetime) -> datetime:
    """Interpret timezone-naive datetimes as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
