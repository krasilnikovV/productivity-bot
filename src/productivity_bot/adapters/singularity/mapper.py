from productivity_bot.adapters.singularity.schemas import TaskResponse
from productivity_bot.domain.entities import Task, TaskPriority

_TASK_PRIORITIES = {
    0: TaskPriority.HIGH,
    1: TaskPriority.NORMAL,
    2: TaskPriority.LOW,
}


def map_task(response: TaskResponse) -> Task:
    return Task(
        id=response.id,
        title=response.title,
        start=response.start,
        deadline=response.deadline,
        priority=_TASK_PRIORITIES[response.priority],
    )
