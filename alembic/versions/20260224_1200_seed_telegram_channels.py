"""seed_telegram_channels

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-24 12:00:00.000000

Seed 22 Telegram channels for monitoring (freelance, business, niche).
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = datetime(2026, 2, 24, 12, 0, 0, tzinfo=UTC)

_CHANNELS = [
    # Freelance (8)
    ("freelead", "Фриланс-лиды", "freelance"),
    ("freelancetaverna", "Фриланс-таверна", "freelance"),
    ("freelansim_ru", "Freelansim (RU)", "freelance"),
    ("kwork_market", "Kwork маркет", "freelance"),
    ("frilans", "Фриланс", "freelance"),
    ("FRILANSb", "Фриланс Б", "freelance"),
    ("frilanse", "Фрилансе", "freelance"),
    ("digitaltender", "IT-тендеры", "freelance"),
    # Business chats (10)
    ("onpeak_chat", "Бизнес-чат OnPeak", "business"),
    ("biznes_chat", "Бизнес-чат", "business"),
    ("BiznesKontakti", "Бизнес-контакты", "business"),
    ("bizneschats", "Бизнес-чаты", "business"),
    ("chat_biznes1", "Бизнес-чат 1", "business"),
    ("smp37", "СМП (бизнес)", "business"),
    ("sprosiprobiznes", "Спроси про бизнес", "business"),
    ("hmoffice", "HM Office", "business"),
    ("ipomogator1", "Помощь бизнесу", "business"),
    ("pomogator", "Помогатор", "business"),
    # Niche (2)
    ("imexpert_talk", "Маркетинг/эксперты", "niche"),
    ("resto_business", "HoReCa/рестораны", "niche"),
]


def upgrade() -> None:
    tbl = sa.table(
        "telegram_channels",
        sa.column("username", sa.String),
        sa.column("title", sa.String),
        sa.column("category", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        tbl,
        [
            {
                "username": username,
                "title": title,
                "category": category,
                "active": True,
                "created_at": _NOW,
                "updated_at": _NOW,
            }
            for username, title, category in _CHANNELS
        ],
    )


def downgrade() -> None:
    usernames = [ch[0] for ch in _CHANNELS]
    op.execute(
        sa.text(
            "DELETE FROM telegram_channels WHERE username = ANY(:usernames)"
        ).bindparams(usernames=usernames)
    )
