from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from typing import cast
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.methods import SendMessage
from aiogram.types import Message, Update

from productivity_bot.application.use_cases import GetNextAction
from productivity_bot.domain.entities import Task, TaskPriority
from productivity_bot.entrypoints.telegram.handlers.next_action import (
    NextActionHandler,
)
from productivity_bot.entrypoints.telegram.update_worker import (
    TelegramUpdateProcessingAttempt,
)

ALLOWED_USER_IDS = frozenset({123})


def fixed_datetime(now: datetime) -> type[datetime]:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    return FixedDatetime


class FakeBot:
    id = 123456


async def dispatch_message(
    handler: NextActionHandler,
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
        pytest.param("next", 123, ChatType.PRIVATE, id="plain-text"),
        pytest.param("/start", 123, ChatType.PRIVATE, id="other-command"),
        pytest.param("/next", 456, ChatType.PRIVATE, id="unlinked-user"),
        pytest.param("/next", None, ChatType.PRIVATE, id="missing-sender"),
        pytest.param("/next", 123, ChatType.GROUP, id="group"),
        pytest.param("/next", 123, ChatType.SUPERGROUP, id="supergroup"),
        pytest.param("/next", 123, ChatType.CHANNEL, id="channel"),
    ],
)
@pytest.mark.asyncio
async def test_messages_outside_next_action_scope_do_not_route_to_use_case(
    text: str | None,
    sender_id: int | None,
    chat_type: ChatType,
    make_telegram_message: Callable[..., Message],
) -> None:
    get_next_action = AsyncMock(spec=GetNextAction)
    handler = NextActionHandler(
        cast(GetNextAction, get_next_action),
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

    get_next_action.execute.assert_not_awaited()
    assert not isinstance(result, SendMessage)


@pytest.mark.asyncio
async def test_next_command_returns_selected_task_without_mutation_marker(
    make_telegram_message: Callable[..., Message],
) -> None:
    selected_task = Task(
        id="T-selected",
        title="Selected task",
        priority=TaskPriority.HIGH,
    )
    get_next_action = AsyncMock(spec=GetNextAction)
    get_next_action.execute.return_value = selected_task
    handler = NextActionHandler(
        cast(GetNextAction, get_next_action),
        allowed_user_ids=ALLOWED_USER_IDS,
    )
    processing_attempt = create_autospec(
        TelegramUpdateProcessingAttempt,
        instance=True,
        spec_set=True,
    )

    result = await dispatch_message(
        handler,
        make_telegram_message(
            text="/next",
            sender_id=123,
            chat_type=ChatType.PRIVATE,
        ),
        processing_attempt=processing_attempt,
    )

    get_next_action.execute.assert_awaited_once()
    processing_attempt.mark_external_mutation_started.assert_not_awaited()
    assert isinstance(result, SendMessage)
    assert result.text == "Next task: Selected task"


@pytest.mark.asyncio
async def test_next_command_reports_when_no_candidate_exists(
    make_telegram_message: Callable[..., Message],
) -> None:
    get_next_action = AsyncMock(spec=GetNextAction)
    get_next_action.execute.return_value = None
    handler = NextActionHandler(
        cast(GetNextAction, get_next_action),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    result = await dispatch_message(
        handler,
        make_telegram_message(
            text="/next",
            sender_id=123,
            chat_type=ChatType.PRIVATE,
        ),
    )

    get_next_action.execute.assert_awaited_once()
    assert isinstance(result, SendMessage)
    assert result.text == "No available tasks"


@pytest.mark.asyncio
async def test_next_command_passes_timezone_aware_utc_reference_once(
    make_telegram_message: Callable[..., Message],
) -> None:
    get_next_action = AsyncMock(spec=GetNextAction)
    get_next_action.execute.return_value = None
    handler = NextActionHandler(
        cast(GetNextAction, get_next_action),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    reference_time = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    with patch(
        "productivity_bot.entrypoints.telegram.handlers.next_action.datetime",
        fixed_datetime(reference_time),
    ):
        await dispatch_message(
            handler,
            make_telegram_message(
                text="/next",
                sender_id=123,
                chat_type=ChatType.PRIVATE,
            ),
        )

    get_next_action.execute.assert_awaited_once()
    (reference,) = get_next_action.execute.await_args.args
    assert reference.tzinfo == UTC
    assert reference == reference_time
