import asyncio
import logging
from secrets import compare_digest
from typing import Annotated

from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod
from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Response, status

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
logger = logging.getLogger(__name__)


class TelegramWebhookHandler:
    def __init__(
        self,
        *,
        bot: Bot,
        dispatcher: Dispatcher,
        webhook_secret: str,
    ) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._webhook_secret = webhook_secret
        self._update_tasks: set[asyncio.Task[None]] = set()
        self.router = APIRouter(tags=["telegram"])
        self.router.add_api_route(
            TELEGRAM_WEBHOOK_PATH,
            self.handle,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
        )

    async def handle(
        self,
        update: Update,
        secret_token: Annotated[
            str | None,
            Header(alias="X-Telegram-Bot-Api-Secret-Token"),
        ] = None,
    ) -> Response:
        if secret_token is None or not compare_digest(
            secret_token,
            self._webhook_secret,
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        task = asyncio.create_task(self._process_update(update))
        self._update_tasks.add(task)
        task.add_done_callback(self._update_tasks.discard)
        return Response(status_code=status.HTTP_200_OK)

    async def _process_update(self, update: Update) -> None:
        try:
            result = await self._dispatcher.feed_update(self._bot, update)
            if isinstance(result, TelegramMethod):
                await self._dispatcher.silent_call_request(
                    bot=self._bot,
                    result=result,
                )
        except Exception:
            logger.exception("Failed to process Telegram update %s", update.update_id)

    async def wait_closed(self) -> None:
        if self._update_tasks:
            await asyncio.gather(*tuple(self._update_tasks))
