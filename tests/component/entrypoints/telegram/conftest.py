from collections.abc import Callable
from typing import Any

import pytest
from aiogram.enums import ChatType
from aiogram.types import Message


@pytest.fixture
def make_telegram_message() -> Callable[..., Message]:
    def factory(
        *,
        text: str | None,
        sender_id: int | None,
        chat_type: ChatType,
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

    return factory
