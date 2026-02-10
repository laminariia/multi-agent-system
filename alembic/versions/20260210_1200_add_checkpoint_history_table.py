"""add_checkpoint_history_table

Revision ID: d9e3f5c72a1b
Revises: b8d2f4a61e3c
Create Date: 2026-02-10 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e3f5c72a1b"
down_revision: str | None = "b8d2f4a61e3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "langgraph_checkpoint_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("state_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_checkpoint_history_thread",
        "langgraph_checkpoint_history",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "idx_checkpoint_history_checkpoint",
        "langgraph_checkpoint_history",
        ["thread_id", "checkpoint_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_checkpoint_history_checkpoint",
        table_name="langgraph_checkpoint_history",
    )
    op.drop_index(
        "idx_checkpoint_history_thread",
        table_name="langgraph_checkpoint_history",
    )
    op.drop_table("langgraph_checkpoint_history")
