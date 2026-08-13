from types import TracebackType
from typing import Any, Self
from urllib.parse import unquote, urlsplit

import httpx2

DEFAULT_BASE_URL = "https://api.singularity-app.com/v2/"
DEFAULT_TIMEOUT_SECONDS = 10.0


class SingularityClientError(Exception):
    """Base exception for Singularity client failures."""


class SingularityTransportError(SingularityClientError):
    """Raised when a Singularity request fails at the transport layer."""


class SingularityTimeoutError(SingularityTransportError):
    """Raised when a Singularity request times out."""


class SingularityApiError(SingularityClientError):
    """Raised when Singularity returns a non-successful HTTP response."""

    def __init__(self, status_code: int, response_body: bytes) -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Singularity API returned HTTP {status_code}")


class SingularityClient:
    """Asynchronous HTTP client for the Singularity API v2."""

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx2.Timeout = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_token.strip():
            raise ValueError("Singularity API token must not be empty")

        normalized_base_url = f"{base_url.rstrip('/')}/"
        self._client = httpx2.AsyncClient(
            base_url=normalized_base_url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Any | None = None,
        json: Any | None = None,
    ) -> httpx2.Response:
        """Send a request to a relative Singularity API endpoint."""
        parsed_path = urlsplit(path)
        if path.startswith("//") or parsed_path.scheme or parsed_path.netloc:
            raise ValueError("Singularity request path must be relative")
        if _contains_dot_segment(parsed_path.path):
            raise ValueError("Singularity request path must not contain dot segments")

        relative_path = path.lstrip("/")
        try:
            response = await self._client.request(
                method,
                relative_path,
                params=params,
                json=json,
            )
        except httpx2.TimeoutException as exc:
            raise SingularityTimeoutError("Singularity API request timed out") from exc
        except httpx2.RequestError as exc:
            raise SingularityTransportError("Singularity API request failed") from exc

        if not response.is_success:
            raise SingularityApiError(response.status_code, response.content)

        return response

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc_value, traceback)


def _contains_dot_segment(path: str) -> bool:
    decoded_path = path
    while True:
        previous_path = decoded_path
        decoded_path = unquote(decoded_path)
        if decoded_path == previous_path:
            break

    return any(segment in {".", ".."} for segment in decoded_path.split("/"))
