from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.methods import SendMessage
from aiogram.types import Message, Update

from productivity_bot.application.use_cases import GetNextAction
from productivity_bot.domain.entities import Task, TaskPriority
from productivity_bot.entrypoints.telegram.handlers.next_action import (
    NextActionHandler,
    is_authorized_next_action_message,
)

ALLOWED_USER_IDS = frozenset({123})


class FakeTaskRepository:
    def __init__(self, active_tasks: Sequence[Task]) -> None:
        self.active_tasks = active_tasks
        self.list_active_tasks_calls = 0

    async def create_task(self, title: str) -> Task:
        return Task(id="T-created", title=title)

    async def list_active_tasks(self) -> Sequence[Task]:
        self.list_active_tasks_calls += 1
        return self.active_tasks

    async def complete_task(self, task_id: str) -> None:
        return None


class FakeProcessingAttempt:
    def __init__(self) -> None:
        self.marker_calls = 0

    async def mark_external_mutation_started(self) -> None:
        self.marker_calls += 1


class FakeBot:
    id = 123456


def make_message(
    text: str | None,
    *,
    sender_id: int | None = 123,
    chat_type: ChatType = ChatType.PRIVATE,
) -> Message:
    message_data: dict[str, Any] = {
        "message_id": 7,
        "date": 1_754_000_000,
        "chat": {"id": 123, "type": chat_type},
    }
    if text is not None:
        message_data["text"] = text
    if sender_id is not None:
        message_data["from"] = {
            "id": sender_id,
            "is_bot": False,
            "first_name": "Test user",
        }
    return Message.model_validate(message_data)


def matches_next_action(message: Message) -> bool:
    return is_authorized_next_action_message(
        message,
        allowed_user_ids=ALLOWED_USER_IDS,
    )


async def dispatch_message(
    handler: NextActionHandler,
    message: Message,
    *,
    processing_attempt: FakeProcessingAttempt | None = None,
) -> object:
    dispatcher = Dispatcher()
    dispatcher.include_router(handler.router)
    result = await dispatcher.feed_update(
        cast(Bot, FakeBot()),
        Update(update_id=1, message=message),
        **(
            {"processing_attempt": processing_attempt}
            if processing_attempt is not None
            else {}
        ),
    )
    return result


def test_authorized_private_message_matches_next_action_filter() -> None:
    assert matches_next_action(make_message("/next")) is True


@pytest.mark.parametrize(
    "sender_id,chat_type",
    [
        (456, ChatType.PRIVATE),
        (None, ChatType.PRIVATE),
        (123, ChatType.GROUP),
        (123, ChatType.SUPERGROUP),
        (123, ChatType.CHANNEL),
    ],
)
def test_unauthorized_message_does_not_match_next_action_filter(
    sender_id: int | None,
    chat_type: ChatType,
) -> None:
    assert (
        matches_next_action(
            make_message("/next", sender_id=sender_id, chat_type=chat_type)
        )
        is False
    )


@pytest.mark.asyncio
async def test_only_next_command_from_authorized_user_routes_to_use_case() -> None:
    repository = FakeTaskRepository([Task(id="T-1", title="Selected")])
    handler = NextActionHandler(
        GetNextAction(repository),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    result = await dispatch_message(handler, make_message("/next"))

    assert repository.list_active_tasks_calls == 1
    assert isinstance(result, SendMessage)
    assert result.text == "Next task: Selected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,sender_id,chat_type",
    [
        ("/unknown", 123, ChatType.PRIVATE),
        ("/next", 456, ChatType.PRIVATE),
        ("/next", None, ChatType.PRIVATE),
        ("/next", 123, ChatType.GROUP),
        ("/next", 123, ChatType.SUPERGROUP),
        ("/next", 123, ChatType.CHANNEL),
    ],
)
async def test_rejected_messages_do_not_route_to_use_case(
    text: str,
    sender_id: int | None,
    chat_type: ChatType,
) -> None:
    repository = FakeTaskRepository([Task(id="T-1", title="Selected")])
    handler = NextActionHandler(
        GetNextAction(repository),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    result = await dispatch_message(
        handler,
        make_message(text, sender_id=sender_id, chat_type=chat_type),
    )

    assert repository.list_active_tasks_calls == 0
    assert not isinstance(result, SendMessage)


@pytest.mark.asyncio
async def test_next_command_returns_only_selected_task_without_mutation_marker() -> None:
    selected_task = Task(
        id="T-selected",
        title="Selected task",
        priority=TaskPriority.HIGH,
    )
    other_task = Task(id="T-other", title="Other task")
    repository = FakeTaskRepository([other_task, selected_task])
    handler = NextActionHandler(
        GetNextAction(repository),
        allowed_user_ids=ALLOWED_USER_IDS,
    )
    processing_attempt = FakeProcessingAttempt()

    result = await dispatch_message(
        handler,
        make_message("/next"),
        processing_attempt=processing_attempt,
    )

    assert repository.list_active_tasks_calls == 1
    assert processing_attempt.marker_calls == 0
    assert isinstance(result, SendMessage)
    assert result.text == "Next task: Selected task"
    assert "Other task" not in result.text


@pytest.mark.asyncio
async def test_next_command_reports_when_no_candidate_exists() -> None:
    repository = FakeTaskRepository([])
    handler = NextActionHandler(
        GetNextAction(repository),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    result = await dispatch_message(handler, make_message("/next"))

    assert repository.list_active_tasks_calls == 1
    assert isinstance(result, SendMessage)
    assert result.text == "No available tasks"


@pytest.mark.asyncio
async def test_next_command_passes_timezone_aware_utc_reference_once() -> None:
    class RecordingGetNextAction:
        def __init__(self) -> None:
            self.references: list[datetime] = []

        async def execute(self, now: datetime) -> Task | None:
            self.references.append(now)
            return None

    get_next_action = RecordingGetNextAction()
    handler = NextActionHandler(
        cast(GetNextAction, get_next_action),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    await dispatch_message(handler, make_message("/next"))

    assert len(get_next_action.references) == 1
    assert get_next_action.references[0].tzinfo == UTC
