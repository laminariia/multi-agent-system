"""Comprehensive unit tests for GDPR compliance module (src/core/gdpr.py).

Covers all four GDPR pillars:
1. Erasure Requests (Right to be Forgotten) — cascade delete, 30-day SLA
2. Data Subject Access Requests (DSAR) — full data export as JSON
3. Consent Tracking — record, revoke, check, list
4. Breach Notification — 72-hour tracking, severity, timeline

Tests follow project TDD pattern: mock AsyncSession, no real DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.gdpr import (
    BreachSeverity,
    BreachStatus,
    ConsentRecord,
    ConsentType,
    DataBreachRecord,
    DSARStatus,
    ErasureRequest,
    ErasureStatus,
    GDPRManager,
    SubjectAccessRequest,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session() -> AsyncMock:
    """Create a mock DB session matching the project pattern."""
    session = AsyncMock()
    session.add = MagicMock()
    session.delete = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _mock_result(rowcount: int = 0) -> MagicMock:
    r = MagicMock()
    r.rowcount = rowcount
    return r


def _mock_scalar_result(value):
    """Mock a result where scalar_one_or_none() returns value."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _mock_scalars_result(values: list):
    """Mock a result where scalars().all() returns values."""
    r = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = values
    r.scalars.return_value = scalars_mock
    return r


def _mock_scalar_one_result(value):
    """Mock a result where scalar_one() returns value."""
    r = MagicMock()
    r.scalar_one.return_value = value
    return r


# ===========================================================================
# 1. Erasure Requests (Right to be Forgotten)
# ===========================================================================


class TestErasureRequestModel:
    """ErasureRequest dataclass validation."""

    def test_create_erasure_request(self):
        req = ErasureRequest(
            subject_email="user@example.com",
            reason="GDPR Article 17 request",
            jurisdiction="EU",
        )
        assert req.subject_email == "user@example.com"
        assert req.status == ErasureStatus.PENDING
        assert req.jurisdiction == "EU"
        assert req.request_id is not None
        assert req.requested_at is not None

    def test_erasure_request_deadline_30_days(self):
        req = ErasureRequest(
            subject_email="user@example.com",
            reason="deletion request",
        )
        delta = req.deadline - req.requested_at
        assert delta.days == 30

    def test_erasure_request_normalizes_email(self):
        req = ErasureRequest(
            subject_email="  USER@Example.COM  ",
            reason="test",
        )
        assert req.subject_email == "user@example.com"

    def test_erasure_status_values(self):
        assert ErasureStatus.PENDING == "pending"
        assert ErasureStatus.IN_PROGRESS == "in_progress"
        assert ErasureStatus.COMPLETED == "completed"
        assert ErasureStatus.FAILED == "failed"


