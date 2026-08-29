from datetime import UTC, datetime

from productivity_bot.domain.entities import Task, TaskPriority


def test_task_defaults_selection_metadata() -> None:
    task = Task(id="T-1", title="Title")

    assert task.start is None
    assert task.deadline is None
    assert task.priority is TaskPriority.NORMAL


def test_task_preserves_explicit_selection_metadata() -> None:
    start = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    deadline = datetime(2026, 8, 30, 17, 0, tzinfo=UTC)

    task = Task(
        id="T-1",
        title="Title",
        start=start,
        deadline=deadline,
        priority=TaskPriority.HIGH,
    )

    assert task.start is start
    assert task.deadline is deadline
    assert task.priority is TaskPriority.HIGH
