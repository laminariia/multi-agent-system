"""add_orchestrator_goals

Revision ID: a1b2c3d4e5f6
Revises: e4a7c8d93f2b
Create Date: 2026-02-11 20:00:00.000000

Migrates orchestrator goals from local YAML file to PostgreSQL so that
both the Railway-hosted dashboard and the local Telegram bot share the
same goal state.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e4a7c8d93f2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orchestrator_goals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("goal_id", sa.String(20), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("priority", sa.String(20), server_default="medium", nullable=False),
        sa.Column("category", sa.String(30), server_default="feature", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("success_criteria", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("depends_on", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id"),
    )
    op.create_index("idx_orch_goals_status", "orchestrator_goals", ["status"])
    op.create_index("idx_orch_goals_goal_id", "orchestrator_goals", ["goal_id"])


def downgrade() -> None:
    op.drop_index("idx_orch_goals_goal_id", table_name="orchestrator_goals")
    op.drop_index("idx_orch_goals_status", table_name="orchestrator_goals")
    op.drop_table("orchestrator_goals")
