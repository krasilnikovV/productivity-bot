from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest

from productivity_bot.adapters.singularity import SingularityAdapter, SingularityClient
from productivity_bot.application.ports import TaskReadError
from productivity_bot.application.use_cases import GetNextAction
from productivity_bot.domain.entities import Task, TaskPriority

USER_TIMEZONE = ZoneInfo("Europe/Moscow")

INVALID_TASK_PAYLOADS = (
    pytest.param({"title": "Missing id"}, id="missing-id"),
    pytest.param({"id": "T-123"}, id="missing-title"),
    pytest.param({"id": 123, "title": "Non-string id"}, id="non-string-id"),
    pytest.param({"id": "T-123", "title": 123}, id="non-string-title"),
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
        tasks = await SingularityAdapter(client, USER_TIMEZONE).list_active_tasks()

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
async def test_list_active_tasks_interprets_naive_dates_in_the_user_timezone() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "tasks": [
                    {
                        "id": "T-local",
                        "title": "Local task",
                        "start": "2026-08-29T14:00:00",
                        "deadline": "2026-08-29T14:00:00",
                    },
                    {
                        "id": "T-available",
                        "title": "Available task",
                        "priority": 0,
                    },
                ]
            },
        )

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        task = await GetNextAction(
            SingularityAdapter(client, USER_TIMEZONE),
            USER_TIMEZONE,
        ).execute(datetime(2026, 8, 29, 12, 0, tzinfo=UTC))

    assert task is not None
    assert task.id == "T-local"


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
        tasks = await SingularityAdapter(client, USER_TIMEZONE).list_active_tasks()

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
        tasks = await SingularityAdapter(client, USER_TIMEZONE).list_active_tasks()

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
            await SingularityAdapter(client, USER_TIMEZONE).list_active_tasks()

    assert error_info.value.retryable is retryable


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
        with pytest.raises(TaskReadError) as error_info:
            await SingularityAdapter(client, USER_TIMEZONE).list_active_tasks()

    assert error_info.value.retryable is False


@pytest.mark.asyncio
async def test_list_active_tasks_classifies_malformed_success_response_as_read_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b'{"tasks":')

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskReadError) as error_info:
            await SingularityAdapter(client, USER_TIMEZONE).list_active_tasks()

    assert error_info.value.retryable is False
