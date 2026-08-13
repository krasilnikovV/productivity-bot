from productivity_bot.adapters.singularity.client import SingularityClient
from productivity_bot.adapters.singularity.mapper import map_task
from productivity_bot.adapters.singularity.schemas import (
    CompleteTaskRequest,
    CreateTaskRequest,
    TaskListResponse,
    TaskResponse,
)
from productivity_bot.domain.entities import Task

PAGE_SIZE = 1000


class SingularityAdapter:
    """Implement task repository operations through the Singularity API."""

    def __init__(self, client: SingularityClient) -> None:
        self._client = client

    async def create_task(self, title: str) -> Task:
        request = CreateTaskRequest(title=title)
        response = await self._client.request(
            "POST",
            "task",
            json=request.model_dump(mode="json"),
        )
        task = TaskResponse.model_validate(response.json())
        return map_task(task)

    async def list_active_tasks(self) -> list[Task]:
        """Return all incomplete, non-note, non-removed, and non-archived tasks."""

        tasks: list[Task] = []
        offset = 0

        while True:
            response = await self._client.request(
                "GET",
                "task",
                params={
                    "checked": 0,
                    "isNote": "false",
                    "includeRemoved": "false",
                    "includeArchived": "false",
                    "includeAllRecurrenceInstances": "true",
                    "fields": "id,title",
                    "maxCount": PAGE_SIZE,
                    "offset": offset,
                },
            )
            page = TaskListResponse.model_validate(response.json())
            tasks.extend(map_task(task) for task in page.tasks)

            if len(page.tasks) < PAGE_SIZE:
                return tasks

            offset += PAGE_SIZE

    async def complete_task(self, task_id: str) -> None:
        request = CompleteTaskRequest()
        await self._client.request(
            "PATCH",
            f"task/{task_id}",
            json=request.model_dump(mode="json"),
        )
