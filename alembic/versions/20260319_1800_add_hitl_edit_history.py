"""Add hitl_edit_history table for tracking payload edits

Revision ID: a3b7e9d14c02
Revises: a3c9e1f28d47
Create Date: 2026-03-19 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b7e9d14c02"
down_revision: str | None = "a3c9e1f28d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hitl_edit_history",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("hitl_id", sa.Uuid(), nullable=False),
        sa.Column("edited_by", sa.Uuid(), nullable=True),
        sa.Column("before_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("edit_type", sa.String(50), nullable=False),
        sa.Column(
            "edited_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["hitl_id"],
            ["hitl_queue.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["edited_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_hitl_edit_history_hitl_id",
        "hitl_edit_history",
        ["hitl_id"],
    )
    op.create_index(
        "idx_hitl_edit_history_edited_at",
        "hitl_edit_history",
        ["edited_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_hitl_edit_history_edited_at", table_name="hitl_edit_history")
    op.drop_index("idx_hitl_edit_history_hitl_id", table_name="hitl_edit_history")
    op.drop_table("hitl_edit_history")
