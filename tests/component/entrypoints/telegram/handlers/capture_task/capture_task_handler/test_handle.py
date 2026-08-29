from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.methods import SendMessage
from aiogram.types import Message, Update

from productivity_bot.application.use_cases import CaptureTask
from productivity_bot.entrypoints.telegram.handlers.capture_task import (
    CaptureTaskHandler,
)
from productivity_bot.entrypoints.telegram.update_worker import (
    TelegramUpdateProcessingAttempt,
)

ALLOWED_USER_IDS = frozenset({123})


class FakeBot:
    id = 123456


async def dispatch_message(
    handler: CaptureTaskHandler,
    message: Message,
    *,
    processing_attempt: TelegramUpdateProcessingAttempt | None = None,
) -> object:
    dispatcher = Dispatcher()
    dispatcher.include_router(handler.router)
    return await dispatcher.feed_update(
        cast(Bot, FakeBot()),
        Update(update_id=1, message=message),
        **(
            {"processing_attempt": processing_attempt}
            if processing_attempt is not None
            else {}
        ),
    )


@pytest.mark.parametrize(
    ("text", "sender_id", "chat_type"),
    [
        pytest.param(None, 123, ChatType.PRIVATE, id="missing-text"),
        pytest.param("", 123, ChatType.PRIVATE, id="empty-text"),
        pytest.param(" ", 123, ChatType.PRIVATE, id="blank-text"),
        pytest.param("\t\n", 123, ChatType.PRIVATE, id="whitespace-text"),
        pytest.param("/start", 123, ChatType.PRIVATE, id="start-command"),
        pytest.param("/help", 123, ChatType.PRIVATE, id="help-command"),
        pytest.param("/future_command", 123, ChatType.PRIVATE, id="future-command"),
        pytest.param("/start details", 123, ChatType.PRIVATE, id="command-with-args"),
        pytest.param("/start@my_bot", 123, ChatType.PRIVATE, id="targeted-command"),
        pytest.param("Buy groceries", 456, ChatType.PRIVATE, id="unlinked-user"),
        pytest.param("Buy groceries", None, ChatType.PRIVATE, id="missing-sender"),
        pytest.param("Buy groceries", 123, ChatType.GROUP, id="group"),
        pytest.param("Buy groceries", 123, ChatType.SUPERGROUP, id="supergroup"),
        pytest.param("Buy groceries", 123, ChatType.CHANNEL, id="channel"),
    ],
)
@pytest.mark.asyncio
async def test_messages_outside_capture_scope_do_not_route_to_use_case(
    text: str | None,
    sender_id: int | None,
    chat_type: ChatType,
    make_telegram_message: Callable[..., Message],
) -> None:
    capture_task = AsyncMock(spec=CaptureTask)
    handler = CaptureTaskHandler(
        cast(CaptureTask, capture_task),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    result = await dispatch_message(
        handler,
        make_telegram_message(
            text=text,
            sender_id=sender_id,
            chat_type=chat_type,
        ),
    )

    capture_task.execute.assert_not_awaited()
    assert not isinstance(result, SendMessage)


@pytest.mark.asyncio
async def test_authorized_private_text_routes_and_records_marker_before_capture(
    make_telegram_message: Callable[..., Message],
) -> None:
    events: list[str] = []
    capture_task = AsyncMock(spec=CaptureTask)
    handler = CaptureTaskHandler(
        cast(CaptureTask, capture_task),
        allowed_user_ids=ALLOWED_USER_IDS,
    )
    processing_attempt = create_autospec(
        TelegramUpdateProcessingAttempt,
        instance=True,
        spec_set=True,
    )
    processing_attempt.mark_external_mutation_started.side_effect = lambda: events.append(
        "mutation_marker"
    )
    capture_task.execute.side_effect = lambda _: events.append("capture_task")

    result = await dispatch_message(
        handler,
        make_telegram_message(
            text="  Buy groceries  ",
            sender_id=123,
            chat_type=ChatType.PRIVATE,
        ),
        processing_attempt=cast(TelegramUpdateProcessingAttempt, processing_attempt),
    )

    assert events == ["mutation_marker", "capture_task"]
    capture_task.execute.assert_awaited_once_with("  Buy groceries  ")
    assert isinstance(result, SendMessage)
    assert result.text == "Task captured"
