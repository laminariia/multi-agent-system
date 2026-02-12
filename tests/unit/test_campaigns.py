"""Unit tests for Campaign CRUD API (src.api.routes.campaigns).

We test route handlers directly via ``.fn()`` to avoid needing a full app instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import HTTPException

from src.api.routes.campaigns import CampaignController, _get_campaign_or_404
from src.api.schemas import (
    CampaignCreateSchema,
    CampaignUpdateSchema,
)


def _make_campaign(**overrides: object) -> MagicMock:
    """Create a mock EmailCampaign with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "name": "Test Campaign",
        "subject_template": "Hello {{name}}",
        "body_template": "Dear {{name}}, ...",
        "target_cities": ["Berlin"],
        "target_categories": ["restaurant"],
        "status": "draft",
        "total_leads": 0,
        "sent_count": 0,
        "open_count": 0,
        "reply_count": 0,
        "bounce_count": 0,
        "created_at": datetime.now(UTC),
        "started_at": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


class TestListCampaigns:
    """Tests for GET /api/v1/campaigns."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self) -> None:
        """Should return empty campaigns list with total 0."""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[mock_result, MagicMock(scalar=MagicMock(return_value=0))])

        ctrl = CampaignController(owner=MagicMock())
        result = await ctrl.list_campaigns.fn(ctrl, db_session=db)

        assert result.total == 0
        assert result.campaigns == []

    @pytest.mark.asyncio
    async def test_returns_campaigns(self) -> None:
        """Should return campaigns with correct total count."""
        campaign = _make_campaign()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [campaign]
        db.execute = AsyncMock(side_effect=[mock_result, MagicMock(scalar=MagicMock(return_value=1))])

        ctrl = CampaignController(owner=MagicMock())
        result = await ctrl.list_campaigns.fn(ctrl, db_session=db)

        assert result.total == 1
        assert len(result.campaigns) == 1
        assert result.campaigns[0].name == "Test Campaign"


class TestGetCampaign:
    """Tests for GET /api/v1/campaigns/{id}."""

    @pytest.mark.asyncio
    async def test_returns_campaign_by_id(self) -> None:
        """Should return campaign details when found."""
        campaign = _make_campaign()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=campaign),
        ))

        ctrl = CampaignController(owner=MagicMock())
        result = await ctrl.get_campaign.fn(ctrl, db_session=db, campaign_id=str(campaign.id))

        assert result.name == "Test Campaign"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Should raise 404 when campaign doesn't exist."""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
        ))

        with pytest.raises(HTTPException) as exc_info:
            await _get_campaign_or_404(db, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_400_for_invalid_uuid(self) -> None:
        """Should raise 400 for malformed campaign ID."""
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await _get_campaign_or_404(db, "not-a-uuid")
        assert exc_info.value.status_code == 400


class TestCreateCampaign:
    """Tests for POST /api/v1/campaigns."""

    @pytest.mark.asyncio
    async def test_creates_campaign_with_draft_status(self) -> None:
        """Should create campaign in 'draft' status."""
        campaign = _make_campaign(status="draft")
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        db.add = MagicMock()

        ctrl = CampaignController(owner=MagicMock())

        with patch("src.api.routes.campaigns.EmailCampaign", return_value=campaign):
            result = await ctrl.create_campaign.fn(
                ctrl,
                data=CampaignCreateSchema(
                    name="New Campaign",
                    subject_template="Hi {{name}}",
                    body_template="Dear {{name}}...",
                    target_cities=["Berlin"],
                ),
                db_session=db,
            )

        assert result.status == "draft"
        db.add.assert_called_once()
        db.flush.assert_awaited_once()


class TestUpdateCampaign:
    """Tests for PUT /api/v1/campaigns/{id}."""

    @pytest.mark.asyncio
    async def test_updates_campaign_fields(self) -> None:
        """Should update only provided fields."""
        campaign = _make_campaign()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=campaign),
        ))
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        ctrl = CampaignController(owner=MagicMock())
        await ctrl.update_campaign.fn(
            ctrl,
            data=CampaignUpdateSchema(name="Updated Name"),
            db_session=db,
            campaign_id=str(campaign.id),
        )

        assert campaign.name == "Updated Name"


class TestDeleteCampaign:
    """Tests for DELETE /api/v1/campaigns/{id}."""

    @pytest.mark.asyncio
    async def test_deletes_campaign(self) -> None:
        """Should delete campaign and return confirmation."""
        campaign = _make_campaign()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=campaign),
        ))
        db.delete = AsyncMock()
        db.flush = AsyncMock()

        ctrl = CampaignController(owner=MagicMock())
        result = await ctrl.delete_campaign.fn(ctrl, db_session=db, campaign_id=str(campaign.id))

        assert result["status"] == "deleted"
        db.delete.assert_awaited_once_with(campaign)


class TestStartCampaign:
    """Tests for POST /api/v1/campaigns/{id}/start."""

    @pytest.mark.asyncio
    async def test_rejects_start_for_active_campaign(self) -> None:
        """Should reject starting an already active campaign."""
        campaign = _make_campaign(status="active")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=campaign),
        ))

        ctrl = CampaignController(owner=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await ctrl.start_campaign.fn(ctrl, db_session=db, campaign_id=str(campaign.id))
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_starts_draft_campaign(self) -> None:
        """Should set status to active and attach leads."""
        campaign = _make_campaign(status="draft")
        lead = MagicMock(id=uuid.uuid4(), status="enriched", email="test@test.com")

        db = AsyncMock()
        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(scalar_one_or_none=MagicMock(return_value=campaign))
            if call_count == 2:
                return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[lead]))))
            return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        ctrl = CampaignController(owner=MagicMock())
        result = await ctrl.start_campaign.fn(ctrl, db_session=db, campaign_id=str(campaign.id))

        assert result.status == "active"


class TestCampaignSchemas:
    """Tests for campaign Pydantic schemas."""

    def test_create_schema_validates_name_min_length(self) -> None:
        """Should reject empty name."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CampaignCreateSchema(
                name="",
                subject_template="s",
                body_template="b",
            )

    def test_create_schema_accepts_valid_input(self) -> None:
        """Should accept valid campaign creation data."""
        schema = CampaignCreateSchema(
            name="My Campaign",
            subject_template="Hello",
            body_template="Body",
            target_cities=["Berlin"],
        )
        assert schema.name == "My Campaign"
        assert schema.target_cities == ["Berlin"]

    def test_update_schema_all_fields_optional(self) -> None:
        """All fields in update schema should be optional."""
        schema = CampaignUpdateSchema()
        assert schema.name is None
        assert schema.status is None
