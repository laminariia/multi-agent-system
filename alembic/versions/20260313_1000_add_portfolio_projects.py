"""Add portfolio_projects table.

Stores portfolio entries used as social proof in bids.

Revision ID: 20260313_1000
Revises: 20260311_0100
Create Date: 2026-03-13 10:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260313_1000"
down_revision = "20260311_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_projects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("platform", sa.String(50), server_default="direct", nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tech_stack", JSONB(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_portfolio_platform", "portfolio_projects", ["platform", "status"])


def downgrade() -> None:
    op.drop_index("idx_portfolio_platform", table_name="portfolio_projects")
    op.drop_table("portfolio_projects")
