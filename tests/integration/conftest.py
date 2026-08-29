from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).parents[2]


class DatabaseSettings(BaseSettings):
    database_url: SecretStr

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
        extra="ignore",
    )


def upgrade_to_head(connection: Connection) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    database_url = DatabaseSettings().database_url.get_secret_value()
    schema_name = f"test_postgres_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    schema_engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema_name}},
    )
    schema_created = False

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(CreateSchema(schema_name))
        schema_created = True
        async with schema_engine.begin() as connection:
            await connection.run_sync(upgrade_to_head)
        yield schema_engine
    finally:
        await schema_engine.dispose()
        try:
            if schema_created:
                async with admin_engine.begin() as connection:
                    await connection.execute(DropSchema(schema_name, cascade=True))
        finally:
            await admin_engine.dispose()


@pytest.fixture
def session_factory(
    postgres_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(postgres_engine, expire_on_commit=False)
