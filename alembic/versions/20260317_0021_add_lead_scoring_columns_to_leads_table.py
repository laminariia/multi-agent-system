"""Add lead scoring columns to leads table

Revision ID: 071bb4b55e18
Revises: 20260313_1000
Create Date: 2026-03-17 00:21:19.388880

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "071bb4b55e18"
down_revision: str | None = "20260313_1000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("lead_score", sa.Float(), server_default="0.0", nullable=True))
    op.add_column("leads", sa.Column("temperature", sa.String(length=20), server_default="cold", nullable=True))
    op.add_column("leads", sa.Column("touch_count", sa.Integer(), server_default="0", nullable=True))
    op.add_column("leads", sa.Column("channel_used", sa.String(length=50), nullable=True))
    op.add_column("leads", sa.Column("google_rating", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("review_count", sa.Integer(), server_default="0", nullable=True))
    op.add_column("leads", sa.Column("source", sa.String(length=100), nullable=True))
    op.add_column("leads", sa.Column("source_id", sa.String(length=255), nullable=True))
    op.add_column(
        "leads", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=True)
    )
    op.add_column("leads", sa.Column("company_name", sa.String(length=255), nullable=True))
    op.add_column("leads", sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_leads_temperature", "leads", ["temperature"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_leads_temperature", table_name="leads")
    op.drop_column("leads", "last_contacted_at")
    op.drop_column("leads", "company_name")
    op.drop_column("leads", "updated_at")
    op.drop_column("leads", "source_id")
    op.drop_column("leads", "source")
    op.drop_column("leads", "review_count")
    op.drop_column("leads", "google_rating")
    op.drop_column("leads", "channel_used")
    op.drop_column("leads", "touch_count")
    op.drop_column("leads", "temperature")
    op.drop_column("leads", "lead_score")
