import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest

from productivity_bot.adapters.singularity import SingularityAdapter, SingularityClient
from productivity_bot.application.ports import (
    TaskMutationConfirmedError,
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
    TaskRepository,
)
from productivity_bot.domain.entities import Task, TaskPriority

USER_TIMEZONE = ZoneInfo("Europe/Moscow")

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
        repository: TaskRepository = SingularityAdapter(client, USER_TIMEZONE)
        task = await repository.create_task("Buy groceries")

    assert task == Task(
        id="T-123",
        title="Buy groceries",
        start=datetime(2026, 8, 29, 9, 15, 30, 123000, tzinfo=UTC),
        deadline=datetime.fromisoformat("2026-08-29T12:15:30+03:00"),
        priority=TaskPriority.HIGH,
    )


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
            await SingularityAdapter(client, USER_TIMEZONE).create_task("Title")


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
            await SingularityAdapter(client, USER_TIMEZONE).create_task("Title")


@pytest.mark.asyncio
async def test_create_task_classifies_malformed_success_response_as_confirmed_mutation() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(201, content=b'{"id":')

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationConfirmedError):
            await SingularityAdapter(client, USER_TIMEZONE).create_task("Title")


@pytest.mark.asyncio
async def test_create_task_maps_request_not_sent_to_retryable_safe_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection failed", request=request)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationNotAppliedError) as error_info:
            await SingularityAdapter(client, USER_TIMEZONE).create_task("Title")

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
            await SingularityAdapter(client, USER_TIMEZONE).create_task("Title")

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
            await SingularityAdapter(client, USER_TIMEZONE).create_task("Title")
