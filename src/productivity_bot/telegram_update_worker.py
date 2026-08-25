import asyncio
import logging
import signal

from productivity_bot.bootstrap.telegram_update_worker import (
    run_telegram_update_worker,
)
from productivity_bot.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def run_worker_process(
    *,
    settings: Settings | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    process_shutdown_event = shutdown_event or asyncio.Event()
    installed_signals: list[signal.Signals] = []
    if shutdown_event is None:
        loop = asyncio.get_running_loop()
        for process_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    process_signal,
                    process_shutdown_event.set,
                )
            except NotImplementedError:
                logger.warning(
                    "Signal handlers are not supported by this event loop"
                )
                break
            installed_signals.append(process_signal)

    try:
        await run_telegram_update_worker(
            settings or get_settings(),
            process_shutdown_event,
        )
    finally:
        if installed_signals:
            loop = asyncio.get_running_loop()
            for process_signal in installed_signals:
                loop.remove_signal_handler(process_signal)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker_process())
