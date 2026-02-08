"""add_user_status_column

Revision ID: b8d2f4a61e3c
Revises: a7f3e1b92d4c
Create Date: 2026-02-08 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d2f4a61e3c"
down_revision: Union[str, None] = "a7f3e1b92d4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
    )
    op.create_index("idx_users_status", "users", ["status"])


def downgrade() -> None:
    op.drop_index("idx_users_status", table_name="users")
    op.drop_column("users", "status")
