from unittest.mock import AsyncMock

import httpx2
import pytest

from productivity_bot.adapters.singularity import SingularityClient


@pytest.mark.parametrize("api_token", ["", " ", "\t\n"])
def test_empty_token_is_rejected_before_transport(api_token: str) -> None:
    handler = AsyncMock(return_value=httpx2.Response(200))
    transport = httpx2.MockTransport(handler)

    with pytest.raises(ValueError, match="token must not be empty"):
        SingularityClient(api_token, transport=transport)

    handler.assert_not_awaited()
