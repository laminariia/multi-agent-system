"""fix_checkpoint_column_names

Revision ID: e4a7c8d93f2b
Revises: d9e3f5c72a1b
Create Date: 2026-02-10 18:30:00.000000

Renames mismatched columns in langgraph_checkpoints to match the SQL
queries used by src/core/checkpoints.py (HybridCheckpointSaver):

  parent_id  -> parent_checkpoint_id
  checkpoint -> state_data

Also adds three new columns that the checkpoint saver writes:
  current_agent  VARCHAR(100)
  status         VARCHAR(50)
  requires_hitl  BOOLEAN DEFAULT FALSE
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a7c8d93f2b"
down_revision: str | None = "d9e3f5c72a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rename columns to match HybridCheckpointSaver SQL queries
    op.alter_column(
        "langgraph_checkpoints",
        "parent_id",
        new_column_name="parent_checkpoint_id",
    )
    op.alter_column(
        "langgraph_checkpoints",
        "checkpoint",
        new_column_name="state_data",
    )

    # Add new columns used by _pg_put
    op.add_column(
        "langgraph_checkpoints",
        sa.Column("current_agent", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "langgraph_checkpoints",
        sa.Column("status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "langgraph_checkpoints",
        sa.Column(
            "requires_hitl",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    # Drop added columns
    op.drop_column("langgraph_checkpoints", "requires_hitl")
    op.drop_column("langgraph_checkpoints", "status")
    op.drop_column("langgraph_checkpoints", "current_agent")

    # Reverse column renames
    op.alter_column(
        "langgraph_checkpoints",
        "state_data",
        new_column_name="checkpoint",
    )
    op.alter_column(
        "langgraph_checkpoints",
        "parent_checkpoint_id",
        new_column_name="parent_id",
    )
