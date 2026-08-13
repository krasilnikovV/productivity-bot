from productivity_bot.adapters.singularity.schemas import TaskResponse
from productivity_bot.domain.entities import Task


def map_task(response: TaskResponse) -> Task:
    return Task(id=response.id, title=response.title)
