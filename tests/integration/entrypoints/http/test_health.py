from typing import cast

from fastapi.testclient import TestClient

from productivity_bot.application.ports import TelegramUpdateInboxRepository
from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import Settings


def test_health_endpoint() -> None:
    settings = Settings(
        telegram_bot_token="123456:test-token",
        telegram_allowed_user_ids=frozenset({123}),
        telegram_webhook_secret="test_webhook_secret",
        singularity_api_token="test-singularity-token",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        _env_file=None,
    )

    app = create_app(
        settings,
        telegram_update_inbox_repository=cast(
            TelegramUpdateInboxRepository,
            object(),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
