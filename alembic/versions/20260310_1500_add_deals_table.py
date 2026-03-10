"""Add deals table for Pipeline B → A bridge.

Creates the ``deals`` table linking Pipeline B leads to Pipeline A
development cycles. Includes indexes on status and lead_id.

Revision ID: 20260310_1500
Revises: 20260226_1200
Create Date: 2026-03-10 15:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260310_1500"
down_revision = "20260226_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("lead_id", sa.UUID(), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("agreed_scope", sa.Text(), nullable=True),
        sa.Column("budget", sa.Numeric(12, 2), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_context", JSONB(), nullable=True),
        sa.Column("design_versions", JSONB(), nullable=True),
        sa.Column("conversation_history", JSONB(), nullable=True),
        sa.Column("pipeline_a_thread_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_deals_status", "deals", ["status"])
    op.create_index("idx_deals_lead_id", "deals", ["lead_id"])


def downgrade() -> None:
    op.drop_index("idx_deals_lead_id", table_name="deals")
    op.drop_index("idx_deals_status", table_name="deals")
    op.drop_table("deals")
