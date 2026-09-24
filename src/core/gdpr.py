"""GDPR compliance manager for the Multi-Agent Service.

Implements four pillars of GDPR/152-FZ compliance:

1. **Erasure Requests** (Right to be Forgotten, Article 17) — cascade delete
   across all tables containing personal data, 30-day SLA.
2. **Data Subject Access Requests** (DSAR, Article 15) — export all personal
   data held for a given email address as structured JSON.
3. **Consent Tracking** — record, revoke, and check consent for B2C
   communications (marketing emails, data processing, profiling).
4. **Breach Notification** (Article 33/34) — 72-hour deadline tracking
   for supervisory authority notification, severity classification.

Jurisdiction support: EU (GDPR), RU (152-FZ), GLOBAL (default).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import Boolean, DateTime, Integer, String, Text, and_, delete, select
from sqlalchemy.dialects.postgresql import ARRAY as PGARRAY
from sqlalchemy.dialects.postgresql import JSONB as PGJSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.core.models import (
    Base,
    CampaignLead,
    EmailSuppressionEntry,
    Lead,
)


def _utcnow() -> datetime:
    """Return the current UTC datetime (local helper to avoid importing private name)."""
    return datetime.now(tz=UTC)


logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ERASURE_DEADLINE_DAYS = 30
_DSAR_DEADLINE_DAYS = 30
_BREACH_NOTIFICATION_HOURS = 72

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _normalize_email(email: str) -> str:
    """Strip whitespace and lowercase an email address."""
    return email.strip().lower()


def _validate_email(email: str) -> str:
    """Validate and normalize an email address. Raises ValueError on failure."""
    normalized = _normalize_email(email)
    if not normalized or not _EMAIL_RE.match(normalized):
        msg = f"Invalid email address: {email!r}"
        raise ValueError(msg)
    return normalized


# ===========================================================================
# Enums
# ===========================================================================


class ErasureStatus(StrEnum):
    """Status of an erasure (Right to be Forgotten) request."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class DSARStatus(StrEnum):
    """Status of a Data Subject Access Request."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ConsentType(StrEnum):
    """Types of consent that can be tracked."""

    MARKETING_EMAIL = "marketing_email"
    DATA_PROCESSING = "data_processing"
    THIRD_PARTY_SHARING = "third_party_sharing"
    PROFILING = "profiling"


class BreachSeverity(StrEnum):
    """Severity classification for data breaches."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BreachStatus(StrEnum):
    """Status progression for breach notification lifecycle."""

    DETECTED = "detected"
    ASSESSED = "assessed"
    AUTHORITY_NOTIFIED = "authority_notified"
    SUBJECTS_NOTIFIED = "subjects_notified"
    RESOLVED = "resolved"


# ===========================================================================
# Data classes (in-memory representations returned by the public API)
# ===========================================================================


@dataclass
class ErasureRequest:
    """Represents a Right to be Forgotten request."""

    subject_email: str
    reason: str
    jurisdiction: str = "GLOBAL"
    status: str = ErasureStatus.PENDING
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requested_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    deadline: datetime = field(init=False)
    completed_at: datetime | None = None
    requested_by: str | None = None

    def __post_init__(self) -> None:
        self.subject_email = _normalize_email(self.subject_email)
        self.deadline = self.requested_at + timedelta(days=_ERASURE_DEADLINE_DAYS)


@dataclass
class SubjectAccessRequest:
    """Represents a Data Subject Access Request (DSAR)."""

    subject_email: str
    reason: str
    status: str = DSARStatus.PENDING
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requested_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    deadline: datetime = field(init=False)
    completed_at: datetime | None = None
    requested_by: str | None = None

    def __post_init__(self) -> None:
        self.subject_email = _normalize_email(self.subject_email)
        self.deadline = self.requested_at + timedelta(days=_DSAR_DEADLINE_DAYS)


@dataclass
class ConsentRecord:
    """Tracks a single consent decision (grant or revocation)."""

    subject_email: str
    consent_type: str
    granted: bool
    source: str
    consent_text: str | None = None
    ip_address: str | None = None
    consent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    granted_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        self.subject_email = _normalize_email(self.subject_email)


