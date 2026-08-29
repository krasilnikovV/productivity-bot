import json
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from productivity_bot.adapters.singularity import (
    SingularityApiError,
    SingularityClient,
    SingularityRequestNotSentError,
    SingularityTimeoutError,
)


@pytest.mark.asyncio
async def test_request_sends_auth_json_and_query_to_default_base_url() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url == "https://api.singularity-app.com/v2/task?offset=0"
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.headers["Accept"] == "application/json"
        assert json.loads(request.content) == {"title": "Buy groceries"}
        return httpx2.Response(201, json={"id": "task-id"})

    async with SingularityClient(
        "secret-token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        response = await client.request(
            "POST",
            "/task",
            params={"offset": 0},
            json={"title": "Buy groceries"},
        )

    assert response.status_code == 201
    assert response.json() == {"id": "task-id"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "path"),
    [
        ("https://singularity.example/v2", "task"),
        ("https://singularity.example/v2/", "/task"),
    ],
)
async def test_request_normalizes_custom_base_url_and_leading_slash(
    base_url: str,
    path: str,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "https://singularity.example/v2/task"
        return httpx2.Response(204)

    async with SingularityClient(
        "token",
        base_url=base_url,
        transport=httpx2.MockTransport(handler),
    ) as client:
        response = await client.request("GET", path)

    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [302, 400, 503])
async def test_non_2xx_response_raises_api_error(status_code: int) -> None:
    response_body = b'opaque error containing "secret-token"'

    # noinspection unused-parameter
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, content=response_body)

    async with SingularityClient(
        "secret-token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(SingularityApiError) as error_info:
            await client.request("GET", "task")

    error = error_info.value
    assert error.status_code == status_code
    assert error.response_body == response_body
    assert response_body.decode() not in str(error)
    assert "secret-token" not in str(error)


@pytest.mark.asyncio
async def test_request_converts_read_timeout() -> None:
    transport_error = httpx2.ReadTimeout("request timed out")

    # noinspection unused-parameter
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise transport_error

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(SingularityTimeoutError) as error_info:
            await client.request("GET", "task")

    assert error_info.value.__cause__ is transport_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport_error",
    [
        httpx2.ConnectError("connection failed"),
        httpx2.ConnectTimeout("connection timed out"),
        httpx2.PoolTimeout("connection pool timed out"),
    ],
)
async def test_request_identifies_transport_errors_before_send(
    transport_error: httpx2.RequestError,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise transport_error

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(SingularityRequestNotSentError):
            await client.request("POST", "task", json={"title": "Title"})



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["https://other.example/task", "//other.example/task"],
)
async def test_external_url_is_rejected_before_transport(path: str) -> None:
    handler = AsyncMock(return_value=httpx2.Response(200))
    transport = httpx2.MockTransport(handler)

    with patch.object(
        transport,
        "aclose",
        new_callable=AsyncMock,
        wraps=transport.aclose,
    ) as close_transport:
        async with SingularityClient("token", transport=transport) as client:
            with pytest.raises(ValueError, match="path must be relative"):
                await client.request("GET", path)

            handler.assert_not_awaited()

    close_transport.assert_awaited_once_with()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "../api",
        "./task",
        "task/../api",
        "%2e%2e/api",
        "%2E/task",
        "task/.%2e/api",
        "task/%2e./api",
        "%252e%252e/api",
        "%2e%2e%2fapi",
    ],
)
async def test_dot_segment_is_rejected_before_transport(path: str) -> None:
    handler = AsyncMock(return_value=httpx2.Response(200))
    transport = httpx2.MockTransport(handler)

    with patch.object(
        transport,
        "aclose",
        new_callable=AsyncMock,
        wraps=transport.aclose,
    ) as close_transport:
        async with SingularityClient("token", transport=transport) as client:
            with pytest.raises(ValueError, match="must not contain dot segments"):
                await client.request("GET", path)

            handler.assert_not_awaited()

    close_transport.assert_awaited_once_with()
