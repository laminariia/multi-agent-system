"""Email suppression list for CAN-SPAM / GDPR compliance.

Checks suppression status BEFORE every email send.
Supports permanent suppression (hard bounce, unsubscribe, GDPR erasure)
and temporary suppression with expiration (soft bounce).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select

from src.core.models import EmailSuppressionEntry

logger = structlog.get_logger(__name__)


class SuppressionList:
    """Email suppression list backed by PostgreSQL.

    Args:
        session: Async SQLAlchemy session.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    @staticmethod
    def _normalize(email: str) -> str:
        return email.strip().lower()

    async def suppress(
        self,
        email: str,
        *,
        reason: str,
        source: str,
        expires_at: datetime | None = None,
    ) -> None:
        """Add email to suppression list. Idempotent — skips if already present."""
        normalized = self._normalize(email)

        result = await self._session.execute(
            select(EmailSuppressionEntry).where(EmailSuppressionEntry.email == normalized)
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            logger.debug("suppression.already_exists", email=normalized, reason=existing.reason)
            return

        entry = EmailSuppressionEntry(
            email=normalized,
            reason=reason,
            source=source,
            expires_at=expires_at,
        )
        self._session.add(entry)
        await self._session.flush()

        logger.info("suppression.added", email=normalized, reason=reason, source=source)

    async def is_suppressed(self, email: str) -> bool:
        """Check if an email is suppressed (considering expiration)."""
        normalized = self._normalize(email)

        result = await self._session.execute(
            select(EmailSuppressionEntry).where(EmailSuppressionEntry.email == normalized)
        )
        entry = result.scalar_one_or_none()

        if entry is None:
            return False

        # Check expiration for temporary suppressions
        if entry.expires_at is not None and entry.expires_at < datetime.now(UTC):
            return False

        return True

    async def unsuppress(self, email: str) -> None:
        """Remove email from suppression list (admin override only)."""
        normalized = self._normalize(email)

        result = await self._session.execute(
            select(EmailSuppressionEntry).where(EmailSuppressionEntry.email == normalized)
        )
        entry = result.scalar_one_or_none()

        if entry is None:
            logger.debug("suppression.unsuppress_noop", email=normalized)
            return

        self._session.delete(entry)
        await self._session.flush()
        logger.info("suppression.removed", email=normalized)

    async def bulk_check(self, emails: list[str]) -> set[str]:
        """Return the subset of emails that are suppressed."""
        if not emails:
            return set()

        normalized = [self._normalize(e) for e in emails]

        result = await self._session.execute(
            select(EmailSuppressionEntry.email).where(
                EmailSuppressionEntry.email.in_(normalized),
                # Exclude expired temporary suppressions
                (
                    (EmailSuppressionEntry.expires_at.is_(None))
                    | (EmailSuppressionEntry.expires_at >= datetime.now(UTC))
                ),
            )
        )
        return set(result.scalars().all())

    async def cleanup_expired(self) -> int:
        """Delete expired temporary suppressions. Returns count removed."""
        result = await self._session.execute(
            delete(EmailSuppressionEntry).where(
                EmailSuppressionEntry.expires_at.is_not(None),
                EmailSuppressionEntry.expires_at < datetime.now(UTC),
            )
        )
        count = result.rowcount
        if count > 0:
            await self._session.flush()
            logger.info("suppression.cleanup", removed=count)
        return count
