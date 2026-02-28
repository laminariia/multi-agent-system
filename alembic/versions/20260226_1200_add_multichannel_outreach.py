"""Add multichannel outreach support.

Adds:
- campaign_leads.channel_type (email/telegram)
- leads.telegram_username
- Index on campaign_leads(channel_type)

Revision ID: 20260226_1200
Revises: 20260224_1200
Create Date: 2026-02-26 12:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260226_1200"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_leads",
        sa.Column(
            "channel_type",
            sa.String(20),
            nullable=False,
            server_default="email",
        ),
    )
    op.add_column(
        "leads",
        sa.Column("telegram_username", sa.String(100), nullable=True),
    )
    op.create_index(
        "idx_campaign_leads_channel",
        "campaign_leads",
        ["channel_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_campaign_leads_channel", table_name="campaign_leads")
    op.drop_column("leads", "telegram_username")
    op.drop_column("campaign_leads", "channel_type")
