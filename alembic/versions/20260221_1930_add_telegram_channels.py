"""add_telegram_channels

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-21 19:30:00.000000

Add telegram_channels table for managing monitored Telegram channels
that the TelegramListener scrapes for freelance job postings.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("idx_tg_channels_active", "telegram_channels", ["active"])


def downgrade() -> None:
    op.drop_index("idx_tg_channels_active", table_name="telegram_channels")
    op.drop_table("telegram_channels")
