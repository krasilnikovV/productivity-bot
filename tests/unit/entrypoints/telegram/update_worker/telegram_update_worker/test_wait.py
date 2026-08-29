from unittest.mock import AsyncMock, patch

import pytest

from productivity_bot.entrypoints.telegram.update_worker import TelegramUpdateWorker
from tests.unit.entrypoints.telegram.update_worker.helpers import (
    make_dispatcher,
    make_repository,
    make_worker,
)


@pytest.mark.asyncio
async def test_wait_surfaces_unexpected_processing_loop_failure() -> None:
    repository = make_repository()
    worker = make_worker(repository, make_dispatcher(), poll_interval=1.0)
    processing_loop = AsyncMock(side_effect=RuntimeError("processing loop crashed"))

    with patch.object(TelegramUpdateWorker, "_processing_loop", processing_loop):
        await worker.start()
        try:
            with pytest.raises(
                RuntimeError,
                match="failed unexpectedly",
            ) as error_info:
                await worker.wait()
        finally:
            await worker.stop()

    processing_loop.assert_awaited_once_with()
    assert isinstance(error_info.value.__cause__, RuntimeError)
    assert str(error_info.value.__cause__) == "processing loop crashed"
