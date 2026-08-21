from collections.abc import Sequence
from typing import Any

import pytest
from aiogram.enums import ChatType
from aiogram.types import Message

from productivity_bot.application.use_cases import CaptureTask
from productivity_bot.domain.entities import Task
from productivity_bot.entrypoints.telegram.handlers.capture_task import (
    CaptureTaskHandler,
    is_authorized_task_capture_message,
)

ALLOWED_USER_IDS = frozenset({123})


class FakeTaskRepository:
    def __init__(self) -> None:
        self.created_titles: list[str] = []

    async def create_task(self, title: str) -> Task:
        self.created_titles.append(title)
        return Task(id="T-123", title=title)

    async def list_active_tasks(self) -> Sequence[Task]:
        return []

    async def complete_task(self, task_id: str) -> None:
        return None


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


def matches_task_capture(message: Message) -> bool:
    return is_authorized_task_capture_message(
        message,
        allowed_user_ids=ALLOWED_USER_IDS,
    )


def test_authorized_private_text_matches_task_capture_filter() -> None:
    assert matches_task_capture(make_message("Buy groceries")) is True


@pytest.mark.parametrize("text", ["", " ", "\t\n"])
def test_blank_text_does_not_match_task_capture_filter(text: str) -> None:
    assert matches_task_capture(make_message(text)) is False


@pytest.mark.parametrize(
    "command",
    ["/start", "/help", "/future_command", "/start details", "/start@my_bot"],
)
def test_bot_command_does_not_match_task_capture_filter(command: str) -> None:
    assert matches_task_capture(make_message(command)) is False


def test_unlinked_user_does_not_match_task_capture_filter() -> None:
    assert matches_task_capture(make_message("Buy groceries", sender_id=456)) is False


def test_message_without_sender_does_not_match_task_capture_filter() -> None:
    assert matches_task_capture(make_message("Buy groceries", sender_id=None)) is False


@pytest.mark.parametrize(
    "chat_type",
    [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL],
)
def test_non_private_chat_does_not_match_task_capture_filter(
    chat_type: ChatType,
) -> None:
    assert matches_task_capture(make_message("Buy groceries", chat_type=chat_type)) is False


@pytest.mark.asyncio
async def test_handler_rejects_message_without_text() -> None:
    repository = FakeTaskRepository()
    handler = CaptureTaskHandler(
        CaptureTask(repository),
        allowed_user_ids=ALLOWED_USER_IDS,
    )

    with pytest.raises(ValueError, match="Task capture message must contain text"):
        await handler.handle(make_message(None))

    assert repository.created_titles == []
