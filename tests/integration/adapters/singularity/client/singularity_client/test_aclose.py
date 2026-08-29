from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from productivity_bot.adapters.singularity import SingularityClient


@pytest.mark.asyncio
async def test_aclose_closes_transport_and_prevents_further_requests() -> None:
    handler = AsyncMock(return_value=httpx2.Response(200))
    transport = httpx2.MockTransport(handler)
    client = SingularityClient("token", transport=transport)

    with patch.object(
        transport,
        "aclose",
        new_callable=AsyncMock,
        wraps=transport.aclose,
    ) as close_transport:
        await client.aclose()

    close_transport.assert_awaited_once_with()
    with pytest.raises(RuntimeError, match="client is closed"):
        await client.request("GET", "task")

    handler.assert_not_awaited()
