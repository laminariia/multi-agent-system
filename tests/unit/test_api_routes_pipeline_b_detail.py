"""Unit tests for Pipeline B lead detail and search endpoints.

Tests the ``PipelineBController.get_lead`` and ``list_leads`` (search param)
route handlers with mocked database dependencies.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.exceptions import HTTPException

from src.api.routes.pipeline_b import PipelineBController
from src.core.models import Lead

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_mock_lead(
    lead_id: uuid.UUID | None = None,
    with_detail_fields: bool = True,
) -> MagicMock:
    """Create a mock Lead ORM instance with all fields populated."""
    if lead_id is None:
        lead_id = uuid.uuid4()

    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = lead_id
    mock_lead.name = "Test Bakery"
    mock_lead.category = "bakery"
    mock_lead.city = "Berlin"
    mock_lead.address = "Alexanderplatz 1"
    mock_lead.phone = "+491234567890"
    mock_lead.email = "info@testbakery.de"
    mock_lead.status = "enriched"
    mock_lead.enrichment_source = "hunter"
    mock_lead.discovered_at = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)

    if with_detail_fields:
        mock_lead.country = "DE"
        mock_lead.latitude = Decimal("52.52164300")
        mock_lead.longitude = Decimal("13.41330600")
        mock_lead.h3_index = "891f1d4d9dfffff"
        mock_lead.website = "https://testbakery.de"
        mock_lead.social_links = {"facebook": "https://fb.com/testbakery"}
        mock_lead.enrichment_cost = Decimal("0.0100")
        mock_lead.enrichment_data = {"hunter_score": 85, "raw": "data"}
        mock_lead.osm_id = 987654321
    else:
        mock_lead.country = None
        mock_lead.latitude = None
        mock_lead.longitude = None
        mock_lead.h3_index = None
        mock_lead.website = None
        mock_lead.social_links = None
        mock_lead.enrichment_cost = None
        mock_lead.enrichment_data = None
        mock_lead.osm_id = None

    return mock_lead


# ---------------------------------------------------------------------------
# get_lead tests
# ---------------------------------------------------------------------------


class TestGetLead:
    """Tests for PipelineBController.get_lead endpoint."""

    @pytest.mark.asyncio
    async def test_get_lead_found(self) -> None:
        """Should return full lead details including all detail fields when lead exists."""
        db_session = AsyncMock()
        lead_id = uuid.uuid4()
        mock_lead = _create_mock_lead(lead_id=lead_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = mock_lead
        db_session.execute.return_value = query_result

        result = await PipelineBController.get_lead.fn(
            self=None,
            db_session=db_session,
            lead_id=str(lead_id),
        )

        # Core fields
        assert result["id"] == str(lead_id)
        assert result["name"] == "Test Bakery"
        assert result["category"] == "bakery"
        assert result["city"] == "Berlin"
        assert result["address"] == "Alexanderplatz 1"
        assert result["phone"] == "+491234567890"
        assert result["email"] == "info@testbakery.de"
        assert result["status"] == "enriched"
        assert result["enrichment_source"] == "hunter"
        assert result["discovered_at"] == "2026-02-10T12:00:00+00:00"

        # Detail fields (new)
        assert result["country"] == "DE"
        assert result["latitude"] == float(Decimal("52.52164300"))
        assert result["longitude"] == float(Decimal("13.41330600"))
        assert result["h3_index"] == "891f1d4d9dfffff"
        assert result["website"] == "https://testbakery.de"
        assert result["social_links"] == {"facebook": "https://fb.com/testbakery"}
        assert result["enrichment_cost"] == float(Decimal("0.0100"))
        assert result["enrichment_data"] == {"hunter_score": 85, "raw": "data"}
        assert result["osm_id"] == 987654321

    @pytest.mark.asyncio
    async def test_get_lead_not_found(self) -> None:
        """Should raise HTTPException 404 when lead does not exist in the database."""
        db_session = AsyncMock()
        lead_id = uuid.uuid4()

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = query_result

        with pytest.raises(HTTPException) as exc_info:
            await PipelineBController.get_lead.fn(
                self=None,
                db_session=db_session,
                lead_id=str(lead_id),
            )

        assert exc_info.value.status_code == 404
        assert "Lead not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_lead_invalid_uuid(self) -> None:
        """Should raise HTTPException 404 when lead_id is not a valid UUID."""
        db_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await PipelineBController.get_lead.fn(
                self=None,
                db_session=db_session,
                lead_id="invalid-uuid",
            )

        assert exc_info.value.status_code == 404
        assert "Lead not found" in exc_info.value.detail
        # Database should NOT have been queried
        db_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_lead_nullable_detail_fields(self) -> None:
        """Should return None for nullable detail fields when they are not set."""
        db_session = AsyncMock()
        lead_id = uuid.uuid4()
        mock_lead = _create_mock_lead(lead_id=lead_id, with_detail_fields=False)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = mock_lead
        db_session.execute.return_value = query_result

        result = await PipelineBController.get_lead.fn(
            self=None,
            db_session=db_session,
            lead_id=str(lead_id),
        )

        assert result["country"] is None
        assert result["latitude"] is None
        assert result["longitude"] is None
        assert result["h3_index"] is None
        assert result["website"] is None
        assert result["social_links"] is None
        assert result["enrichment_cost"] is None
        assert result["enrichment_data"] is None
        assert result["osm_id"] is None


# ---------------------------------------------------------------------------
# list_leads search parameter tests
# ---------------------------------------------------------------------------


class TestListLeadsSearch:
    """Tests for PipelineBController.list_leads search filtering."""

    @pytest.mark.asyncio
    async def test_list_leads_search(self) -> None:
        """Should return leads matching search term via ilike filter on name."""
        db_session = AsyncMock()

        mock_lead = _create_mock_lead()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_lead]
        mock_exec_result_1 = MagicMock()
        mock_exec_result_1.scalars.return_value = mock_scalars

        mock_exec_result_2 = MagicMock()
        mock_exec_result_2.scalar.return_value = 1

        db_session.execute = AsyncMock(side_effect=[mock_exec_result_1, mock_exec_result_2])

        result = await PipelineBController.list_leads.fn(
            self=None,
            db_session=db_session,
            city=None,
            status=None,
            search="Bakery",
            limit=50,
            offset=0,
        )

        assert result["total"] == 1
        assert len(result["leads"]) == 1
        assert result["leads"][0]["name"] == "Test Bakery"

        # Verify two DB queries were made (leads query + count query)
        assert db_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_list_leads_search_none(self) -> None:
        """Should not apply name filter when search parameter is None."""
        db_session = AsyncMock()

        mock_lead_1 = _create_mock_lead()
        mock_lead_1.name = "Bakery A"
        mock_lead_2 = _create_mock_lead()
        mock_lead_2.name = "Cafe B"

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_lead_1, mock_lead_2]
        mock_exec_result_1 = MagicMock()
        mock_exec_result_1.scalars.return_value = mock_scalars

        mock_exec_result_2 = MagicMock()
        mock_exec_result_2.scalar.return_value = 2

        db_session.execute = AsyncMock(side_effect=[mock_exec_result_1, mock_exec_result_2])

        result = await PipelineBController.list_leads.fn(
            self=None,
            db_session=db_session,
            city=None,
            status=None,
            search=None,
            limit=50,
            offset=0,
        )

        assert result["total"] == 2
        assert len(result["leads"]) == 2
        assert result["leads"][0]["name"] == "Bakery A"
        assert result["leads"][1]["name"] == "Cafe B"
