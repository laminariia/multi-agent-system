"""Add pipeline B tables: telegram_user_profiles, touch_history,
telegram_notification_prefs, telegram_notification_log, client_context,
and lead field extensions.

Revision ID: b4c8d2e19f03
Revises: a3b7e9d14c02
Create Date: 2026-03-20 02:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c8d2e19f03"
down_revision: str | None = "a3b7e9d14c02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Table 1: telegram_user_profiles ---
    op.create_table(
        "telegram_user_profiles",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("last_name", sa.String(255), nullable=True),
        sa.Column(
            "messages_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=True,
        ),
        sa.Column(
            "score",
            sa.Float(),
            server_default="0.0",
            nullable=False,
        ),
        sa.Column("needs", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default="new",
            nullable=False,
        ),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index(
        "idx_tg_profile_status_score",
        "telegram_user_profiles",
        ["status", "score"],
    )

    # --- Table 2: touch_history ---
    op.create_table(
        "touch_history",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_touch_history_lead_created",
        "touch_history",
        ["lead_id", "created_at"],
    )

    # --- Table 3: telegram_notification_prefs ---
    op.create_table(
        "telegram_notification_prefs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "notification_types",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text(
                "ARRAY['bid_approval','dev_launch','final_review',"
                "'design_review','concept_review','escalation']::text[]"
            ),
            nullable=True,
        ),
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "quiet_hours_start",
            sa.Integer(),
            server_default="23",
            nullable=False,
        ),
        sa.Column(
            "quiet_hours_end",
            sa.Integer(),
            server_default="8",
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.String(50),
            server_default="Europe/Moscow",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    # --- Table 4: telegram_notification_log ---
    op.create_table(
        "telegram_notification_log",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("hitl_id", sa.Uuid(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("content_preview", sa.String(200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_taken", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["hitl_id"],
            ["hitl_queue.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_tg_notif_log_user",
        "telegram_notification_log",
        ["user_id"],
    )
    op.create_index(
        "idx_tg_notif_log_hitl",
        "telegram_notification_log",
        ["hitl_id"],
    )
    op.create_index(
        "idx_tg_notif_log_sent",
        "telegram_notification_log",
        ["sent_at"],
    )

    # --- Table 5: client_context ---
    op.create_table(
        "client_context",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("deal_id", sa.Uuid(), nullable=False),
        sa.Column(
            "key_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=True,
        ),
        sa.Column("agreed_scope", sa.Text(), nullable=True),
        sa.Column(
            "decisions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=True,
        ),
        sa.Column(
            "design_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=True,
        ),
        sa.Column(
            "client_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=True,
        ),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"],
            ["deals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deal_id"),
    )
    op.create_index(
        "idx_client_context_lead",
        "client_context",
        ["lead_id"],
    )

    # --- Lead table extensions ---
    op.add_column(
        "leads",
        sa.Column("touch_state", sa.String(20), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("next_touch_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "battlecard_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "scoring_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "leads",
        sa.Column("analysis_tier", sa.String(10), nullable=True),
    )
    op.create_index(
        "idx_leads_touch_state",
        "leads",
        ["touch_state"],
    )


def downgrade() -> None:
    # --- Lead extensions ---
    op.drop_index("idx_leads_touch_state", table_name="leads")
    op.drop_column("leads", "analysis_tier")
    op.drop_column("leads", "scoring_rules")
    op.drop_column("leads", "battlecard_json")
    op.drop_column("leads", "next_touch_at")
    op.drop_column("leads", "touch_state")

    # --- client_context ---
    op.drop_index("idx_client_context_lead", table_name="client_context")
    op.drop_table("client_context")

    # --- telegram_notification_log ---
    op.drop_index("idx_tg_notif_log_sent", table_name="telegram_notification_log")
    op.drop_index("idx_tg_notif_log_hitl", table_name="telegram_notification_log")
    op.drop_index("idx_tg_notif_log_user", table_name="telegram_notification_log")
    op.drop_table("telegram_notification_log")

    # --- telegram_notification_prefs ---
    op.drop_table("telegram_notification_prefs")

    # --- touch_history ---
    op.drop_index("idx_touch_history_lead_created", table_name="touch_history")
    op.drop_table("touch_history")

    # --- telegram_user_profiles ---
    op.drop_index("idx_tg_profile_status_score", table_name="telegram_user_profiles")
    op.drop_table("telegram_user_profiles")
