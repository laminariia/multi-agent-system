"""Add client_messages and negotiations tables, extend bids

Revision ID: b4c8f2e17d03
Revises: b4c8d2e19f03
Create Date: 2026-03-20 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c8f2e17d03"
down_revision: str | None = "b4c8d2e19f03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- bids: add negotiation-related columns --------------------------------
    op.add_column(
        "bids",
        sa.Column("platform_thread_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "bids",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bids",
        sa.Column("client_telegram_id", sa.BigInteger(), nullable=True),
    )

    # -- client_messages ------------------------------------------------------
    op.create_table(
        "client_messages",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("sender", sa.String(20), nullable=False),
        sa.Column("message_type", sa.String(30), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column(
            "auto_generated",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "hitl_reviewed",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("hitl_id", sa.Uuid(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bids.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["hitl_id"],
            ["hitl_queue.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_client_messages_bid_id", "client_messages", ["bid_id"])
    op.create_index(
        "idx_client_messages_bid_created",
        "client_messages",
        ["bid_id", "created_at"],
    )
    op.create_index(
        "idx_client_messages_platform_ext",
        "client_messages",
        ["platform", "external_id"],
    )
    op.create_index(
        "idx_client_messages_bid_dir_created",
        "client_messages",
        ["bid_id", "direction", "created_at"],
    )

    # -- negotiations ---------------------------------------------------------
    op.create_table(
        "negotiations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.String(30),
            server_default="initial",
            nullable=False,
        ),
        sa.Column("previous_state", sa.String(30), nullable=True),
        sa.Column(
            "state_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("original_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("current_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("final_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "rounds",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "followup_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column(
            "history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["bid_id"],
            ["bids.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bid_id", name="uq_negotiations_bid_id"),
    )
    op.create_index("idx_negotiations_bid", "negotiations", ["bid_id"])
    op.create_index(
        "idx_negotiations_active_state",
        "negotiations",
        ["state"],
        postgresql_where=sa.text("state NOT IN ('won', 'lost')"),
    )


def downgrade() -> None:
    # -- negotiations ---------------------------------------------------------
    op.drop_index("idx_negotiations_active_state", table_name="negotiations")
    op.drop_index("idx_negotiations_bid", table_name="negotiations")
    op.drop_table("negotiations")

    # -- client_messages ------------------------------------------------------
    op.drop_index("idx_client_messages_bid_dir_created", table_name="client_messages")
    op.drop_index("idx_client_messages_platform_ext", table_name="client_messages")
    op.drop_index("idx_client_messages_bid_created", table_name="client_messages")
    op.drop_index("ix_client_messages_bid_id", table_name="client_messages")
    op.drop_table("client_messages")

    # -- bids: remove negotiation-related columns -----------------------------
    op.drop_column("bids", "client_telegram_id")
    op.drop_column("bids", "last_polled_at")
    op.drop_column("bids", "platform_thread_id")
