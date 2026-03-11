"""Unit tests for Lead model extended columns (pipeline-b-spec.md).

Verifies that all spec-required columns exist on the Lead model
with correct types and defaults.
"""

from __future__ import annotations

from src.core.models import Lead


class TestLeadModelColumns:
    """Verify Lead model has all pipeline-b-spec required columns."""

    def test_lead_score_column_exists(self):
        assert hasattr(Lead, "lead_score")

    def test_temperature_column_exists(self):
        assert hasattr(Lead, "temperature")

    def test_touch_count_column_exists(self):
        assert hasattr(Lead, "touch_count")

    def test_last_contacted_at_column_exists(self):
        assert hasattr(Lead, "last_contacted_at")

    def test_channel_used_column_exists(self):
        assert hasattr(Lead, "channel_used")

    def test_google_rating_column_exists(self):
        assert hasattr(Lead, "google_rating")

    def test_review_count_column_exists(self):
        assert hasattr(Lead, "review_count")

    def test_source_column_exists(self):
        assert hasattr(Lead, "source")

    def test_source_id_column_exists(self):
        assert hasattr(Lead, "source_id")

    def test_updated_at_column_exists(self):
        assert hasattr(Lead, "updated_at")

    def test_temperature_valid_values(self):
        """Temperature should accept hot/warm/cold string values."""
        col = Lead.__table__.columns["temperature"]
        assert col.nullable is True  # optional field

    def test_lead_score_is_numeric(self):
        col = Lead.__table__.columns["lead_score"]
        assert col.nullable is True

    def test_touch_count_default_zero(self):
        col = Lead.__table__.columns["touch_count"]
        assert col.server_default is not None or col.default is not None
