import json

import httpx2
import pytest
from pydantic import ValidationError

from productivity_bot.adapters.singularity import SingularityAdapter, SingularityClient
from productivity_bot.application.ports import TaskRepository
from productivity_bot.domain.entities import Task

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

    assert task == Task(id="T-123", title="Buy groceries")


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
            "fields": "id,title",
            "maxCount": "1000",
            "offset": "0",
        }
        assert "limit" not in request.url.params
        return httpx2.Response(
            200,
            json={
                "tasks": [
                    {"id": "T-1", "title": "First", "extra": "ignored"},
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

    assert tasks == [Task(id="T-1", title="First"), Task(id="T-2", title="Second")]
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
            tasks = [
                {"id": f"T-{index}", "title": f"Task {index}"} for index in range(1000)
            ]
        else:
            tasks = [{"id": "T-1000", "title": "Task 1000"}]
        return httpx2.Response(200, json={"tasks": tasks})

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
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(201, json=payload)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(ValidationError):
            await SingularityAdapter(client).create_task("Title")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_payload",
    INVALID_TASK_PAYLOADS,
)
async def test_list_active_tasks_rejects_invalid_task_response(
    task_payload: dict[str, object],
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"tasks": [task_payload]})

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(ValidationError):
            await SingularityAdapter(client).list_active_tasks()