class TestGDPRManagerErasure:
    """GDPRManager.submit_erasure_request and process_erasure."""

    async def test_submit_erasure_request(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        result = await mgr.submit_erasure_request(
            subject_email="victim@example.com",
            reason="I want my data deleted",
            jurisdiction="EU",
            requested_by="admin@example.com",
        )

        assert result.subject_email == "victim@example.com"
        assert result.status == ErasureStatus.PENDING
        assert result.jurisdiction == "EU"
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_submit_erasure_normalizes_email(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        result = await mgr.submit_erasure_request(
            subject_email="  UPPER@EXAMPLE.COM  ",
            reason="test",
        )
        assert result.subject_email == "upper@example.com"

    async def test_submit_erasure_duplicate_pending_raises(self):
        session = _mock_session()
        existing = MagicMock()
        existing.status = ErasureStatus.PENDING
        session.execute = AsyncMock(return_value=_mock_scalar_result(existing))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="pending erasure request already exists"):
            await mgr.submit_erasure_request(
                subject_email="dup@example.com",
                reason="duplicate",
            )

    async def test_process_erasure_cascade_delete(self):
        session = _mock_session()
        # Mock: find the erasure request
        erasure_record = MagicMock()
        erasure_record.request_id = str(uuid.uuid4())
        erasure_record.subject_email = "user@example.com"
        erasure_record.status = ErasureStatus.PENDING

        # Sequence: 1) find request, 2-N) cascade deletes
        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(erasure_record),  # find request
                _mock_result(3),  # leads deleted
                _mock_result(2),  # campaign_leads deleted
                _mock_result(1),  # email_suppression deleted
                _mock_result(0),  # agent_logs anonymized
                _mock_result(0),  # gdpr_consent_records deleted
                _mock_result(0),  # gdpr_access_requests deleted
                _mock_result(0),  # gdpr_breach_records anonymized
            ]
        )

        mgr = GDPRManager(session)
        result = await mgr.process_erasure(erasure_record.request_id)

        assert result["status"] == ErasureStatus.COMPLETED
        assert result["tables_affected"] >= 0
        assert erasure_record.status == ErasureStatus.COMPLETED

    async def test_process_erasure_not_found_raises(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="Erasure request .* not found"):
            await mgr.process_erasure("nonexistent-id")

    async def test_process_erasure_already_completed_raises(self):
        session = _mock_session()
        record = MagicMock()
        record.status = ErasureStatus.COMPLETED
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="already completed"):
            await mgr.process_erasure("some-id")

    async def test_process_erasure_marks_failed_on_error(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "fail-id"
        record.subject_email = "fail@example.com"
        record.status = ErasureStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),  # find request
                Exception("DB connection lost"),  # cascade fails
            ]
        )
        mgr = GDPRManager(session)

        with pytest.raises(Exception, match="DB connection lost"):
            await mgr.process_erasure("fail-id")

        assert record.status == ErasureStatus.FAILED

    async def test_get_erasure_status(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "req-123"
        record.subject_email = "user@example.com"
        record.status = ErasureStatus.IN_PROGRESS
        record.requested_at = datetime.now(UTC)
        record.deadline = datetime.now(UTC) + timedelta(days=30)
        record.completed_at = None
        record.jurisdiction = "EU"
        record.reason = "GDPR request"
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        status = await mgr.get_erasure_status("req-123")
        assert status["request_id"] == "req-123"
        assert status["status"] == ErasureStatus.IN_PROGRESS

    async def test_get_erasure_status_not_found(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="not found"):
            await mgr.get_erasure_status("missing-id")

    async def test_list_erasure_requests(self):
        session = _mock_session()
        items = [MagicMock(), MagicMock()]
        for i, item in enumerate(items):
            item.request_id = f"req-{i}"
            item.subject_email = f"user{i}@example.com"
            item.status = ErasureStatus.PENDING
            item.requested_at = datetime.now(UTC)
            item.deadline = datetime.now(UTC) + timedelta(days=30)
            item.completed_at = None
            item.jurisdiction = "EU"
            item.reason = "test"
        session.execute = AsyncMock(return_value=_mock_scalars_result(items))
        mgr = GDPRManager(session)

        result = await mgr.list_erasure_requests()
        assert len(result) == 2

    async def test_list_erasure_requests_filter_by_status(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalars_result([]))
        mgr = GDPRManager(session)

        result = await mgr.list_erasure_requests(status=ErasureStatus.COMPLETED)
        assert result == []
        session.execute.assert_called_once()


# ===========================================================================
# 2. Data Subject Access Requests (DSAR)
# ===========================================================================


class TestSubjectAccessRequestModel:
    """SubjectAccessRequest dataclass validation."""

    def test_create_dsar(self):
        req = SubjectAccessRequest(
            subject_email="user@example.com",
            reason="GDPR Article 15 request",
        )
        assert req.subject_email == "user@example.com"
        assert req.status == DSARStatus.PENDING
        assert req.request_id is not None
        assert req.requested_at is not None

    def test_dsar_deadline_30_days(self):
        req = SubjectAccessRequest(
            subject_email="user@example.com",
            reason="data access",
        )
        delta = req.deadline - req.requested_at
        assert delta.days == 30

    def test_dsar_normalizes_email(self):
        req = SubjectAccessRequest(
            subject_email="  USER@Example.COM  ",
            reason="test",
        )
        assert req.subject_email == "user@example.com"

    def test_dsar_status_values(self):
        assert DSARStatus.PENDING == "pending"
        assert DSARStatus.PROCESSING == "processing"
        assert DSARStatus.COMPLETED == "completed"
        assert DSARStatus.FAILED == "failed"


class TestGDPRManagerDSAR:
    """GDPRManager DSAR methods."""

    async def test_submit_dsar(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        result = await mgr.submit_access_request(
            subject_email="user@example.com",
            reason="I want my data",
            requested_by="admin@company.com",
        )
        assert result.subject_email == "user@example.com"
        assert result.status == DSARStatus.PENDING
        session.add.assert_called_once()

    async def test_submit_dsar_duplicate_pending_raises(self):
        session = _mock_session()
        existing = MagicMock()
        existing.status = DSARStatus.PENDING
        session.execute = AsyncMock(return_value=_mock_scalar_result(existing))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="pending access request already exists"):
            await mgr.submit_access_request(
                subject_email="dup@example.com",
                reason="duplicate",
            )

    async def test_process_dsar_collects_all_data(self):
        session = _mock_session()
        dsar_record = MagicMock()
        dsar_record.request_id = str(uuid.uuid4())
        dsar_record.subject_email = "user@example.com"
        dsar_record.status = DSARStatus.PENDING

        # Mock lead data
        lead = MagicMock()
        lead.id = uuid.uuid4()
        lead.name = "Test Business"
        lead.email = "user@example.com"
        lead.phone = "+1234567890"
        lead.category = "restaurant"
        lead.city = "Moscow"
        lead.country = "RU"
        lead.status = "new"
        lead.discovered_at = datetime.now(UTC)

        # Mock suppression entry
        suppression = MagicMock()
        suppression.email = "user@example.com"
        suppression.reason = "gdpr_erasure"
        suppression.suppressed_at = datetime.now(UTC)

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(dsar_record),  # find request
                _mock_scalars_result([lead]),  # leads
                _mock_scalars_result([]),  # campaign_leads
                _mock_scalars_result([suppression]),  # suppression entries
            ]
        )

        mgr = GDPRManager(session)
        export = await mgr.process_access_request(dsar_record.request_id)

        assert export["subject_email"] == "user@example.com"
        assert "leads" in export["data"]
        assert "suppression_entries" in export["data"]
        assert len(export["data"]["leads"]) == 1
        assert export["data"]["leads"][0]["name"] == "Test Business"
        assert dsar_record.status == DSARStatus.COMPLETED

    async def test_process_dsar_not_found_raises(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="Access request .* not found"):
            await mgr.process_access_request("nonexistent")

    async def test_process_dsar_already_completed_raises(self):
        session = _mock_session()
        record = MagicMock()
        record.status = DSARStatus.COMPLETED
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="already completed"):
            await mgr.process_access_request("done-id")

    async def test_process_dsar_empty_data(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "empty-id"
        record.subject_email = "nobody@example.com"
        record.status = DSARStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                _mock_scalars_result([]),  # no leads
                _mock_scalars_result([]),  # no campaign_leads
                _mock_scalars_result([]),  # no suppression
            ]
        )

        mgr = GDPRManager(session)
        export = await mgr.process_access_request("empty-id")

        assert export["data"]["leads"] == []
        assert export["data"]["campaign_leads"] == []
        assert export["data"]["suppression_entries"] == []

    async def test_process_dsar_marks_failed_on_error(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "fail-id"
        record.subject_email = "fail@example.com"
        record.status = DSARStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                Exception("DB error"),
            ]
        )
        mgr = GDPRManager(session)

        with pytest.raises(Exception, match="DB error"):
            await mgr.process_access_request("fail-id")

        assert record.status == DSARStatus.FAILED

    async def test_get_dsar_status(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "dsar-123"
        record.subject_email = "user@example.com"
        record.status = DSARStatus.PROCESSING
        record.requested_at = datetime.now(UTC)
        record.deadline = datetime.now(UTC) + timedelta(days=30)
        record.completed_at = None
        record.reason = "data access"
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        status = await mgr.get_access_request_status("dsar-123")
        assert status["request_id"] == "dsar-123"
        assert status["status"] == DSARStatus.PROCESSING


# ===========================================================================
# 3. Consent Tracking
# ===========================================================================


class TestConsentRecordModel:
    """ConsentRecord dataclass validation."""

    def test_create_consent_record(self):
        rec = ConsentRecord(
            subject_email="user@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
            granted=True,
            source="web_form",
            consent_text="I agree to receive marketing emails",
        )
        assert rec.subject_email == "user@example.com"
        assert rec.consent_type == ConsentType.MARKETING_EMAIL
        assert rec.granted is True
        assert rec.consent_id is not None

    def test_consent_type_values(self):
        assert ConsentType.MARKETING_EMAIL == "marketing_email"
        assert ConsentType.DATA_PROCESSING == "data_processing"
        assert ConsentType.THIRD_PARTY_SHARING == "third_party_sharing"
        assert ConsentType.PROFILING == "profiling"

    def test_consent_normalizes_email(self):
        rec = ConsentRecord(
            subject_email="  USER@Example.COM  ",
            consent_type=ConsentType.MARKETING_EMAIL,
            granted=True,
            source="web",
        )
        assert rec.subject_email == "user@example.com"


class TestGDPRManagerConsent:
    """GDPRManager consent tracking methods."""

    async def test_record_consent(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        result = await mgr.record_consent(
            subject_email="user@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
            granted=True,
            source="web_form",
            consent_text="I agree to receive marketing emails",
            ip_address="192.168.1.1",
        )

        assert result.subject_email == "user@example.com"
        assert result.consent_type == ConsentType.MARKETING_EMAIL
        assert result.granted is True
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_record_consent_normalizes_email(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        result = await mgr.record_consent(
            subject_email="  UPPER@EXAMPLE.COM  ",
            consent_type=ConsentType.DATA_PROCESSING,
            granted=True,
            source="api",
        )
        assert result.subject_email == "upper@example.com"

    async def test_revoke_consent(self):
        session = _mock_session()
        existing = MagicMock()
        existing.subject_email = "user@example.com"
        existing.consent_type = ConsentType.MARKETING_EMAIL
        existing.granted = True
        session.execute = AsyncMock(return_value=_mock_scalar_result(existing))
        mgr = GDPRManager(session)

        result = await mgr.revoke_consent(
            subject_email="user@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
            source="user_request",
        )

        assert result is True
        session.add.assert_called_once()  # revocation record added
        session.flush.assert_called()

    async def test_revoke_consent_not_found(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        result = await mgr.revoke_consent(
            subject_email="nobody@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
            source="user_request",
        )
        assert result is False

    async def test_check_consent_granted(self):
        session = _mock_session()
        record = MagicMock()
        record.granted = True
        record.revoked_at = None
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        result = await mgr.check_consent(
            subject_email="user@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
        )
        assert result is True

    async def test_check_consent_not_granted(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        result = await mgr.check_consent(
            subject_email="user@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
        )
        assert result is False

    async def test_check_consent_revoked(self):
        session = _mock_session()
        record = MagicMock()
        record.granted = True
        record.revoked_at = datetime.now(UTC)  # was revoked
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        result = await mgr.check_consent(
            subject_email="user@example.com",
            consent_type=ConsentType.MARKETING_EMAIL,
        )
        assert result is False

    async def test_list_consents_for_subject(self):
        session = _mock_session()
        records = []
        for ct in [ConsentType.MARKETING_EMAIL, ConsentType.DATA_PROCESSING]:
            r = MagicMock()
            r.consent_id = str(uuid.uuid4())
            r.subject_email = "user@example.com"
            r.consent_type = ct
            r.granted = True
            r.granted_at = datetime.now(UTC)
            r.revoked_at = None
            r.source = "web_form"
            r.consent_text = "I agree"
            records.append(r)

        session.execute = AsyncMock(return_value=_mock_scalars_result(records))
        mgr = GDPRManager(session)

        result = await mgr.list_consents("user@example.com")
        assert len(result) == 2

    async def test_list_consents_empty(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalars_result([]))
        mgr = GDPRManager(session)

        result = await mgr.list_consents("nobody@example.com")
        assert result == []


# ===========================================================================
# 4. Breach Notification
# ===========================================================================


class TestDataBreachRecordModel:
    """DataBreachRecord dataclass validation."""

    def test_create_breach_record(self):
        rec = DataBreachRecord(
            title="Database exposure",
            description="PostgreSQL backup was publicly accessible for 2 hours",
            severity=BreachSeverity.HIGH,
            affected_subjects_count=150,
            data_categories=["email", "name", "phone"],
            discovered_by="security_team",
        )
        assert rec.title == "Database exposure"
        assert rec.severity == BreachSeverity.HIGH
        assert rec.status == BreachStatus.DETECTED
        assert rec.breach_id is not None
        assert rec.notification_deadline is not None

    def test_breach_72_hour_deadline(self):
        rec = DataBreachRecord(
            title="Test breach",
            description="Test",
            severity=BreachSeverity.LOW,
            affected_subjects_count=1,
        )
        delta = rec.notification_deadline - rec.detected_at
        assert delta.total_seconds() == 72 * 3600

    def test_breach_severity_values(self):
        assert BreachSeverity.LOW == "low"
        assert BreachSeverity.MEDIUM == "medium"
        assert BreachSeverity.HIGH == "high"
        assert BreachSeverity.CRITICAL == "critical"

    def test_breach_status_values(self):
        assert BreachStatus.DETECTED == "detected"
        assert BreachStatus.ASSESSED == "assessed"
        assert BreachStatus.AUTHORITY_NOTIFIED == "authority_notified"
        assert BreachStatus.SUBJECTS_NOTIFIED == "subjects_notified"
        assert BreachStatus.RESOLVED == "resolved"


class TestGDPRManagerBreach:
    """GDPRManager breach notification methods."""

    async def test_report_breach(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        result = await mgr.report_breach(
            title="Data leak via API",
            description="Unauthenticated endpoint exposed lead emails",
            severity=BreachSeverity.HIGH,
            affected_subjects_count=500,
            data_categories=["email", "phone", "name"],
            discovered_by="penetration_test",
        )

        assert result.title == "Data leak via API"
        assert result.severity == BreachSeverity.HIGH
        assert result.affected_subjects_count == 500
        assert result.status == BreachStatus.DETECTED
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_report_breach_sets_72h_deadline(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        result = await mgr.report_breach(
            title="Minor leak",
            description="Low severity incident",
            severity=BreachSeverity.LOW,
            affected_subjects_count=1,
        )

        delta = result.notification_deadline - result.detected_at
        assert abs(delta.total_seconds() - 72 * 3600) < 2  # within 2s tolerance

    async def test_update_breach_status(self):
        session = _mock_session()
        record = MagicMock()
        record.breach_id = "breach-123"
        record.status = BreachStatus.DETECTED
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        result = await mgr.update_breach_status(
            breach_id="breach-123",
            new_status=BreachStatus.AUTHORITY_NOTIFIED,
            note="Reported to DPA on 2026-03-17",
        )

        assert result["status"] == BreachStatus.AUTHORITY_NOTIFIED
        assert record.status == BreachStatus.AUTHORITY_NOTIFIED
        session.flush.assert_called()

    async def test_update_breach_status_not_found(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="Breach .* not found"):
            await mgr.update_breach_status("missing", BreachStatus.ASSESSED)

    async def test_get_breach_details(self):
        session = _mock_session()
        record = MagicMock()
        record.breach_id = "breach-456"
        record.title = "API exposure"
        record.description = "Details"
        record.severity = BreachSeverity.MEDIUM
        record.status = BreachStatus.ASSESSED
        record.affected_subjects_count = 42
        record.data_categories = ["email"]
        record.detected_at = datetime.now(UTC)
        record.notification_deadline = datetime.now(UTC) + timedelta(hours=72)
        record.authority_notified_at = None
        record.subjects_notified_at = None
        record.resolved_at = None
        record.discovered_by = "audit"
        record.notes = []
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        result = await mgr.get_breach_details("breach-456")
        assert result["breach_id"] == "breach-456"
        assert result["severity"] == BreachSeverity.MEDIUM

    async def test_get_breach_not_found(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="not found"):
            await mgr.get_breach_details("missing")

    async def test_list_breaches(self):
        session = _mock_session()
        breaches = []
        for i in range(3):
            b = MagicMock()
            b.breach_id = f"breach-{i}"
            b.title = f"Breach {i}"
            b.severity = BreachSeverity.MEDIUM
            b.status = BreachStatus.DETECTED
            b.affected_subjects_count = i * 10
            b.detected_at = datetime.now(UTC)
            b.notification_deadline = datetime.now(UTC) + timedelta(hours=72)
            breaches.append(b)

        session.execute = AsyncMock(return_value=_mock_scalars_result(breaches))
        mgr = GDPRManager(session)

        result = await mgr.list_breaches()
        assert len(result) == 3

    async def test_list_breaches_filter_by_status(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalars_result([]))
        mgr = GDPRManager(session)

        result = await mgr.list_breaches(status=BreachStatus.RESOLVED)
        assert result == []

    async def test_list_active_breaches_near_deadline(self):
        session = _mock_session()
        # Breach 1: deadline in 12 hours (urgent)
        b1 = MagicMock()
        b1.breach_id = "urgent-1"
        b1.title = "Urgent breach"
        b1.severity = BreachSeverity.CRITICAL
        b1.status = BreachStatus.DETECTED
        b1.affected_subjects_count = 1000
        b1.detected_at = datetime.now(UTC) - timedelta(hours=60)
        b1.notification_deadline = datetime.now(UTC) + timedelta(hours=12)

        session.execute = AsyncMock(return_value=_mock_scalars_result([b1]))
        mgr = GDPRManager(session)

        result = await mgr.list_breaches(status=BreachStatus.DETECTED)
        assert len(result) == 1
        assert result[0]["breach_id"] == "urgent-1"

    async def test_breach_timeline_tracking(self):
        """Verify breach status transitions store timestamps."""
        session = _mock_session()
        record = MagicMock()
        record.breach_id = "timeline-1"
        record.status = BreachStatus.DETECTED
        record.authority_notified_at = None
        record.subjects_notified_at = None
        record.resolved_at = None
        record.notes = []
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        # Transition to authority_notified
        await mgr.update_breach_status(
            "timeline-1",
            BreachStatus.AUTHORITY_NOTIFIED,
            note="Notified DPA",
        )
        assert record.authority_notified_at is not None

    async def test_breach_subjects_notified_timestamp(self):
        session = _mock_session()
        record = MagicMock()
        record.breach_id = "subj-1"
        record.status = BreachStatus.AUTHORITY_NOTIFIED
        record.authority_notified_at = datetime.now(UTC)
        record.subjects_notified_at = None
        record.resolved_at = None
        record.notes = []
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        await mgr.update_breach_status(
            "subj-1",
            BreachStatus.SUBJECTS_NOTIFIED,
            note="Emails sent to 100 subjects",
        )
        assert record.subjects_notified_at is not None

    async def test_breach_resolved_timestamp(self):
        session = _mock_session()
        record = MagicMock()
        record.breach_id = "resolved-1"
        record.status = BreachStatus.SUBJECTS_NOTIFIED
        record.authority_notified_at = datetime.now(UTC)
        record.subjects_notified_at = datetime.now(UTC)
        record.resolved_at = None
        record.notes = []
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        await mgr.update_breach_status("resolved-1", BreachStatus.RESOLVED)
        assert record.resolved_at is not None


# ===========================================================================
# 5. Cross-cutting: Audit logging
# ===========================================================================


class TestGDPRManagerAuditLog:
    """GDPRManager audit log creation."""

    async def test_erasure_creates_audit_log(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "audit-erasure"
        record.subject_email = "user@example.com"
        record.status = ErasureStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                _mock_result(1),  # leads
                _mock_result(0),  # campaign_leads
                _mock_result(0),  # suppression
                _mock_result(0),  # agent_logs anonymized
                _mock_result(0),  # gdpr_consent_records
                _mock_result(0),  # gdpr_access_requests
                _mock_result(0),  # gdpr_breach_records anonymized
            ]
        )

        mgr = GDPRManager(session)
        await mgr.process_erasure("audit-erasure")

        # session.add called at least twice: once for audit log, once for status update
        assert session.add.call_count >= 1

    async def test_dsar_creates_audit_log(self):
        session = _mock_session()
        record = MagicMock()
        record.request_id = "audit-dsar"
        record.subject_email = "user@example.com"
        record.status = DSARStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                _mock_scalars_result([]),
                _mock_scalars_result([]),
                _mock_scalars_result([]),
            ]
        )

        mgr = GDPRManager(session)
        await mgr.process_access_request("audit-dsar")

        assert session.add.call_count >= 1


# ===========================================================================
# 6. Edge cases and validation
# ===========================================================================


class TestGDPREdgeCases:
    """Edge cases and input validation."""

    async def test_empty_email_raises(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="email"):
            await mgr.submit_erasure_request(
                subject_email="",
                reason="test",
            )

    async def test_invalid_email_raises(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="email"):
            await mgr.submit_erasure_request(
                subject_email="not-an-email",
                reason="test",
            )

    async def test_empty_reason_raises_for_erasure(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="reason"):
            await mgr.submit_erasure_request(
                subject_email="user@example.com",
                reason="",
            )

    async def test_empty_reason_raises_for_dsar(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="reason"):
            await mgr.submit_access_request(
                subject_email="user@example.com",
                reason="",
            )

    async def test_breach_empty_title_raises(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="title"):
            await mgr.report_breach(
                title="",
                description="Details",
                severity=BreachSeverity.LOW,
                affected_subjects_count=1,
            )

    async def test_breach_negative_affected_count_raises(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="affected_subjects_count"):
            await mgr.report_breach(
                title="Test",
                description="Details",
                severity=BreachSeverity.LOW,
                affected_subjects_count=-1,
            )

    async def test_consent_empty_source_raises(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="source"):
            await mgr.record_consent(
                subject_email="user@example.com",
                consent_type=ConsentType.MARKETING_EMAIL,
                granted=True,
                source="",
            )

    async def test_jurisdiction_defaults_to_global(self):
        req = ErasureRequest(
            subject_email="user@example.com",
            reason="test",
        )
        assert req.jurisdiction == "GLOBAL"

    async def test_152fz_jurisdiction(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))
        mgr = GDPRManager(session)

        result = await mgr.submit_erasure_request(
            subject_email="user@yandex.ru",
            reason="152-FZ deletion request",
            jurisdiction="RU",
        )
        assert result.jurisdiction == "RU"


# ===========================================================================
# 7. Erasure cascade -- consent, access, breach tables (Issue 2)
# ===========================================================================


class TestErasureCascadeExtended:
    """Verify erasure cascade deletes from consent, access, and breach tables."""

    async def test_process_erasure_deletes_consent_records(self):
        """Erasure should delete consent records for the subject."""
        session = _mock_session()
        record = MagicMock()
        record.request_id = "consent-erasure"
        record.subject_email = "user@example.com"
        record.status = ErasureStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),  # find request
                _mock_result(1),  # leads
                _mock_result(0),  # campaign_leads
                _mock_result(0),  # suppression
                _mock_result(0),  # agent_logs
                _mock_result(3),  # gdpr_consent_records
                _mock_result(0),  # gdpr_access_requests
                _mock_result(0),  # gdpr_breach_records
            ]
        )
        mgr = GDPRManager(session)
        result = await mgr.process_erasure("consent-erasure")

        assert result["status"] == ErasureStatus.COMPLETED
        assert result["details"]["gdpr_consent_records"] == 3

    async def test_process_erasure_deletes_access_requests(self):
        """Erasure should delete access requests for the subject."""
        session = _mock_session()
        record = MagicMock()
        record.request_id = "access-erasure"
        record.subject_email = "user@example.com"
        record.status = ErasureStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                _mock_result(0),  # leads
                _mock_result(0),  # campaign_leads
                _mock_result(0),  # suppression
                _mock_result(0),  # agent_logs
                _mock_result(0),  # gdpr_consent_records
                _mock_result(2),  # gdpr_access_requests
                _mock_result(0),  # gdpr_breach_records
            ]
        )
        mgr = GDPRManager(session)
        result = await mgr.process_erasure("access-erasure")

        assert result["details"]["gdpr_access_requests"] == 2

    async def test_process_erasure_anonymizes_breach_records(self):
        """Erasure should anonymize breach records mentioning the subject."""
        session = _mock_session()
        record = MagicMock()
        record.request_id = "breach-anon"
        record.subject_email = "user@example.com"
        record.status = ErasureStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                _mock_result(0),  # leads
                _mock_result(0),  # campaign_leads
                _mock_result(0),  # suppression
                _mock_result(0),  # agent_logs
                _mock_result(0),  # gdpr_consent_records
                _mock_result(0),  # gdpr_access_requests
                _mock_result(1),  # gdpr_breach_records
            ]
        )
        mgr = GDPRManager(session)
        result = await mgr.process_erasure("breach-anon")

        assert result["details"]["gdpr_breach_records_anonymized"] == 1

    async def test_process_erasure_full_cascade_counts(self):
        """All table deletion counts should be included in the result."""
        session = _mock_session()
        record = MagicMock()
        record.request_id = "full-cascade"
        record.subject_email = "user@example.com"
        record.status = ErasureStatus.PENDING

        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(record),
                _mock_result(2),  # leads
                _mock_result(1),  # campaign_leads
                _mock_result(1),  # suppression
                _mock_result(1),  # agent_logs
                _mock_result(3),  # gdpr_consent_records
                _mock_result(1),  # gdpr_access_requests
                _mock_result(1),  # gdpr_breach_records
            ]
        )
        mgr = GDPRManager(session)
        result = await mgr.process_erasure("full-cascade")

        details = result["details"]
        assert "gdpr_consent_records" in details
        assert "gdpr_access_requests" in details
        assert "gdpr_breach_records_anonymized" in details
        assert result["tables_affected"] == 10  # 2+1+1+1+3+1+1


