"""Reconcile the schema with the SQLAlchemy models

``alembic check`` found drift between the models and the migration chain.
Where the database was wider (string lengths, extra column, indexes), the
models were aligned with it; this migration covers the rest:

- create ``email_suppression_list``: the model existed, the table never did;
- ``leads.lead_score`` DOUBLE -> INTEGER and ``leads.google_rating``
  DOUBLE -> NUMERIC(2, 1), the types the models and the code use;
- NOT NULL on the columns the models declare non-nullable, after
  backfilling NULLs (timestamps with now(), ``touch_count`` with 0).

Revision ID: c7d1e5f28a94
Revises: b4c8f2e17d03
Create Date: 2026-09-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d1e5f28a94"
down_revision: str | None = "b4c8f2e17d03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, value for existing NULLs)
_NOT_NULL = (
    ("client_context", "updated_at", sa.func.now()),
    ("client_messages", "created_at", sa.func.now()),
    ("hitl_edit_history", "edited_at", sa.func.now()),
    ("leads", "touch_count", 0),
    ("negotiations", "created_at", sa.func.now()),
    ("negotiations", "updated_at", sa.func.now()),
    ("scheduled_messages", "created_at", sa.func.now()),
    ("telegram_notification_log", "created_at", sa.func.now()),
    ("telegram_notification_prefs", "created_at", sa.func.now()),
    ("telegram_user_profiles", "created_at", sa.func.now()),
    ("touch_history", "created_at", sa.func.now()),
)


def upgrade() -> None:
    op.create_table(
        "email_suppression_list",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_suppression_email", "email_suppression_list", ["email"])

    # The float default ('0.0') is dropped before the type change and restored as an integer.
    op.alter_column("leads", "lead_score", server_default=None)
    op.alter_column(
        "leads",
        "lead_score",
        type_=sa.Integer(),
        existing_type=sa.Float(),
        postgresql_using="round(lead_score)::integer",
    )
    op.alter_column("leads", "lead_score", server_default="0")
    op.alter_column(
        "leads",
        "google_rating",
        type_=sa.Numeric(2, 1),
        existing_type=sa.Float(),
        postgresql_using="round(google_rating::numeric, 1)",
    )

    for table, column, value in _NOT_NULL:
        tbl = sa.table(table, sa.column(column))
        op.execute(tbl.update().where(tbl.c[column].is_(None)).values({column: value}))
        op.alter_column(table, column, nullable=False)


def downgrade() -> None:
    for table, column, _ in reversed(_NOT_NULL):
        op.alter_column(table, column, nullable=True)

    op.alter_column("leads", "google_rating", type_=sa.Float(), existing_type=sa.Numeric(2, 1))
    op.alter_column("leads", "lead_score", server_default=None)
    op.alter_column("leads", "lead_score", type_=sa.Float(), existing_type=sa.Integer())
    op.alter_column("leads", "lead_score", server_default="0.0")

    op.drop_index("idx_suppression_email", table_name="email_suppression_list")
    op.drop_table("email_suppression_list")
