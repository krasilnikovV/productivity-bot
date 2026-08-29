import asyncio
import signal
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock, call, create_autospec

import pytest

from productivity_bot import telegram_update_worker as process_module
from productivity_bot.config import Settings


def use_controlled_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    loop: asyncio.AbstractEventLoop,
) -> Mock:
    get_running_loop = Mock(return_value=loop)
    monkeypatch.setattr(
        process_module,
        "asyncio",
        SimpleNamespace(
            Event=asyncio.Event,
            get_running_loop=get_running_loop,
        ),
    )
    return get_running_loop


@pytest.mark.asyncio
async def test_process_runner_installs_signals_and_removes_them_after_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    loop = create_autospec(asyncio.AbstractEventLoop, instance=True, spec_set=True)
    get_running_loop = use_controlled_event_loop(monkeypatch, loop)
    received_events: list[asyncio.Event] = []

    async def fake_run_worker(
        actual_settings: Settings,
        actual_shutdown_event: asyncio.Event,
    ) -> None:
        assert actual_settings is settings
        received_events.append(actual_shutdown_event)
        assert loop.add_signal_handler.call_count == 2
        signal_callback = loop.add_signal_handler.call_args_list[1].args[1]
        signal_callback()
        assert actual_shutdown_event.is_set()

    monkeypatch.setattr(
        process_module,
        "run_telegram_update_worker",
        fake_run_worker,
    )

    await process_module.run_worker_process(settings=settings)

    shutdown_event = received_events[0]
    assert loop.add_signal_handler.call_args_list == [
        call(signal.SIGINT, shutdown_event.set),
        call(signal.SIGTERM, shutdown_event.set),
    ]
    assert loop.remove_signal_handler.call_args_list == [
        call(signal.SIGINT),
        call(signal.SIGTERM),
    ]
    assert get_running_loop.call_count == 2


@pytest.mark.asyncio
async def test_process_runner_removes_signals_after_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
) -> None:
    loop = create_autospec(asyncio.AbstractEventLoop, instance=True, spec_set=True)
    use_controlled_event_loop(monkeypatch, loop)

    async def fail_worker(*_: object) -> None:
        raise RuntimeError("worker failed")

    monkeypatch.setattr(process_module, "run_telegram_update_worker", fail_worker)

    with pytest.raises(RuntimeError, match="worker failed"):
        await process_module.run_worker_process(settings=make_settings())

    assert loop.remove_signal_handler.call_args_list == [
        call(signal.SIGINT),
        call(signal.SIGTERM),
    ]


@pytest.mark.asyncio
async def test_process_runner_continues_when_signals_are_unsupported(
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    loop = create_autospec(asyncio.AbstractEventLoop, instance=True, spec_set=True)
    loop.add_signal_handler.side_effect = NotImplementedError
    use_controlled_event_loop(monkeypatch, loop)
    logger = Mock()
    monkeypatch.setattr(process_module, "logger", logger)
    received_events: list[asyncio.Event] = []

    async def fake_run_worker(
        actual_settings: Settings,
        actual_shutdown_event: asyncio.Event,
    ) -> None:
        assert actual_settings is settings
        received_events.append(actual_shutdown_event)

    monkeypatch.setattr(
        process_module,
        "run_telegram_update_worker",
        fake_run_worker,
    )

    await process_module.run_worker_process(settings=settings)

    assert len(received_events) == 1
    assert not received_events[0].is_set()
    loop.add_signal_handler.assert_called_once()
    assert loop.add_signal_handler.call_args.args[0] == signal.SIGINT
    loop.remove_signal_handler.assert_not_called()
    logger.warning.assert_called_once_with(
        "Signal handlers are not supported by this event loop"
    )
