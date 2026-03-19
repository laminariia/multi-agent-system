"""GDPR compliance API routes.

Provides endpoints for managing GDPR obligations:
- Erasure requests (Right to be Forgotten)
- Data Subject Access Requests (DSAR)
- Consent tracking (record/revoke/check)
- Breach notification management

Mounted at ``/api/v1/gdpr``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from litestar import Controller, Request, get, post
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.core.exceptions import MASException
from src.core.gdpr import (
    GDPRManager,
)
from src.core.models import User

logger = structlog.get_logger(__name__)


# ===========================================================================
# Request / Response schemas (Pydantic, project convention: Schema suffix)
# ===========================================================================

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402


class _GDPRBaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# -- Erasure --


class ErasureRequestSchema(_GDPRBaseSchema):
    """Request body for submitting an erasure request."""

    subject_email: str = Field(..., description="Email of the data subject")
    reason: str = Field(..., min_length=1, max_length=2000, description="Reason for erasure")
    jurisdiction: str = Field(default="GLOBAL", description="Legal jurisdiction: EU, RU, GLOBAL")


class ErasureResponseSchema(_GDPRBaseSchema):
    """Response for an erasure request."""

    request_id: str
    subject_email: str
    status: str
    jurisdiction: str
    requested_at: datetime
    deadline: datetime
    completed_at: datetime | None = None


class ErasureProcessResponseSchema(_GDPRBaseSchema):
    """Response after processing (executing) an erasure request."""

    request_id: str
    status: str
    tables_affected: int
    details: dict[str, int]


class ErasureListResponseSchema(_GDPRBaseSchema):
    """Response for listing erasure requests."""

    items: list[ErasureResponseSchema]
    total: int


# -- DSAR --


class DSARRequestSchema(_GDPRBaseSchema):
    """Request body for submitting a data subject access request."""

    subject_email: str = Field(..., description="Email of the data subject")
    reason: str = Field(..., min_length=1, max_length=2000, description="Reason for access request")


class DSARResponseSchema(_GDPRBaseSchema):
    """Response for a DSAR status check."""

    request_id: str
    subject_email: str
    status: str
    requested_at: datetime
    deadline: datetime
    completed_at: datetime | None = None


class DSARExportResponseSchema(_GDPRBaseSchema):
    """Response containing the exported personal data."""

    request_id: str
    subject_email: str
    exported_at: str
    data: dict[str, Any]


class DSARListResponseSchema(_GDPRBaseSchema):
    """Response for listing DSARs."""

    items: list[DSARResponseSchema]
    total: int


# -- Consent --


class ConsentRequestSchema(_GDPRBaseSchema):
    """Request body for recording consent."""

    subject_email: str = Field(..., description="Email of the data subject")
    consent_type: str = Field(..., description="Type: marketing_email, data_processing, third_party_sharing, profiling")
    granted: bool = Field(..., description="Whether consent is granted")
    source: str = Field(..., min_length=1, description="Source of consent: web_form, api, etc.")
    consent_text: str | None = Field(default=None, description="The actual consent text shown")
    ip_address: str | None = Field(default=None, description="IP address at consent time")


class ConsentResponseSchema(_GDPRBaseSchema):
    """Response for a consent record."""

    consent_id: str
    subject_email: str
    consent_type: str
    granted: bool
    granted_at: datetime
    revoked_at: datetime | None = None
    source: str
    consent_text: str | None = None


class ConsentRevokeRequestSchema(_GDPRBaseSchema):
    """Request body for revoking consent."""

    subject_email: str = Field(..., description="Email of the data subject")
    consent_type: str = Field(..., description="Type of consent to revoke")
    source: str = Field(..., min_length=1, description="Source of the revocation request")


class ConsentCheckResponseSchema(_GDPRBaseSchema):
    """Response for checking consent status."""

    subject_email: str
    consent_type: str
    has_active_consent: bool


class ConsentListResponseSchema(_GDPRBaseSchema):
    """Response for listing consent records."""

    items: list[ConsentResponseSchema]
    total: int


# -- Breach --


class BreachReportRequestSchema(_GDPRBaseSchema):
    """Request body for reporting a data breach."""

    title: str = Field(..., min_length=1, max_length=500, description="Breach title")
    description: str = Field(..., min_length=1, description="Detailed description")
    severity: str = Field(..., description="Severity: low, medium, high, critical")
    affected_subjects_count: int = Field(..., ge=0, description="Number of affected subjects")
    data_categories: list[str] = Field(default_factory=list, description="Types of affected data")
    discovered_by: str | None = Field(default=None, description="Who discovered the breach")


class BreachResponseSchema(_GDPRBaseSchema):
    """Response for a breach record."""

    breach_id: str
    title: str
    severity: str
    status: str
    affected_subjects_count: int
    detected_at: datetime
    notification_deadline: datetime


class BreachDetailResponseSchema(_GDPRBaseSchema):
    """Full breach details response."""

    breach_id: str
    title: str
    description: str
    severity: str
    status: str
    affected_subjects_count: int
    data_categories: list[str] | None = None
    detected_at: datetime
    notification_deadline: datetime
    authority_notified_at: datetime | None = None
    subjects_notified_at: datetime | None = None
    resolved_at: datetime | None = None
    discovered_by: str | None = None
    notes: list[dict[str, Any]] | None = None


class BreachStatusUpdateRequestSchema(_GDPRBaseSchema):
    """Request body for updating breach status."""

    status: str = Field(..., description="New status: assessed, authority_notified, subjects_notified, resolved")
    note: str | None = Field(default=None, description="Note about this status change")


class BreachStatusUpdateResponseSchema(_GDPRBaseSchema):
    """Response after updating breach status."""

    breach_id: str
    status: str
    updated_at: str


class BreachListResponseSchema(_GDPRBaseSchema):
    """Response for listing breaches."""

    items: list[BreachResponseSchema]
    total: int


# ===========================================================================
# Controller
# ===========================================================================


class GDPRController(Controller):
    """GDPR compliance management endpoints."""

    path = "/api/v1/gdpr"
    tags = ["gdpr"]

    # -------------------------------------------------------------------
    # Erasure Requests
    # -------------------------------------------------------------------

    @post(
        "/erasure",
        summary="Submit an erasure request (Right to be Forgotten)",
        guards=[require_role("owner", "co_owner")],
        status_code=201,
    )
    async def submit_erasure(
        self,
        data: ErasureRequestSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> ErasureResponseSchema:
        """Create a new erasure request for a data subject.

        30-day SLA per GDPR Article 17 / 152-FZ requirements.
        """
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.submit_erasure_request(
                subject_email=data.subject_email,
                reason=data.reason,
                jurisdiction=data.jurisdiction,
                requested_by=request.user.email,
            )
        except ValueError as exc:
            raise MASException(
                str(exc),
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            ) from exc

        return ErasureResponseSchema(
            request_id=result.request_id,
            subject_email=result.subject_email,
            status=result.status,
            jurisdiction=result.jurisdiction,
            requested_at=result.requested_at,
            deadline=result.deadline,
        )

    @post(
        "/erasure/{request_id:str}/process",
        summary="Process (execute) an erasure request",
        guards=[require_role("owner")],
        status_code=200,
    )
    async def process_erasure(
        self,
        request_id: str,
        db_session: AsyncSession,
    ) -> ErasureProcessResponseSchema:
        """Execute cascade deletion for the given erasure request.

        Deletes personal data from leads, campaign_leads,
        email_suppression, and anonymizes agent_logs.
        """
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.process_erasure(request_id)
        except ValueError as exc:
            raise MASException(
                str(exc),
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            ) from exc

        return ErasureProcessResponseSchema(
            request_id=result["request_id"],
            status=result["status"],
            tables_affected=result["tables_affected"],
            details=result["details"],
        )

    @get(
        "/erasure/{request_id:str}",
        summary="Get erasure request status",
        guards=[require_role("owner", "co_owner")],
    )
    async def get_erasure_status(
        self,
        request_id: str,
        db_session: AsyncSession,
    ) -> ErasureResponseSchema:
        """Return the current status of an erasure request."""
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.get_erasure_status(request_id)
        except ValueError as exc:
            raise NotFoundException(detail=str(exc)) from exc

        return ErasureResponseSchema(**result)

    @get(
        "/erasure",
        summary="List erasure requests",
        guards=[require_role("owner", "co_owner")],
    )
    async def list_erasure_requests(
        self,
        db_session: AsyncSession,
        status: str | None = Parameter(default=None, description="Filter by status"),
    ) -> ErasureListResponseSchema:
        """List all erasure requests, optionally filtered by status."""
        mgr = GDPRManager(db_session)
        items = await mgr.list_erasure_requests(status=status)

        return ErasureListResponseSchema(
            items=[ErasureResponseSchema(**item) for item in items],
            total=len(items),
        )

    # -------------------------------------------------------------------
    # Data Subject Access Requests (DSAR)
    # -------------------------------------------------------------------

    @post(
        "/access",
        summary="Submit a data subject access request (DSAR)",
        guards=[require_role("owner", "co_owner")],
        status_code=201,
    )
    async def submit_dsar(
        self,
        data: DSARRequestSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> DSARResponseSchema:
        """Create a new DSAR. 30-day SLA per GDPR Article 15."""
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.submit_access_request(
                subject_email=data.subject_email,
                reason=data.reason,
                requested_by=request.user.email,
            )
        except ValueError as exc:
            raise MASException(
                str(exc),
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            ) from exc

        return DSARResponseSchema(
            request_id=result.request_id,
            subject_email=result.subject_email,
            status=result.status,
            requested_at=result.requested_at,
            deadline=result.deadline,
        )

    @post(
        "/access/{request_id:str}/process",
        summary="Process a DSAR (export personal data)",
        guards=[require_role("owner")],
        status_code=200,
    )
    async def process_dsar(
        self,
        request_id: str,
        db_session: AsyncSession,
    ) -> DSARExportResponseSchema:
        """Collect and export all personal data for a data subject."""
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.process_access_request(request_id)
        except ValueError as exc:
            raise MASException(
                str(exc),
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            ) from exc

        return DSARExportResponseSchema(
            request_id=result["request_id"],
            subject_email=result["subject_email"],
            exported_at=result["exported_at"],
            data=result["data"],
        )

    @get(
        "/access/{request_id:str}",
        summary="Get DSAR status",
        guards=[require_role("owner", "co_owner")],
    )
    async def get_dsar_status(
        self,
        request_id: str,
        db_session: AsyncSession,
    ) -> DSARResponseSchema:
        """Return the current status of a data access request."""
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.get_access_request_status(request_id)
        except ValueError as exc:
            raise NotFoundException(detail=str(exc)) from exc

        return DSARResponseSchema(**result)

    @get(
        "/access",
        summary="List data subject access requests",
        guards=[require_role("owner", "co_owner")],
    )
    async def list_dsars(
        self,
        db_session: AsyncSession,
        status: str | None = Parameter(default=None, description="Filter by status"),
    ) -> DSARListResponseSchema:
        """List all DSARs."""
        mgr = GDPRManager(db_session)
        records = await mgr.list_access_requests(status=status)

        items = [DSARResponseSchema(**r) for r in records]

        return DSARListResponseSchema(items=items, total=len(items))

    # -------------------------------------------------------------------
    # Consent Tracking
    # -------------------------------------------------------------------

    @post(
        "/consent",
        summary="Record a consent decision",
        guards=[require_role("owner", "co_owner")],
        status_code=201,
    )
    async def record_consent(
        self,
        data: ConsentRequestSchema,
        db_session: AsyncSession,
    ) -> ConsentResponseSchema:
        """Record a consent grant or denial for a data subject.

        Required for B2C marketing emails per GDPR/152-FZ.
        B2B legitimate interest does not require prior consent.
        """
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.record_consent(
                subject_email=data.subject_email,
                consent_type=data.consent_type,
                granted=data.granted,
                source=data.source,
                consent_text=data.consent_text,
                ip_address=data.ip_address,
            )
        except ValueError as exc:
            raise MASException(
                str(exc),
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            ) from exc

        return ConsentResponseSchema(
            consent_id=result.consent_id,
            subject_email=result.subject_email,
            consent_type=result.consent_type,
            granted=result.granted,
            granted_at=result.granted_at,
            source=result.source,
            consent_text=result.consent_text,
        )

    @post(
        "/consent/revoke",
        summary="Revoke a previously granted consent",
        guards=[require_role("owner", "co_owner")],
        status_code=200,
    )
    async def revoke_consent(
        self,
        data: ConsentRevokeRequestSchema,
        db_session: AsyncSession,
    ) -> ConsentCheckResponseSchema:
        """Revoke consent. Immediate processing per GDPR best practice."""
        mgr = GDPRManager(db_session)
        revoked = await mgr.revoke_consent(
            subject_email=data.subject_email,
            consent_type=data.consent_type,
            source=data.source,
        )

        return ConsentCheckResponseSchema(
            subject_email=data.subject_email,
            consent_type=data.consent_type,
            has_active_consent=not revoked,
        )

    @get(
        "/consent/check",
        summary="Check if a subject has active consent",
        guards=[require_role("owner", "co_owner")],
    )
    async def check_consent(
        self,
        db_session: AsyncSession,
        subject_email: str = Parameter(description="Email of the data subject"),
        consent_type: str = Parameter(description="Type of consent to check"),
    ) -> ConsentCheckResponseSchema:
        """Check whether active (non-revoked) consent exists."""
        mgr = GDPRManager(db_session)
        has_consent = await mgr.check_consent(
            subject_email=subject_email,
            consent_type=consent_type,
        )

        return ConsentCheckResponseSchema(
            subject_email=subject_email,
            consent_type=consent_type,
            has_active_consent=has_consent,
        )

    @get(
        "/consent/{subject_email:str}",
        summary="List all consent records for a subject",
        guards=[require_role("owner", "co_owner")],
    )
    async def list_consents(
        self,
        subject_email: str,
        db_session: AsyncSession,
    ) -> ConsentListResponseSchema:
        """Return the full consent history for a data subject."""
        mgr = GDPRManager(db_session)
        items = await mgr.list_consents(subject_email)

        return ConsentListResponseSchema(
            items=[ConsentResponseSchema(**item) for item in items],
            total=len(items),
        )

    # -------------------------------------------------------------------
    # Breach Notification
    # -------------------------------------------------------------------

    @post(
        "/breach",
        summary="Report a data breach",
        guards=[require_role("owner")],
        status_code=201,
    )
    async def report_breach(
        self,
        data: BreachReportRequestSchema,
        db_session: AsyncSession,
    ) -> BreachResponseSchema:
        """Report a new data breach. Sets a 72-hour notification deadline.

        Per GDPR Article 33, the supervisory authority must be notified
        within 72 hours of becoming aware of a personal data breach.
        """
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.report_breach(
                title=data.title,
                description=data.description,
                severity=data.severity,
                affected_subjects_count=data.affected_subjects_count,
                data_categories=data.data_categories,
                discovered_by=data.discovered_by,
            )
        except ValueError as exc:
            raise MASException(
                str(exc),
                details={"error_code": "VALIDATION_ERROR", "status_code": 422},
            ) from exc

        return BreachResponseSchema(
            breach_id=result.breach_id,
            title=result.title,
            severity=result.severity,
            status=result.status,
            affected_subjects_count=result.affected_subjects_count,
            detected_at=result.detected_at,
            notification_deadline=result.notification_deadline,
        )

    @post(
        "/breach/{breach_id:str}/status",
        summary="Update breach status",
        guards=[require_role("owner")],
        status_code=200,
    )
    async def update_breach_status(
        self,
        breach_id: str,
        data: BreachStatusUpdateRequestSchema,
        db_session: AsyncSession,
    ) -> BreachStatusUpdateResponseSchema:
        """Update the status of a breach and track the notification timeline."""
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.update_breach_status(
                breach_id=breach_id,
                new_status=data.status,
                note=data.note,
            )
        except ValueError as exc:
            raise NotFoundException(detail=str(exc)) from exc

        return BreachStatusUpdateResponseSchema(
            breach_id=result["breach_id"],
            status=result["status"],
            updated_at=result["updated_at"],
        )

    @get(
        "/breach/{breach_id:str}",
        summary="Get breach details",
        guards=[require_role("owner", "co_owner")],
    )
    async def get_breach_details(
        self,
        breach_id: str,
        db_session: AsyncSession,
    ) -> BreachDetailResponseSchema:
        """Return full details for a breach record."""
        mgr = GDPRManager(db_session)
        try:
            result = await mgr.get_breach_details(breach_id)
        except ValueError as exc:
            raise NotFoundException(detail=str(exc)) from exc

        return BreachDetailResponseSchema(**result)

    @get(
        "/breach",
        summary="List data breaches",
        guards=[require_role("owner", "co_owner")],
    )
    async def list_breaches(
        self,
        db_session: AsyncSession,
        status: str | None = Parameter(default=None, description="Filter by status"),
    ) -> BreachListResponseSchema:
        """List all breach records, optionally filtered by status."""
        mgr = GDPRManager(db_session)
        items = await mgr.list_breaches(status=status)

        return BreachListResponseSchema(
            items=[BreachResponseSchema(**item) for item in items],
            total=len(items),
        )
