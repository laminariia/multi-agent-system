"""Add platform_bid_id column to bids table.

Stores the external platform's bid/proposal identifier (e.g. Freelancer bid ID)
so the system can fetch messages and track bid status on the platform.

Revision ID: 20260311_0100
Revises: 20260310_1800
Create Date: 2026-03-11 01:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260311_0100"
down_revision = "20260310_1800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bids", sa.Column("platform_bid_id", sa.String(255), nullable=True))
    op.create_index("idx_bids_platform_bid_id", "bids", ["platform_bid_id"])


def downgrade() -> None:
    op.drop_index("idx_bids_platform_bid_id", table_name="bids")
    op.drop_column("bids", "platform_bid_id")
