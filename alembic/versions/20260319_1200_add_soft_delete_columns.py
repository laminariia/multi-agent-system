"""Add soft-delete deleted_at columns to high-volume tables

Revision ID: f5a8c3d72b19
Revises: 071bb4b55e18
Create Date: 2026-03-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a8c3d72b19"
down_revision: str | None = "071bb4b55e18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables receiving the soft-delete column.
_TABLES = ("agent_logs", "hitl_queue", "ab_test_results", "scheduled_messages")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "deleted_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=None,
            ),
        )
        # Partial index: only index rows that have been soft-deleted.
        # This keeps the index small and avoids penalising normal queries.
        op.create_index(
            f"idx_{table}_deleted_at",
            table,
            ["deleted_at"],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NOT NULL"),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"idx_{table}_deleted_at", table_name=table)
        op.drop_column(table, "deleted_at")
