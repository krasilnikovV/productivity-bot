import json
from datetime import UTC, datetime

import httpx2
import pytest
from pydantic import ValidationError

from productivity_bot.adapters.singularity import SingularityAdapter, SingularityClient
from productivity_bot.application.ports import (
    TaskMutationConfirmedError,
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
    TaskReadError,
    TaskRepository,
)
from productivity_bot.domain.entities import Task, TaskPriority

INVALID_TASK_PAYLOADS = (
    pytest.param({"title": "Missing id"}, id="missing-id"),
    pytest.param({"id": "T-123"}, id="missing-title"),
    pytest.param({"id": 123, "title": "Non-string id"}, id="non-string-id"),
    pytest.param({"id": "T-123", "title": 123}, id="non-string-title"),
)


@pytest.mark.asyncio
async def test_create_task_sends_title_and_maps_full_response() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url == "https://api.singularity-app.com/v2/task"
        assert json.loads(request.content) == {"title": "Buy groceries"}
        return httpx2.Response(
            201,
            json={
                "id": "T-123",
                "title": "Buy groceries",
                "start": "2026-08-29T09:15:30.123Z",
                "deadline": "2026-08-29T12:15:30+03:00",
                "priority": 0,
                "projectId": "P-456",
                "tags": [{"id": "tag-id"}],
            },
        )

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        repository: TaskRepository = SingularityAdapter(client)
        task = await repository.create_task("Buy groceries")

    assert task == Task(
        id="T-123",
        title="Buy groceries",
        start=datetime(2026, 8, 29, 9, 15, 30, 123000, tzinfo=UTC),
        deadline=datetime.fromisoformat("2026-08-29T12:15:30+03:00"),
        priority=TaskPriority.HIGH,
    )


@pytest.mark.asyncio
async def test_list_active_tasks_sends_filters_and_stops_after_short_page() -> None:
    request_count = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal request_count
        request_count += 1
        assert request.method == "GET"
        assert request.url.path == "/v2/task"
        assert dict(request.url.params) == {
            "checked": "0",
            "isNote": "false",
            "includeRemoved": "false",
            "includeArchived": "false",
            "includeAllRecurrenceInstances": "true",
            "fields": "id,title,start,deadline,priority",
            "maxCount": "1000",
            "offset": "0",
        }
        assert "limit" not in request.url.params
        return httpx2.Response(
            200,
            json={
                "tasks": [
                    {
                        "id": "T-1",
                        "title": "First",
                        "start": "2026-08-29T09:15:30.123Z",
                        "deadline": "2026-08-29T12:15:30+03:00",
                        "priority": 0,
                        "extra": "ignored",
                    },
                    {"id": "T-2", "title": "Second"},
                ],
                "extra": "ignored",
            },
        )

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        tasks = await SingularityAdapter(client).list_active_tasks()

    assert tasks == [
        Task(
            id="T-1",
            title="First",
            start=datetime(2026, 8, 29, 9, 15, 30, 123000, tzinfo=UTC),
            deadline=datetime.fromisoformat("2026-08-29T12:15:30+03:00"),
            priority=TaskPriority.HIGH,
        ),
        Task(id="T-2", title="Second"),
    ]
    assert request_count == 1


@pytest.mark.asyncio
async def test_list_active_tasks_reads_full_page_then_short_page() -> None:
    offsets: list[int] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["maxCount"] == "1000"
        assert "limit" not in request.url.params
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        if offset == 0:
            _tasks = [
                {"id": f"T-{index}", "title": f"Task {index}"} for index in range(1000)
            ]
        else:
            _tasks = [{"id": "T-1000", "title": "Task 1000"}]
        return httpx2.Response(200, json={"tasks": _tasks})

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        tasks = await SingularityAdapter(client).list_active_tasks()

    assert offsets == [0, 1000]
    assert tasks == [
        Task(id=f"T-{index}", title=f"Task {index}") for index in range(1001)
    ]


@pytest.mark.asyncio
async def test_list_active_tasks_returns_empty_list_after_one_request() -> None:
    request_count = 0

    # noinspection unused-parameter
    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal request_count
        request_count += 1
        return httpx2.Response(200, json={"tasks": []})

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        tasks = await SingularityAdapter(client).list_active_tasks()

    assert tasks == []
    assert request_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_or_error,retryable",
    [
        pytest.param(httpx2.ReadTimeout("response timeout"), True, id="timeout"),
        pytest.param(httpx2.Response(503), True, id="server-error"),
        pytest.param(httpx2.Response(400), False, id="client-error"),
    ],
)
async def test_list_active_tasks_classifies_external_errors(
    response_or_error: httpx2.Response | httpx2.RequestError,
    retryable: bool,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if isinstance(response_or_error, httpx2.RequestError):
            response_or_error.request = request
            raise response_or_error
        return response_or_error

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskReadError) as error_info:
            await SingularityAdapter(client).list_active_tasks()

    assert error_info.value.retryable is retryable


@pytest.mark.asyncio
async def test_complete_task_patches_checked_status() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PATCH"
        assert request.url == "https://api.singularity-app.com/v2/task/T-123"
        assert json.loads(request.content) == {"checked": 1}
        return httpx2.Response(
            200,
            json={"id": "T-123", "title": "Buy groceries", "checked": 1},
        )

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        await SingularityAdapter(client).complete_task("T-123")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    INVALID_TASK_PAYLOADS,
)
async def test_create_task_rejects_invalid_response(payload: dict[str, object]) -> None:
    # noinspection unused-parameter
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(201, json=payload)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationConfirmedError):
            await SingularityAdapter(client).create_task("Title")


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_metadata_in_confirmed_response() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            201,
            json={"id": "T-123", "title": "Title", "priority": 3},
        )

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationConfirmedError):
            await SingularityAdapter(client).create_task("Title")


@pytest.mark.asyncio
async def test_create_task_maps_request_not_sent_to_retryable_safe_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection failed", request=request)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationNotAppliedError) as error_info:
            await SingularityAdapter(client).create_task("Title")

    assert error_info.value.retryable is True


@pytest.mark.asyncio
async def test_create_task_maps_rejection_to_terminal_safe_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, request=request)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationNotAppliedError) as error_info:
            await SingularityAdapter(client).create_task("Title")

    assert error_info.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_or_error",
    [
        pytest.param(httpx2.ReadTimeout("response timeout"), id="read-timeout"),
        pytest.param(httpx2.Response(503), id="server-error"),
    ],
)
async def test_create_task_maps_ambiguous_failure_to_unknown_outcome(
    response_or_error: httpx2.Response | httpx2.RequestError,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if isinstance(response_or_error, httpx2.RequestError):
            response_or_error.request = request
            raise response_or_error
        return response_or_error

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationOutcomeUnknownError):
            await SingularityAdapter(client).create_task("Title")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_payload",
    INVALID_TASK_PAYLOADS,
)
async def test_list_active_tasks_rejects_invalid_task_response(
    task_payload: dict[str, object],
) -> None:
    # noinspection unused-parameter
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"tasks": [task_payload]})

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(ValidationError):
            await SingularityAdapter(client).list_active_tasks()
