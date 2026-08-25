from secrets import compare_digest
from typing import Annotated, Any

from aiogram import Bot
from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from productivity_bot.application.ports import TelegramUpdateInboxRepository

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
TELEGRAM_ALLOWED_UPDATE_TYPES = ("message",)


class TelegramWebhookHandler:
    def __init__(
        self,
        *,
        bot: Bot,
        webhook_secret: str,
        update_inbox_repository: TelegramUpdateInboxRepository,
    ) -> None:
        self._bot = bot
        self._webhook_secret = webhook_secret
        self._update_inbox_repository = update_inbox_repository
        self.router = APIRouter(tags=["telegram"])
        self.router.add_api_route(
            TELEGRAM_WEBHOOK_PATH,
            self.handle,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
        )

    async def handle(
        self,
        update_data: dict[str, Any],
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

        update = self._parse_update(update_data)
        await self._update_inbox_repository.insert_update(
            update.update_id,
            update_data,
        )
        return Response(status_code=status.HTTP_200_OK)

    def _parse_update(self, update_data: dict[str, Any]) -> Update:
        try:
            return Update.model_validate(
                update_data,
                context={"bot": self._bot},
            )
        except ValidationError as error:
            errors = [
                {
                    **validation_error,
                    "loc": ("body", *validation_error["loc"]),
                }
                for validation_error in error.errors()
            ]
            raise RequestValidationError(errors, body=update_data) from error
