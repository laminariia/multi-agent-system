"""Add scheduled_messages table for delivery throttling.

Creates the ``scheduled_messages`` table used by the execution cloaking
system to store progress-update messages dispatched on a schedule.

Revision ID: 20260310_1800
Revises: 20260310_1500
Create Date: 2026-03-10 18:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260310_1800"
down_revision = "20260310_1500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(20), server_default="platform", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "idx_scheduled_messages_status_send_at",
        "scheduled_messages",
        ["status", "send_at"],
    )
    op.create_index(
        "idx_scheduled_messages_thread",
        "scheduled_messages",
        ["thread_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_scheduled_messages_thread", table_name="scheduled_messages")
    op.drop_index("idx_scheduled_messages_status_send_at", table_name="scheduled_messages")
    op.drop_table("scheduled_messages")
