from collections.abc import Callable
from typing import cast

from fastapi.testclient import TestClient

from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import Settings


def test_health_endpoint(make_settings: Callable[..., Settings]) -> None:
    app = create_app(
        make_settings(),
        telegram_update_inbox_repository=cast(
            TelegramUpdateInboxRepository,
            object(),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
