import json

import httpx2
import pytest

from productivity_bot.adapters.singularity import (
    SingularityApiError,
    SingularityClient,
    SingularityTimeoutError,
    SingularityTransportError,
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
@pytest.mark.parametrize(
    ("transport_error", "expected_error"),
    [
        (httpx2.ReadTimeout("request timed out"), SingularityTimeoutError),
        (httpx2.ConnectError("connection failed"), SingularityTransportError),
    ],
)
async def test_request_converts_transport_errors(
    transport_error: httpx2.RequestError,
    expected_error: type[SingularityTransportError],
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise transport_error

    async with SingularityClient(
        "token",
        transport=httpx2.MockTransport(handler),
    ) as client:
        with pytest.raises(expected_error) as error_info:
            await client.request("GET", "task")

    assert error_info.value.__cause__ is transport_error


@pytest.mark.parametrize("api_token", ["", " ", "\t\n"])
def test_empty_token_is_rejected_before_transport(api_token: str) -> None:
    transport = TrackingTransport()

    with pytest.raises(ValueError, match="token must not be empty"):
        SingularityClient(api_token, transport=transport)

    assert transport.request_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["https://other.example/task", "//other.example/task"],
)
async def test_external_url_is_rejected_before_transport(path: str) -> None:
    transport = TrackingTransport()

    async with SingularityClient("token", transport=transport) as client:
        with pytest.raises(ValueError, match="path must be relative"):
            await client.request("GET", path)

        assert transport.request_count == 0

    assert transport.is_closed


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
    transport = TrackingTransport()

    async with SingularityClient("token", transport=transport) as client:
        with pytest.raises(ValueError, match="must not contain dot segments"):
            await client.request("GET", path)

        assert transport.request_count == 0

    assert transport.is_closed


@pytest.mark.asyncio
async def test_aclose_closes_transport() -> None:
    transport = TrackingTransport()
    client = SingularityClient("token", transport=transport)

    await client.aclose()

    assert transport.is_closed


@pytest.mark.asyncio
async def test_context_manager_closes_transport() -> None:
    transport = TrackingTransport()

    async with SingularityClient("token", transport=transport):
        assert not transport.is_closed

    assert transport.is_closed


class TrackingTransport(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.request_count = 0
        self.is_closed = False

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.request_count += 1
        return httpx2.Response(200)

    async def aclose(self) -> None:
        self.is_closed = True
