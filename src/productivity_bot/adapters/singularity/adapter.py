from pydantic import ValidationError

from productivity_bot.adapters.singularity.client import (
    SingularityApiError,
    SingularityClient,
    SingularityClientError,
    SingularityRequestNotSentError,
)
from productivity_bot.adapters.singularity.mapper import map_task
from productivity_bot.adapters.singularity.schemas import (
    CompleteTaskRequest,
    CreateTaskRequest,
    TaskListResponse,
    TaskResponse,
)
from productivity_bot.application.ports import (
    TaskMutationConfirmedError,
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
    TaskReadError,
)
from productivity_bot.domain.entities import Task

PAGE_SIZE = 1000


class SingularityAdapter:
    """Implement task repository operations through the Singularity API."""

    def __init__(self, client: SingularityClient) -> None:
        self._client = client

    async def create_task(self, title: str) -> Task:
        request = CreateTaskRequest(title=title)
        try:
            response = await self._client.request(
                "POST",
                "task",
                json=request.model_dump(mode="json"),
            )
        except SingularityRequestNotSentError as error:
            raise TaskMutationNotAppliedError(
                "Singularity task creation request was not sent",
                retryable=True,
            ) from error
        except SingularityApiError as error:
            if 400 <= error.status_code < 500:
                raise TaskMutationNotAppliedError(
                    "Singularity rejected task creation",
                    retryable=False,
                ) from error
            raise TaskMutationOutcomeUnknownError(
                "Singularity task creation outcome is unknown"
            ) from error
        except SingularityClientError as error:
            raise TaskMutationOutcomeUnknownError(
                "Singularity task creation outcome is unknown"
            ) from error

        try:
            task = TaskResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise TaskMutationConfirmedError(
                "Singularity confirmed task creation but returned an invalid response"
            ) from error
        return map_task(task)

    async def list_active_tasks(self) -> list[Task]:
        """Return all incomplete, non-note, non-removed, and non-archived tasks."""

        tasks: list[Task] = []
        offset = 0

        try:
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
                        "fields": "id,title,start,deadline,priority",
                        "maxCount": PAGE_SIZE,
                        "offset": offset,
                    },
                )
                page = TaskListResponse.model_validate(response.json())
                tasks.extend(map_task(task) for task in page.tasks)

                if len(page.tasks) < PAGE_SIZE:
                    return tasks

                offset += PAGE_SIZE
        except SingularityApiError as error:
            raise TaskReadError(
                "Singularity active-task read failed",
                retryable=error.status_code >= 500,
            ) from error
        except SingularityClientError as error:
            raise TaskReadError(
                "Singularity active-task read failed",
                retryable=True,
            ) from error
        except (ValueError, ValidationError) as error:
            raise TaskReadError(
                "Singularity returned an invalid active-task response",
                retryable=False,
            ) from error

    async def complete_task(self, task_id: str) -> None:
        request = CompleteTaskRequest()
        try:
            await self._client.request(
                "PATCH",
                f"task/{task_id}",
                json=request.model_dump(mode="json"),
            )
        except SingularityRequestNotSentError as error:
            raise TaskMutationNotAppliedError(
                "Singularity task completion request was not sent",
                retryable=True,
            ) from error
        except SingularityApiError as error:
            if 400 <= error.status_code < 500:
                raise TaskMutationNotAppliedError(
                    "Singularity rejected task completion",
                    retryable=False,
                ) from error
            raise TaskMutationOutcomeUnknownError(
                "Singularity task completion outcome is unknown"
            ) from error
        except SingularityClientError as error:
            raise TaskMutationOutcomeUnknownError(
                "Singularity task completion outcome is unknown"
            ) from error
