"""add telegram update claim tracking

Revision ID: 0f7c53e5e6a1
Revises: 5951d37231e4
Create Date: 2026-08-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f7c53e5e6a1"
down_revision: str | Sequence[str] | None = "5951d37231e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_update_inbox",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telegram_update_inbox",
        sa.Column(
            "external_mutation_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_telegram_update_inbox_abandoned",
        "telegram_update_inbox",
        ["status", "claimed_at"],
        unique=False,
    )

    inbox = sa.table(
        "telegram_update_inbox",
        sa.column("status", sa.String()),
        sa.column("last_error", sa.Text()),
    )
    op.execute(
        inbox.update()
        .where(inbox.c.status == "processing")
        .values(
            status="uncertain",
            last_error=(
                "Processing claim predates recoverable claim tracking; "
                "external outcome may be unknown"
            ),
        )
    )
    op.create_check_constraint(
        "ck_telegram_update_inbox_processing_has_claimed_at",
        "telegram_update_inbox",
        "status != 'processing' OR claimed_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_telegram_update_inbox_processing_has_claimed_at",
        "telegram_update_inbox",
        type_="check",
    )
    op.drop_index(
        "ix_telegram_update_inbox_abandoned",
        table_name="telegram_update_inbox",
    )
    op.drop_column(
        "telegram_update_inbox",
        "external_mutation_started_at",
    )
    op.drop_column("telegram_update_inbox", "claimed_at")
