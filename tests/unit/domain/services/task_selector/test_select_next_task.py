from datetime import UTC, datetime, timedelta, timezone

from productivity_bot.domain.entities import Task, TaskPriority
from productivity_bot.domain.services import select_next_task

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def task(
    task_id: str,
    *,
    start: datetime | None = None,
    deadline: datetime | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
) -> Task:
    return Task(
        id=task_id,
        title=task_id,
        start=start,
        deadline=deadline,
        priority=priority,
    )


def test_returns_none_for_an_empty_iterable() -> None:
    assert select_next_task([], NOW) is None


def test_excludes_future_start_tasks_before_ranking() -> None:
    future_task = task(
        "future",
        start=NOW + timedelta(minutes=1),
        deadline=NOW - timedelta(days=1),
        priority=TaskPriority.HIGH,
    )
    available_task = task("available", priority=TaskPriority.LOW)

    assert select_next_task([future_task, available_task], NOW) is available_task
    assert select_next_task([future_task], NOW) is None


def test_includes_task_starting_at_selection_reference() -> None:
    starting_now_task = task("starting-now", start=NOW)

    assert select_next_task([starting_now_task], NOW) is starting_now_task


def test_normalizes_date_only_and_offset_datetimes_to_utc() -> None:
    now_with_offset = datetime(
        2026, 8, 29, 15, 0, tzinfo=timezone(timedelta(hours=3))
    )
    date_only = datetime.fromisoformat("2026-08-29")
    date_only_task = task(
        "date-only",
        start=date_only,
        deadline=date_only,
        priority=TaskPriority.LOW,
    )
    offset_task = task(
        "offset",
        start=datetime(2026, 8, 29, 12, 0, tzinfo=timezone(timedelta(hours=3))),
        deadline=datetime(2026, 8, 29, 15, 0, tzinfo=timezone(timedelta(hours=3))),
        priority=TaskPriority.HIGH,
    )

    assert select_next_task([offset_task, date_only_task], now_with_offset) is date_only_task


def test_overdue_task_outranks_non_overdue_task() -> None:
    overdue_task = task(
        "overdue", deadline=NOW - timedelta(minutes=1), priority=TaskPriority.LOW
    )
    non_overdue_task = task(
        "non-overdue", deadline=NOW, priority=TaskPriority.HIGH
    )

    assert select_next_task([non_overdue_task, overdue_task], NOW) is overdue_task


def test_priority_orders_tasks_within_the_same_overdue_group() -> None:
    low_task = task(
        "low", deadline=NOW - timedelta(hours=1), priority=TaskPriority.LOW
    )
    normal_task = task(
        "normal", deadline=NOW - timedelta(hours=1), priority=TaskPriority.NORMAL
    )
    high_task = task(
        "high", deadline=NOW - timedelta(hours=1), priority=TaskPriority.HIGH
    )

    assert select_next_task([low_task, normal_task, high_task], NOW) is high_task
    assert select_next_task([low_task, normal_task], NOW) is normal_task


def test_nearest_present_deadline_outranks_missing_deadline() -> None:
    distant_overdue_task = task(
        "distant", deadline=NOW - timedelta(days=2)
    )
    nearby_overdue_task = task(
        "nearby", deadline=NOW - timedelta(minutes=1)
    )
    no_deadline_task = task("no-deadline")
    upcoming_task = task("upcoming", deadline=NOW + timedelta(hours=1))

    assert (
        select_next_task([distant_overdue_task, nearby_overdue_task], NOW)
        is nearby_overdue_task
    )
    assert select_next_task([no_deadline_task, upcoming_task], NOW) is upcoming_task


def test_nearest_future_deadline_outranks_more_distant_future_deadline() -> None:
    distant_task = task("distant", deadline=NOW + timedelta(days=2))
    nearby_task = task("nearby", deadline=NOW + timedelta(minutes=1))

    assert select_next_task([distant_task, nearby_task], NOW) is nearby_task


def test_earlier_present_start_outranks_missing_start() -> None:
    later_start_task = task("later", start=NOW - timedelta(minutes=1))
    earlier_start_task = task("earlier", start=NOW - timedelta(hours=1))
    no_start_task = task("no-start")

    assert select_next_task([later_start_task, earlier_start_task], NOW) is earlier_start_task
    assert select_next_task([no_start_task, earlier_start_task], NOW) is earlier_start_task


def test_lexicographically_smaller_id_breaks_ties_independent_of_input_order() -> None:
    first_task = task("a-task")
    second_task = task("z-task")

    assert select_next_task([second_task, first_task], NOW) is first_task
    assert select_next_task([first_task, second_task], NOW) is first_task


def test_does_not_modify_the_input_list() -> None:
    tasks = [
        task("future", start=NOW + timedelta(minutes=1)),
        task("available", deadline=NOW - timedelta(minutes=1)),
    ]
    original_tasks = tasks.copy()

    select_next_task(tasks, NOW)

    assert tasks == original_tasks
