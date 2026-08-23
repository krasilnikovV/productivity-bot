"""create telegram update inbox

Revision ID: 5951d37231e4
Revises:
Create Date: 2026-08-23 02:51:35.866113

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5951d37231e4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_update_inbox",
        sa.Column(
            "update_id",
            sa.BigInteger(),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_update_inbox_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "status IN "
            "('pending', 'processing', 'succeeded', 'failed', 'uncertain')",
            name="ck_telegram_update_inbox_status",
        ),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_index(
        "ix_telegram_update_inbox_ready",
        "telegram_update_inbox",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_update_inbox_ready",
        table_name="telegram_update_inbox",
    )
    op.drop_table("telegram_update_inbox")
