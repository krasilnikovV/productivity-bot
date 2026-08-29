from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from functools import partial

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.methods import SendMessage
from aiogram.types import Message

from productivity_bot.application.use_cases import GetNextAction


def is_authorized_next_action_message(
    message: Message,
    *,
    allowed_user_ids: AbstractSet[int],
) -> bool:
    sender = message.from_user
    return (
        message.chat.type == ChatType.PRIVATE
        and sender is not None
        and sender.id in allowed_user_ids
    )


class NextActionHandler:
    def __init__(
        self,
        get_next_action: GetNextAction,
        *,
        allowed_user_ids: AbstractSet[int],
    ) -> None:
        self._get_next_action = get_next_action
        self.router = Router()
        message_filter = partial(
            is_authorized_next_action_message,
            allowed_user_ids=frozenset(allowed_user_ids),
        )
        self.router.message.register(self.handle, Command("next"), message_filter)

    async def handle(self, message: Message) -> SendMessage:
        task = await self._get_next_action.execute(datetime.now(UTC))
        if task is None:
            return message.answer("No available tasks")
        return message.answer(f"Next task: {task.title}")
