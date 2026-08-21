from collections.abc import Set as AbstractSet
from functools import partial

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.methods import SendMessage
from aiogram.types import Message

from productivity_bot.application.use_cases import CaptureTask


def is_authorized_task_capture_message(
    message: Message,
    *,
    allowed_user_ids: AbstractSet[int],
) -> bool:
    sender = message.from_user
    return (
        message.text is not None
        and bool(message.text.strip())
        and not message.text.startswith("/")
        and message.chat.type == ChatType.PRIVATE
        and sender is not None
        and sender.id in allowed_user_ids
    )


class CaptureTaskHandler:
    def __init__(
        self,
        capture_task: CaptureTask,
        *,
        allowed_user_ids: AbstractSet[int],
    ) -> None:
        self._capture_task = capture_task
        self.router = Router()
        message_filter = partial(
            is_authorized_task_capture_message,
            allowed_user_ids=frozenset(allowed_user_ids),
        )
        self.router.message.register(self.handle, message_filter)

    async def handle(self, message: Message) -> SendMessage:
        message_text = message.text
        if message_text is None:
            raise ValueError("Task capture message must contain text")

        await self._capture_task.execute(message_text)
        return message.answer("Task captured")
