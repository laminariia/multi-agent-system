"""Add telegram_notifications audit table

Revision ID: a3c9e1f28d47
Revises: f5a8c3d72b19
Create Date: 2026-03-19 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c9e1f28d47"
down_revision: str | None = "f5a8c3d72b19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_notifications",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "hitl_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "delivered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # Index for querying by user
    op.create_index(
        "idx_telegram_notifications_user_id",
        "telegram_notifications",
        ["user_id"],
    )

    # Index for querying by hitl_id
    op.create_index(
        "idx_telegram_notifications_hitl_id",
        "telegram_notifications",
        ["hitl_id"],
    )

    # Index for querying recent notifications
    op.create_index(
        "idx_telegram_notifications_sent_at",
        "telegram_notifications",
        ["sent_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_telegram_notifications_sent_at", table_name="telegram_notifications")
    op.drop_index("idx_telegram_notifications_hitl_id", table_name="telegram_notifications")
    op.drop_index("idx_telegram_notifications_user_id", table_name="telegram_notifications")
    op.drop_table("telegram_notifications")