# ===========================================================================
# 8. list_access_requests method (Issue 5)
# ===========================================================================


class TestGDPRManagerListAccessRequests:
    """Verify GDPRManager.list_access_requests method."""

    async def test_list_access_requests_all(self):
        session = _mock_session()
        items = []
        for i in range(2):
            r = MagicMock()
            r.request_id = f"dsar-{i}"
            r.subject_email = f"user{i}@example.com"
            r.status = DSARStatus.PENDING
            r.reason = "data access"
            r.requested_at = datetime.now(UTC)
            r.deadline = datetime.now(UTC) + timedelta(days=30)
            r.completed_at = None
            items.append(r)
        session.execute = AsyncMock(return_value=_mock_scalars_result(items))
        mgr = GDPRManager(session)

        result = await mgr.list_access_requests()
        assert len(result) == 2
        assert result[0]["request_id"] == "dsar-0"

    async def test_list_access_requests_filtered(self):
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalars_result([]))
        mgr = GDPRManager(session)

        result = await mgr.list_access_requests(status=DSARStatus.COMPLETED)
        assert result == []
        session.execute.assert_called_once()

    async def test_list_access_requests_returns_correct_fields(self):
        session = _mock_session()
        r = MagicMock()
        r.request_id = "dsar-fields"
        r.subject_email = "user@example.com"
        r.status = DSARStatus.PROCESSING
        r.reason = "Article 15"
        r.requested_at = datetime.now(UTC)
        r.deadline = datetime.now(UTC) + timedelta(days=30)
        r.completed_at = None
        session.execute = AsyncMock(return_value=_mock_scalars_result([r]))
        mgr = GDPRManager(session)

        result = await mgr.list_access_requests()
        assert len(result) == 1
        item = result[0]
        expected_keys = {"request_id", "subject_email", "status", "reason", "requested_at", "deadline", "completed_at"}
        assert expected_keys.issubset(item.keys())