@dataclass
class DataBreachRecord:
    """Tracks a data breach through its notification lifecycle."""

    title: str
    description: str
    severity: str
    affected_subjects_count: int
    data_categories: list[str] = field(default_factory=list)
    discovered_by: str | None = None
    status: str = BreachStatus.DETECTED
    breach_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    detected_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    notification_deadline: datetime = field(init=False)
    authority_notified_at: datetime | None = None
    subjects_notified_at: datetime | None = None
    resolved_at: datetime | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.notification_deadline = self.detected_at + timedelta(
            hours=_BREACH_NOTIFICATION_HOURS,
        )


# ===========================================================================
# ORM models for GDPR tables
# ===========================================================================
# Follow project convention from src/core/models.py. Defined in this module
# to avoid modifying the existing models.py file.


class GDPRErasureRecord(Base):
    """Tracks Right to be Forgotten (erasure) requests."""

    __tablename__ = "gdpr_erasure_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(10), default="GLOBAL", server_default="GLOBAL")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class GDPRAccessRecord(Base):
    """Tracks Data Subject Access Requests (DSAR)."""

    __tablename__ = "gdpr_access_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class GDPRConsentRecord(Base):
    """Tracks consent decisions (grants and revocations)."""

    __tablename__ = "gdpr_consent_records"

    consent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    consent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GDPRBreachRecord(Base):
    """Tracks data breaches through their notification lifecycle."""

    __tablename__ = "gdpr_breach_records"

    breach_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="detected", server_default="detected")
    affected_subjects_count: Mapped[int] = mapped_column(Integer, default=0)
    data_categories: Mapped[list[str] | None] = mapped_column(PGARRAY(Text), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notification_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authority_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subjects_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[list[dict[str, Any]] | None] = mapped_column(PGJSONB, nullable=True)


class GDPRAuditLog(Base):
    """Audit log for GDPR operations (immutable trail)."""

    __tablename__ = "gdpr_audit_log"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(PGJSONB, nullable=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ===========================================================================
# GDPRManager
# ===========================================================================


class GDPRManager:
    """Orchestrates all GDPR compliance operations.

    Args:
        session: An ``AsyncSession`` for database operations.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -----------------------------------------------------------------------
    # 1. Erasure Requests (Right to be Forgotten)
    # -----------------------------------------------------------------------

    async def submit_erasure_request(
        self,
        subject_email: str,
        reason: str,
        *,
        jurisdiction: str = "GLOBAL",
        requested_by: str | None = None,
    ) -> ErasureRequest:
        """Create a new erasure request for a data subject.

        Args:
            subject_email: Email of the data subject requesting erasure.
            reason: Free-text reason (e.g. "GDPR Article 17 request").
            jurisdiction: Legal jurisdiction -- "EU", "RU", or "GLOBAL".
            requested_by: Email/ID of the person submitting the request.

        Returns:
            The created :class:`ErasureRequest`.

        Raises:
            ValueError: On invalid input or duplicate pending request.
        """
        email = _validate_email(subject_email)
        if not reason or not reason.strip():
            msg = "reason must not be empty"
            raise ValueError(msg)

        # Check for existing pending request
        result = await self._session.execute(
            select(GDPRErasureRecord).where(
                and_(
                    GDPRErasureRecord.subject_email == email,
                    GDPRErasureRecord.status == ErasureStatus.PENDING,
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            msg = f"A pending erasure request already exists for {email}"
            raise ValueError(msg)

        request = ErasureRequest(
            subject_email=email,
            reason=reason,
            jurisdiction=jurisdiction,
            requested_by=requested_by,
        )

        record = GDPRErasureRecord(
            request_id=request.request_id,
            subject_email=request.subject_email,
            reason=request.reason,
            jurisdiction=request.jurisdiction,
            status=request.status,
            requested_at=request.requested_at,
            deadline=request.deadline,
            requested_by=requested_by,
        )
        self._session.add(record)
        await self._session.flush()

        logger.info(
            "gdpr.erasure_request_submitted",
            request_id=request.request_id,
            subject_email=email,
            jurisdiction=jurisdiction,
        )

        return request

    async def process_erasure(self, request_id: str) -> dict[str, Any]:
        """Execute cascade deletion for an erasure request.

        Deletes personal data from: leads, campaign_leads,
        email_suppression_list, and clears PII from agent_logs.

        Args:
            request_id: The erasure request ID to process.

        Returns:
            Summary dict with status and per-table deletion counts.

        Raises:
            ValueError: If request not found or already completed.
        """
        result = await self._session.execute(
            select(GDPRErasureRecord).where(
                GDPRErasureRecord.request_id == request_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            msg = f"Erasure request {request_id!r} not found"
            raise ValueError(msg)

        if record.status == ErasureStatus.COMPLETED:
            msg = f"Erasure request {request_id!r} already completed"
            raise ValueError(msg)

        record.status = ErasureStatus.IN_PROGRESS
        email = record.subject_email
        tables_affected: dict[str, int] = {}

        try:
            # 1. Delete leads by email
            r = await self._session.execute(delete(Lead).where(Lead.email == email))
            tables_affected["leads"] = r.rowcount or 0

            # 2. Delete orphaned campaign_leads (lead FK cascade handles most,
            #    but clean up any remaining references)
            r = await self._session.execute(delete(CampaignLead).where(CampaignLead.lead_id.not_in(select(Lead.id))))
            tables_affected["campaign_leads"] = r.rowcount or 0

            # 3. Delete email suppression entries
            r = await self._session.execute(
                delete(EmailSuppressionEntry).where(
                    EmailSuppressionEntry.email == email,
                )
            )
            tables_affected["email_suppression"] = r.rowcount or 0

            # 4. Clear PII from agent_logs (anonymize, don't delete -- audit trail)
            from sqlalchemy import text as sa_text  # noqa: PLC0415

            r = await self._session.execute(
                sa_text(
                    "UPDATE agent_logs SET details = details - 'email' - 'phone' - 'name' "
                    "WHERE details->>'email' = :email"
                ),
                {"email": email},
            )
            tables_affected["agent_logs_anonymized"] = r.rowcount or 0

            # 5. Delete consent records for this subject
            r = await self._session.execute(
                delete(GDPRConsentRecord).where(
                    GDPRConsentRecord.subject_email == email,
                )
            )
            tables_affected["gdpr_consent_records"] = r.rowcount or 0

            # 6. Delete access requests for this subject
            r = await self._session.execute(
                delete(GDPRAccessRecord).where(
                    GDPRAccessRecord.subject_email == email,
                )
            )
            tables_affected["gdpr_access_requests"] = r.rowcount or 0

            # 7. Anonymize breach records where subject email appears
            r = await self._session.execute(
                sa_text(
                    "UPDATE gdpr_breach_records "
                    "SET description = regexp_replace(description, :email_pattern, '[REDACTED]', 'gi'), "
                    "    notes = regexp_replace(notes::text, :email_pattern, '[REDACTED]', 'gi')::jsonb "
                    "WHERE description ILIKE :email_like "
                    "   OR notes::text ILIKE :email_like"
                ),
                {
                    "email_pattern": email.replace(".", "\\."),
                    "email_like": "%" + email + "%",
                },
            )
            tables_affected["gdpr_breach_records_anonymized"] = r.rowcount or 0

            # Mark completed
            record.status = ErasureStatus.COMPLETED
            record.completed_at = datetime.now(tz=UTC)
            await self._session.flush()

            total = sum(tables_affected.values())
            logger.info(
                "gdpr.erasure_completed",
                request_id=request_id,
                subject_email=email,
                tables_affected=tables_affected,
                total_records=total,
            )

            # Create audit log entry
            audit = GDPRAuditLog(
                action="erasure_completed",
                subject_email=email,
                details={
                    "request_id": request_id,
                    "tables_affected": tables_affected,
                    "total_records": total,
                },
            )
            self._session.add(audit)
            await self._session.flush()

            return {
                "request_id": request_id,
                "status": ErasureStatus.COMPLETED,
                "tables_affected": total,
                "details": tables_affected,
            }

        except Exception:
            record.status = ErasureStatus.FAILED
            await self._session.flush()
            logger.error(
                "gdpr.erasure_failed",
                request_id=request_id,
                subject_email=email,
                exc_info=True,
            )
            raise

    async def get_erasure_status(self, request_id: str) -> dict[str, Any]:
        """Get the current status of an erasure request.

        Raises:
            ValueError: If request not found.
        """
        result = await self._session.execute(
            select(GDPRErasureRecord).where(
                GDPRErasureRecord.request_id == request_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            msg = f"Erasure request {request_id!r} not found"
            raise ValueError(msg)

        return {
            "request_id": record.request_id,
            "subject_email": record.subject_email,
            "status": record.status,
            "jurisdiction": record.jurisdiction,
            "reason": record.reason,
            "requested_at": record.requested_at,
            "deadline": record.deadline,
            "completed_at": record.completed_at,
        }

    async def list_erasure_requests(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all erasure requests, optionally filtered by status."""
        stmt = select(GDPRErasureRecord)
        if status is not None:
            stmt = stmt.where(GDPRErasureRecord.status == status)
        stmt = stmt.order_by(GDPRErasureRecord.requested_at.desc())

        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "request_id": r.request_id,
                "subject_email": r.subject_email,
                "status": r.status,
                "jurisdiction": r.jurisdiction,
                "reason": r.reason,
                "requested_at": r.requested_at,
                "deadline": r.deadline,
                "completed_at": r.completed_at,
            }
            for r in records
        ]

    # -----------------------------------------------------------------------
    # 2. Data Subject Access Requests (DSAR)
    # -----------------------------------------------------------------------

    async def submit_access_request(
        self,
        subject_email: str,
        reason: str,
        *,
        requested_by: str | None = None,
    ) -> SubjectAccessRequest:
        """Create a new Data Subject Access Request.

        Args:
            subject_email: Email of the data subject.
            reason: Free-text reason for the request.
            requested_by: Who submitted this request.

        Returns:
            The created :class:`SubjectAccessRequest`.

        Raises:
            ValueError: On invalid input or duplicate pending request.
        """
        email = _validate_email(subject_email)
        if not reason or not reason.strip():
            msg = "reason must not be empty"
            raise ValueError(msg)

        # Check for existing pending request
        result = await self._session.execute(
            select(GDPRAccessRecord).where(
                and_(
                    GDPRAccessRecord.subject_email == email,
                    GDPRAccessRecord.status == DSARStatus.PENDING,
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            msg = f"A pending access request already exists for {email}"
            raise ValueError(msg)

        request = SubjectAccessRequest(
            subject_email=email,
            reason=reason,
            requested_by=requested_by,
        )

        record = GDPRAccessRecord(
            request_id=request.request_id,
            subject_email=request.subject_email,
            reason=request.reason,
            status=request.status,
            requested_at=request.requested_at,
            deadline=request.deadline,
            requested_by=requested_by,
        )
        self._session.add(record)
        await self._session.flush()

        logger.info(
            "gdpr.access_request_submitted",
            request_id=request.request_id,
            subject_email=email,
        )

        return request

    async def process_access_request(self, request_id: str) -> dict[str, Any]:
        """Collect all personal data for a subject and return as JSON.

        Gathers data from: leads, campaign_leads, email_suppression_list.

        Args:
            request_id: The DSAR request ID.

        Returns:
            Export dict containing all personal data found.

        Raises:
            ValueError: If request not found or already completed.
        """
        result = await self._session.execute(
            select(GDPRAccessRecord).where(
                GDPRAccessRecord.request_id == request_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            msg = f"Access request {request_id!r} not found"
            raise ValueError(msg)

        if record.status == DSARStatus.COMPLETED:
            msg = f"Access request {request_id!r} already completed"
            raise ValueError(msg)

        record.status = DSARStatus.PROCESSING
        email = record.subject_email

        try:
            # 1. Leads
            r = await self._session.execute(select(Lead).where(Lead.email == email))
            leads = r.scalars().all()
            leads_data = [
                {
                    "id": str(lead.id),
                    "name": lead.name,
                    "email": lead.email,
                    "phone": lead.phone,
                    "category": lead.category,
                    "city": lead.city,
                    "country": lead.country,
                    "website": lead.website,
                    "status": lead.status,
                    "discovered_at": (lead.discovered_at.isoformat() if lead.discovered_at else None),
                }
                for lead in leads
            ]

            # 2. Campaign leads (via lead IDs)
            lead_ids = [lead.id for lead in leads]
            if lead_ids:
                r = await self._session.execute(select(CampaignLead).where(CampaignLead.lead_id.in_(lead_ids)))
            else:
                r = await self._session.execute(select(CampaignLead).where(CampaignLead.lead_id.is_(None)))
            campaign_leads = r.scalars().all()
            campaign_leads_data = [
                {
                    "campaign_id": str(cl.campaign_id),
                    "lead_id": str(cl.lead_id),
                    "status": cl.status,
                    "channel_type": cl.channel_type,
                    "sent_at": (cl.sent_at.isoformat() if cl.sent_at else None),
                }
                for cl in campaign_leads
            ]

            # 3. Suppression entries
            r = await self._session.execute(
                select(EmailSuppressionEntry).where(
                    EmailSuppressionEntry.email == email,
                )
            )
            suppression_entries = r.scalars().all()
            suppression_data = [
                {
                    "email": entry.email,
                    "reason": entry.reason,
                    "suppressed_at": (entry.suppressed_at.isoformat() if entry.suppressed_at else None),
                }
                for entry in suppression_entries
            ]

            # Mark completed
            record.status = DSARStatus.COMPLETED
            record.completed_at = datetime.now(tz=UTC)
            await self._session.flush()

            export = {
                "request_id": request_id,
                "subject_email": email,
                "exported_at": datetime.now(tz=UTC).isoformat(),
                "data": {
                    "leads": leads_data,
                    "campaign_leads": campaign_leads_data,
                    "suppression_entries": suppression_data,
                },
            }

            # Create audit log
            audit = GDPRAuditLog(
                action="access_request_completed",
                subject_email=email,
                details={
                    "request_id": request_id,
                    "records_exported": {
                        "leads": len(leads_data),
                        "campaign_leads": len(campaign_leads_data),
                        "suppression_entries": len(suppression_data),
                    },
                },
            )
            self._session.add(audit)
            await self._session.flush()

            logger.info(
                "gdpr.access_request_completed",
                request_id=request_id,
                subject_email=email,
                leads=len(leads_data),
                campaign_leads=len(campaign_leads_data),
                suppression=len(suppression_data),
            )

            return export

        except Exception:
            record.status = DSARStatus.FAILED
            await self._session.flush()
            logger.error(
                "gdpr.access_request_failed",
                request_id=request_id,
                subject_email=email,
                exc_info=True,
            )
            raise

    async def get_access_request_status(self, request_id: str) -> dict[str, Any]:
        """Get the current status of a DSAR.

        Raises:
            ValueError: If request not found.
        """
        result = await self._session.execute(
            select(GDPRAccessRecord).where(
                GDPRAccessRecord.request_id == request_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            msg = f"Access request {request_id!r} not found"
            raise ValueError(msg)

        return {
            "request_id": record.request_id,
            "subject_email": record.subject_email,
            "status": record.status,
            "reason": record.reason,
            "requested_at": record.requested_at,
            "deadline": record.deadline,
            "completed_at": record.completed_at,
        }

    async def list_access_requests(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all data subject access requests, optionally filtered by status.

        Args:
            status: Optional status filter (see :class:`DSARStatus`).

        Returns:
            List of DSAR record dicts.
        """
        stmt = select(GDPRAccessRecord)
        if status is not None:
            stmt = stmt.where(GDPRAccessRecord.status == status)
        stmt = stmt.order_by(GDPRAccessRecord.requested_at.desc())

        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "request_id": r.request_id,
                "subject_email": r.subject_email,
                "status": r.status,
                "reason": r.reason,
                "requested_at": r.requested_at,
                "deadline": r.deadline,
                "completed_at": r.completed_at,
            }
            for r in records
        ]

    # -----------------------------------------------------------------------
    # 3. Consent Tracking
    # -----------------------------------------------------------------------

    async def record_consent(
        self,
        subject_email: str,
        consent_type: str,
        granted: bool,
        source: str,
        *,
        consent_text: str | None = None,
        ip_address: str | None = None,
    ) -> ConsentRecord:
        """Record a consent decision (grant or denial).

        Args:
            subject_email: Email of the data subject.
            consent_type: Type of consent (see :class:`ConsentType`).
            granted: Whether consent was granted.
            source: Where consent was obtained (web_form, api, etc.).
            consent_text: The actual consent text shown to the user.
            ip_address: IP address of the user at consent time.

        Returns:
            The created :class:`ConsentRecord`.

        Raises:
            ValueError: On invalid input.
        """
        email = _validate_email(subject_email)
        if not source or not source.strip():
            msg = "source must not be empty"
            raise ValueError(msg)

        record = ConsentRecord(
            subject_email=email,
            consent_type=consent_type,
            granted=granted,
            source=source,
            consent_text=consent_text,
            ip_address=ip_address,
        )

        db_record = GDPRConsentRecord(
            consent_id=record.consent_id,
            subject_email=record.subject_email,
            consent_type=record.consent_type,
            granted=record.granted,
            source=record.source,
            consent_text=record.consent_text,
            ip_address=record.ip_address,
            granted_at=record.granted_at,
        )
        self._session.add(db_record)
        await self._session.flush()

        logger.info(
            "gdpr.consent_recorded",
            consent_id=record.consent_id,
            subject_email=email,
            consent_type=consent_type,
            granted=granted,
            source=source,
        )

        return record

    async def revoke_consent(
        self,
        subject_email: str,
        consent_type: str,
        source: str,
    ) -> bool:
        """Revoke a previously granted consent.

        Args:
            subject_email: Email of the data subject.
            consent_type: Which consent type to revoke.
            source: Where the revocation request came from.

        Returns:
            True if consent was found and revoked, False if no active
            consent was found.
        """
        email = _normalize_email(subject_email)

        result = await self._session.execute(
            select(GDPRConsentRecord).where(
                and_(
                    GDPRConsentRecord.subject_email == email,
                    GDPRConsentRecord.consent_type == consent_type,
                    GDPRConsentRecord.granted.is_(True),
                    GDPRConsentRecord.revoked_at.is_(None),
                )
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            logger.debug(
                "gdpr.consent_revoke_noop",
                subject_email=email,
                consent_type=consent_type,
            )
            return False

        now = datetime.now(tz=UTC)
        record.revoked_at = now

        # Also record the revocation as a new consent event
        revocation = GDPRConsentRecord(
            consent_id=str(uuid.uuid4()),
            subject_email=email,
            consent_type=consent_type,
            granted=False,
            source=source,
            granted_at=now,
            revoked_at=None,
        )
        self._session.add(revocation)
        await self._session.flush()

        logger.info(
            "gdpr.consent_revoked",
            subject_email=email,
            consent_type=consent_type,
            source=source,
        )

        return True

    async def check_consent(
        self,
        subject_email: str,
        consent_type: str,
    ) -> bool:
        """Check if a subject has active (non-revoked) consent.

        Returns:
            True if active consent exists, False otherwise.
        """
        email = _normalize_email(subject_email)

        result = await self._session.execute(
            select(GDPRConsentRecord).where(
                and_(
                    GDPRConsentRecord.subject_email == email,
                    GDPRConsentRecord.consent_type == consent_type,
                    GDPRConsentRecord.granted.is_(True),
                    GDPRConsentRecord.revoked_at.is_(None),
                )
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            return False

        # Double-check: if revoked_at is set, consent is no longer active
        if record.revoked_at is not None:
            return False

        return True

    async def list_consents(self, subject_email: str) -> list[dict[str, Any]]:
        """List all consent records for a subject.

        Returns:
            List of consent record dicts.
        """
        email = _normalize_email(subject_email)

        result = await self._session.execute(
            select(GDPRConsentRecord)
            .where(GDPRConsentRecord.subject_email == email)
            .order_by(GDPRConsentRecord.granted_at.desc())
        )
        records = result.scalars().all()

        return [
            {
                "consent_id": r.consent_id,
                "subject_email": r.subject_email,
                "consent_type": r.consent_type,
                "granted": r.granted,
                "granted_at": r.granted_at,
                "revoked_at": r.revoked_at,
                "source": r.source,
                "consent_text": r.consent_text,
            }
            for r in records
        ]

    # -----------------------------------------------------------------------
    # 4. Breach Notification
    # -----------------------------------------------------------------------

    async def report_breach(
        self,
        title: str,
        description: str,
        severity: str,
        affected_subjects_count: int,
        *,
        data_categories: list[str] | None = None,
        discovered_by: str | None = None,
    ) -> DataBreachRecord:
        """Report a new data breach.

        Creates a breach record with a 72-hour notification deadline.

        Args:
            title: Short title describing the breach.
            description: Detailed description of the breach.
            severity: Severity level (see :class:`BreachSeverity`).
            affected_subjects_count: Number of affected data subjects.
            data_categories: Types of personal data affected.
            discovered_by: Who discovered the breach.

        Returns:
            The created :class:`DataBreachRecord`.

        Raises:
            ValueError: On invalid input.
        """
        if not title or not title.strip():
            msg = "title must not be empty"
            raise ValueError(msg)
        if affected_subjects_count < 0:
            msg = "affected_subjects_count must be non-negative"
            raise ValueError(msg)

        breach = DataBreachRecord(
            title=title,
            description=description,
            severity=severity,
            affected_subjects_count=affected_subjects_count,
            data_categories=data_categories or [],
            discovered_by=discovered_by,
        )

        db_record = GDPRBreachRecord(
            breach_id=breach.breach_id,
            title=breach.title,
            description=breach.description,
            severity=breach.severity,
            status=breach.status,
            affected_subjects_count=breach.affected_subjects_count,
            data_categories=breach.data_categories,
            detected_at=breach.detected_at,
            notification_deadline=breach.notification_deadline,
            discovered_by=breach.discovered_by,
        )
        self._session.add(db_record)
        await self._session.flush()

        logger.warning(
            "gdpr.breach_reported",
            breach_id=breach.breach_id,
            title=title,
            severity=severity,
            affected_subjects_count=affected_subjects_count,
            notification_deadline=breach.notification_deadline.isoformat(),
        )

        return breach

    async def update_breach_status(
        self,
        breach_id: str,
        new_status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Update the status of a breach and track timeline.

        Args:
            breach_id: The breach record ID.
            new_status: New status to set.
            note: Optional note about this status transition.

        Returns:
            Updated breach status dict.

        Raises:
            ValueError: If breach not found or new_status is invalid.
        """
        # Validate new_status against BreachStatus enum
        valid_statuses = {s.value for s in BreachStatus}
        if new_status not in valid_statuses:
            msg = f"Invalid breach status {new_status!r}. Must be one of: {', '.join(sorted(valid_statuses))}"
            raise ValueError(msg)

        result = await self._session.execute(
            select(GDPRBreachRecord).where(
                GDPRBreachRecord.breach_id == breach_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            msg = f"Breach {breach_id!r} not found"
            raise ValueError(msg)

        now = datetime.now(tz=UTC)
        old_status = record.status
        record.status = new_status

        # Track timeline timestamps
        if new_status == BreachStatus.AUTHORITY_NOTIFIED:
            record.authority_notified_at = now
        elif new_status == BreachStatus.SUBJECTS_NOTIFIED:
            record.subjects_notified_at = now
        elif new_status == BreachStatus.RESOLVED:
            record.resolved_at = now

        # Append note to history
        if note:
            if not hasattr(record, "notes") or record.notes is None:
                record.notes = []
            record.notes = [
                *record.notes,
                {"timestamp": now.isoformat(), "note": note},
            ]

        await self._session.flush()

        logger.info(
            "gdpr.breach_status_updated",
            breach_id=breach_id,
            old_status=old_status,
            new_status=new_status,
            note=note,
        )

        return {
            "breach_id": breach_id,
            "status": new_status,
            "updated_at": now.isoformat(),
        }

    async def get_breach_details(self, breach_id: str) -> dict[str, Any]:
        """Get full details of a breach record.

        Raises:
            ValueError: If breach not found.
        """
        result = await self._session.execute(
            select(GDPRBreachRecord).where(
                GDPRBreachRecord.breach_id == breach_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            msg = f"Breach {breach_id!r} not found"
            raise ValueError(msg)

        return {
            "breach_id": record.breach_id,
            "title": record.title,
            "description": record.description,
            "severity": record.severity,
            "status": record.status,
            "affected_subjects_count": record.affected_subjects_count,
            "data_categories": record.data_categories,
            "detected_at": record.detected_at,
            "notification_deadline": record.notification_deadline,
            "authority_notified_at": record.authority_notified_at,
            "subjects_notified_at": record.subjects_notified_at,
            "resolved_at": record.resolved_at,
            "discovered_by": record.discovered_by,
            "notes": record.notes,
        }

    async def list_breaches(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all breach records, optionally filtered by status."""
        stmt = select(GDPRBreachRecord)
        if status is not None:
            stmt = stmt.where(GDPRBreachRecord.status == status)
        stmt = stmt.order_by(GDPRBreachRecord.detected_at.desc())

        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "breach_id": r.breach_id,
                "title": r.title,
                "severity": r.severity,
                "status": r.status,
                "affected_subjects_count": r.affected_subjects_count,
                "detected_at": r.detected_at,
                "notification_deadline": r.notification_deadline,
            }
            for r in records
        ]
