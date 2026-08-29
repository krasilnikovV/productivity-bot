from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from productivity_bot.adapters.singularity import SingularityClient


@pytest.mark.asyncio
async def test_context_manager_closes_transport() -> None:
    handler = AsyncMock(return_value=httpx2.Response(200))
    transport = httpx2.MockTransport(handler)

    with patch.object(
        transport,
        "aclose",
        new_callable=AsyncMock,
        wraps=transport.aclose,
    ) as close_transport:
        async with SingularityClient("token", transport=transport):
            close_transport.assert_not_awaited()

    close_transport.assert_awaited_once_with()
