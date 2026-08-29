from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from productivity_bot.config import Settings
from tests.component.entrypoints.telegram.webhook.helpers import (
    DEADLOCK_TIMEOUT,
    WEBHOOK_HEADERS,
    AppFactory,
    make_app_factory,
)


@pytest.fixture
def make_app(
    make_settings: Callable[..., Settings],
) -> AppFactory:
    return make_app_factory(make_settings)


@pytest.mark.filterwarnings(
    "error::pydantic.warnings.UnsupportedFieldAttributeWarning",
)
def test_webhook_accepts_update_while_no_worker_is_running(
    make_app: AppFactory,
) -> None:
    app, _, repository, events = make_app()
    payload = {"update_id": 42}

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json=payload,
        )

    assert response.status_code == 200
    assert response.content == b""
    repository.insert_update.assert_awaited_once_with(42, payload)
    assert events == ["session_close"]


def test_webhook_waits_for_repository_insert_before_acknowledging(
    make_app: AppFactory,
) -> None:
    insert_started = Event()
    insert_release = Event()
    response_completed = Event()
    app, _, _, _ = make_app(
        insert_started=insert_started,
        insert_release=insert_release,
    )

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        def post_update() -> object:
            response = client.post(
                "/telegram/webhook",
                headers=WEBHOOK_HEADERS,
                json={"update_id": 42},
            )
            response_completed.set()
            return response

        response_future = executor.submit(post_update)
        try:
            assert insert_started.wait(timeout=DEADLOCK_TIMEOUT)
            assert not response_completed.is_set()
        finally:
            insert_release.set()
        response = response_future.result(timeout=DEADLOCK_TIMEOUT)

    assert response.status_code == 200


def test_webhook_acknowledges_duplicate_repository_result(
    make_app: AppFactory,
) -> None:
    app, _, repository, _ = make_app()
    payload = {"update_id": 42}
    repository.insert_update.return_value = False

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json=payload,
        )

    assert response.status_code == 200
    repository.insert_update.assert_awaited_once_with(42, payload)


def test_webhook_returns_500_when_insert_fails(make_app: AppFactory) -> None:
    app, _, repository, _ = make_app(insert_error=RuntimeError("database unavailable"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={"update_id": 42},
        )

    assert response.status_code == 500
    repository.insert_update.assert_awaited_once_with(42, {"update_id": 42})


@pytest.mark.parametrize("secret", [None, "incorrect_secret"])
def test_webhook_rejects_missing_or_incorrect_secret(
    secret: str | None,
    make_app: AppFactory,
) -> None:
    app, _, repository, _ = make_app()
    headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=headers,
            json={"update_id": 42},
        )

    assert response.status_code == 401
    repository.insert_update.assert_not_awaited()


def test_webhook_rejects_malformed_update(make_app: AppFactory) -> None:
    app, _, repository, _ = make_app()

    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            headers=WEBHOOK_HEADERS,
            json={},
        )

    assert response.status_code == 422
    repository.insert_update.assert_not_awaited()
