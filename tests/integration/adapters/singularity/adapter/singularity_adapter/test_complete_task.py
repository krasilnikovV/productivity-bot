import json
from zoneinfo import ZoneInfo

import httpx2
import pytest

from productivity_bot.adapters.singularity import SingularityAdapter, SingularityClient
from productivity_bot.application.ports import (
    TaskMutationNotAppliedError,
    TaskMutationOutcomeUnknownError,
)

USER_TIMEZONE = ZoneInfo("Europe/Moscow")


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
        await SingularityAdapter(client, USER_TIMEZONE).complete_task("T-123")


@pytest.mark.asyncio
async def test_complete_task_maps_request_not_sent_to_retryable_safe_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection failed", request=request)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationNotAppliedError) as error_info:
            await SingularityAdapter(client, USER_TIMEZONE).complete_task("T-123")

    assert error_info.value.retryable is True


@pytest.mark.asyncio
async def test_complete_task_maps_rejection_to_terminal_safe_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, request=request)

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(TaskMutationNotAppliedError) as error_info:
            await SingularityAdapter(client, USER_TIMEZONE).complete_task("T-123")

    assert error_info.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_or_error",
    [
        pytest.param(httpx2.ReadTimeout("response timeout"), id="read-timeout"),
        pytest.param(httpx2.Response(503), id="server-error"),
    ],
)
async def test_complete_task_maps_ambiguous_failure_to_unknown_outcome(
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
            await SingularityAdapter(client, USER_TIMEZONE).complete_task("T-123")