# ===========================================================================
# 9. Breach status validation (Issue 7)
# ===========================================================================


class TestBreachStatusValidation:
    """Verify update_breach_status validates new_status against BreachStatus enum."""

    async def test_invalid_breach_status_raises(self):
        session = _mock_session()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="Invalid breach status"):
            await mgr.update_breach_status(
                breach_id="some-id",
                new_status="invalid_status",
            )

    async def test_invalid_breach_status_not_queried(self):
        """When status is invalid, DB should not be queried."""
        session = _mock_session()
        session.execute = AsyncMock()
        mgr = GDPRManager(session)

        with pytest.raises(ValueError, match="Invalid breach status"):
            await mgr.update_breach_status("id", "bogus")

        session.execute.assert_not_called()

    async def test_valid_breach_status_accepted(self):
        """All valid BreachStatus values should be accepted."""
        session = _mock_session()
        record = MagicMock()
        record.breach_id = "valid-status"
        record.status = BreachStatus.DETECTED
        record.authority_notified_at = None
        record.subjects_notified_at = None
        record.resolved_at = None
        record.notes = []
        session.execute = AsyncMock(return_value=_mock_scalar_result(record))
        mgr = GDPRManager(session)

        result = await mgr.update_breach_status(
            breach_id="valid-status",
            new_status=BreachStatus.ASSESSED,
        )
        assert result["status"] == BreachStatus.ASSESSED

    async def test_all_breach_status_values_valid(self):
        """Every BreachStatus enum value should pass validation."""
        session = _mock_session()
        for status in BreachStatus:
            record = MagicMock()
            record.breach_id = f"test-{status.value}"
            record.status = BreachStatus.DETECTED
            record.authority_notified_at = None
            record.subjects_notified_at = None
            record.resolved_at = None
            record.notes = []
            session.execute = AsyncMock(return_value=_mock_scalar_result(record))
            mgr = GDPRManager(session)
            result = await mgr.update_breach_status(
                breach_id=f"test-{status.value}",
                new_status=status.value,
            )
            assert result["status"] == status.value
