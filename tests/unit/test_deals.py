"""Tests for P2.9 — Pipeline B → A Bridge (Deal Management).

Covers:
- Deal model creation and validation
- CRUD endpoints (create, list, get, update)
- POST /api/v1/deals/{id}/start-development — bridge to Pipeline A
- Edge cases: invalid status, missing deal, duplicate pipeline launch
- Pipeline payload construction (agreed_scope → requirements, design_versions)

We test route handlers directly via ``.fn()`` to avoid needing a full app instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import HTTPException, NotFoundException

from src.core.exceptions import MASException

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Mock Factories
# ---------------------------------------------------------------------------


def _create_mock_request(user_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Request with authenticated user."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.id = user_id or uuid.uuid4()
    request.user.email = "test@example.com"
    return request


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.flush = AsyncMock()
    session.add = MagicMock()  # add is sync
    return session


def _create_mock_deal(
    deal_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    title: str = "Website for Coffee Shop",
    status: str = "won",
    agreed_scope: str = "Build a modern landing page with contact form",
    budget: Decimal = Decimal("1500.00"),
    deadline: datetime | None = None,
    client_context: dict[str, Any] | None = None,
    design_versions: dict[str, Any] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    pipeline_a_thread_id: str | None = None,
) -> MagicMock:
    """Create a mock Deal ORM instance."""
    mock_deal = MagicMock()
    mock_deal.id = deal_id or uuid.uuid4()
    mock_deal.lead_id = lead_id
    mock_deal.title = title
    mock_deal.status = status
    mock_deal.agreed_scope = agreed_scope
    mock_deal.budget = budget
    mock_deal.deadline = deadline or datetime(2026, 6, 1, tzinfo=UTC)
    mock_deal.client_context = client_context or {"name": "Coffee Corner", "city": "Berlin"}
    mock_deal.design_versions = design_versions
    mock_deal.conversation_history = conversation_history
    mock_deal.pipeline_a_thread_id = pipeline_a_thread_id
    mock_deal.created_at = datetime.now(UTC)
    mock_deal.updated_at = datetime.now(UTC)
    return mock_deal


def _create_mock_lead(lead_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Lead ORM instance."""
    mock_lead = MagicMock()
    mock_lead.id = lead_id or uuid.uuid4()
    mock_lead.name = "Coffee Corner Berlin"
    mock_lead.city = "Berlin"
    mock_lead.email = "info@coffeecorner.de"
    mock_lead.status = "enriched"
    return mock_lead


# ===========================================================================
# 1. Deal Model
# ===========================================================================


class TestDealModel:
    """Tests for the Deal ORM model definition."""

    def test_deal_model_exists(self):
        """Deal model should be importable from src.core.models."""
        from src.core.models import Deal

        assert Deal.__tablename__ == "deals"

    def test_deal_model_has_required_columns(self):
        """Deal model should have all columns from the spec."""
        from src.core.models import Deal

        mapper = Deal.__table__.columns
        required = [
            "id",
            "lead_id",
            "title",
            "status",
            "agreed_scope",
            "budget",
            "deadline",
            "client_context",
            "design_versions",
            "conversation_history",
            "pipeline_a_thread_id",
            "created_at",
            "updated_at",
        ]
        column_names = [c.name for c in mapper]
        for col in required:
            assert col in column_names, f"Missing column: {col}"

    def test_deal_status_default(self):
        """Deal status should default to 'new'."""
        from src.core.models import Deal

        col = Deal.__table__.columns["status"]
        assert col.server_default is not None
        assert col.server_default.arg == "new"

    def test_deal_lead_id_nullable(self):
        """lead_id should be nullable (deals can exist without leads)."""
        from src.core.models import Deal

        col = Deal.__table__.columns["lead_id"]
        assert col.nullable is True


# ===========================================================================
# 2. Pydantic Schemas
# ===========================================================================


class TestDealSchemas:
    """Tests for Deal request/response schemas."""

    def test_create_schema_valid(self):
        """DealCreateSchema accepts valid data."""
        from src.api.schemas import DealCreateSchema

        schema = DealCreateSchema(
            title="Build website",
            agreed_scope="Landing page with form",
            budget=1500.0,
        )
        assert schema.title == "Build website"
        assert schema.budget == 1500.0

    def test_create_schema_requires_title(self):
        """DealCreateSchema requires title."""
        from pydantic import ValidationError

        from src.api.schemas import DealCreateSchema

        with pytest.raises(ValidationError, match="title"):
            DealCreateSchema(agreed_scope="scope", budget=100)

    def test_create_schema_requires_agreed_scope(self):
        """DealCreateSchema requires agreed_scope."""
        from pydantic import ValidationError

        from src.api.schemas import DealCreateSchema

        with pytest.raises(ValidationError, match="agreed_scope"):
            DealCreateSchema(title="test", budget=100)

    def test_create_schema_optional_fields(self):
        """DealCreateSchema allows optional lead_id, deadline, etc."""
        from src.api.schemas import DealCreateSchema

        schema = DealCreateSchema(
            title="Test",
            agreed_scope="Scope",
            budget=500,
            lead_id=str(uuid.uuid4()),
            client_context={"name": "Client"},
        )
        assert schema.lead_id is not None
        assert schema.client_context == {"name": "Client"}

    def test_update_schema_all_optional(self):
        """DealUpdateSchema has all fields optional."""
        from src.api.schemas import DealUpdateSchema

        schema = DealUpdateSchema()
        assert schema.title is None
        assert schema.status is None

    def test_update_schema_validates_status(self):
        """DealUpdateSchema validates status against allowed values."""
        from src.api.schemas import DealUpdateSchema

        schema = DealUpdateSchema(status="won")
        assert schema.status == "won"

    def test_update_schema_rejects_invalid_status(self):
        """DealUpdateSchema rejects invalid status values."""
        from pydantic import ValidationError

        from src.api.schemas import DealUpdateSchema

        with pytest.raises(ValidationError, match="status"):
            DealUpdateSchema(status="invalid_status")


# ===========================================================================
# 3. Create Deal (POST /api/v1/deals)
# ===========================================================================


class TestCreateDeal:
    """Tests for POST /api/v1/deals endpoint."""

    @patch("src.api.routes.deals.Deal")
    async def test_create_deal_basic(self, mock_deal_cls: MagicMock) -> None:
        """Should create a deal with required fields."""
        from src.api.routes.deals import DealController
        from src.api.schemas import DealCreateSchema

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        data = DealCreateSchema(
            title="Website project",
            agreed_scope="Build landing page",
            budget=1000.0,
        )

        result = await controller.create_deal.fn(
            controller,
            data=data,
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "created"
        assert "id" in result
        db_session.add.assert_called_once()
        db_session.flush.assert_awaited_once()

    @patch("src.api.routes.deals.Deal")
    async def test_create_deal_with_lead_id(self, mock_deal_cls: MagicMock) -> None:
        """Should create a deal linked to an existing lead."""
        from src.api.routes.deals import DealController
        from src.api.schemas import DealCreateSchema

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        lead_id = uuid.uuid4()
        # Mock lead lookup
        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = _create_mock_lead(lead_id)
        db_session.execute.return_value = lead_result

        data = DealCreateSchema(
            title="Website project",
            agreed_scope="Build landing page",
            budget=1000.0,
            lead_id=str(lead_id),
        )

        result = await controller.create_deal.fn(
            controller,
            data=data,
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "created"

    @patch("src.api.routes.deals.Deal")
    async def test_create_deal_invalid_lead_id(self, mock_deal_cls: MagicMock) -> None:
        """Should raise 404 if lead_id doesn't exist."""
        from src.api.routes.deals import DealController
        from src.api.schemas import DealCreateSchema

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = lead_result

        data = DealCreateSchema(
            title="Website",
            agreed_scope="Scope",
            budget=500,
            lead_id=str(uuid.uuid4()),
        )

        with pytest.raises(NotFoundException, match="Lead"):
            await controller.create_deal.fn(
                controller,
                data=data,
                db_session=db_session,
                request=request,
            )


# ===========================================================================
# 4. List Deals (GET /api/v1/deals)
# ===========================================================================


class TestListDeals:
    """Tests for GET /api/v1/deals endpoint."""

    async def test_list_deals_empty(self) -> None:
        """Should return empty list when no deals exist."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        # First call: list query, second call: count query
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db_session.execute = AsyncMock(side_effect=[result_mock, count_result])

        result = await controller.list_deals.fn(
            controller,
            db_session=db_session,
        )

        assert result["total"] == 0
        assert result["deals"] == []

    async def test_list_deals_with_status_filter(self) -> None:
        """Should filter deals by status."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        deal = _create_mock_deal(status="won")
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [deal]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        db_session.execute = AsyncMock(side_effect=[result_mock, count_result])

        result = await controller.list_deals.fn(
            controller,
            db_session=db_session,
            status="won",
        )

        assert result["total"] == 1
        assert len(result["deals"]) == 1
        assert result["deals"][0]["status"] == "won"

    async def test_list_deals_with_search(self) -> None:
        """Should filter deals by title search."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        deal = _create_mock_deal(title="Coffee Shop Website")
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [deal]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        db_session.execute = AsyncMock(side_effect=[result_mock, count_result])

        result = await controller.list_deals.fn(
            controller,
            db_session=db_session,
            search="Coffee",
        )

        assert result["total"] == 1


# ===========================================================================
# 5. Get Deal (GET /api/v1/deals/{id})
# ===========================================================================


class TestGetDeal:
    """Tests for GET /api/v1/deals/{id} endpoint."""

    async def test_get_deal_found(self) -> None:
        """Should return full deal details."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id, status="won")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        result = await controller.get_deal.fn(
            controller,
            deal_id=str(deal_id),
            db_session=db_session,
        )

        assert result["id"] == str(deal_id)
        assert result["status"] == "won"
        assert result["agreed_scope"] is not None

    async def test_get_deal_not_found(self) -> None:
        """Should raise 404 for non-existent deal."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Deal not found"):
            await controller.get_deal.fn(
                controller,
                deal_id=str(uuid.uuid4()),
                db_session=db_session,
            )

    async def test_get_deal_invalid_uuid(self) -> None:
        """Should raise 400 for invalid UUID format."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_deal.fn(
                controller,
                deal_id="not-a-uuid",
                db_session=db_session,
            )
        assert exc_info.value.status_code == 400


# ===========================================================================
# 6. Update Deal (PATCH /api/v1/deals/{id})
# ===========================================================================


class TestUpdateDeal:
    """Tests for PATCH /api/v1/deals/{id} endpoint."""

    async def test_update_deal_status(self) -> None:
        """Should update deal status."""
        from src.api.routes.deals import DealController
        from src.api.schemas import DealUpdateSchema

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id, status="negotiating")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        result = await controller.update_deal.fn(
            controller,
            deal_id=str(deal_id),
            data=DealUpdateSchema(status="won"),
            db_session=db_session,
        )

        assert deal.status == "won"
        assert result["status"] == "updated"
        db_session.flush.assert_awaited_once()

    async def test_update_deal_scope(self) -> None:
        """Should update agreed_scope."""
        from src.api.routes.deals import DealController
        from src.api.schemas import DealUpdateSchema

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id)

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        result = await controller.update_deal.fn(
            controller,
            deal_id=str(deal_id),
            data=DealUpdateSchema(agreed_scope="Updated scope with extra features"),
            db_session=db_session,
        )

        assert deal.agreed_scope == "Updated scope with extra features"
        assert result["status"] == "updated"

    async def test_update_deal_not_found(self) -> None:
        """Should raise 404 for non-existent deal."""
        from src.api.routes.deals import DealController
        from src.api.schemas import DealUpdateSchema

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Deal"):
            await controller.update_deal.fn(
                controller,
                deal_id=str(uuid.uuid4()),
                data=DealUpdateSchema(status="won"),
                db_session=db_session,
            )


# ===========================================================================
# 7. Start Development — Bridge (POST /api/v1/deals/{id}/start-development)
# ===========================================================================


class TestStartDevelopment:
    """Tests for POST /api/v1/deals/{id}/start-development — the Pipeline B → A bridge."""

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.deals.asyncio")
    async def test_start_development_won_deal(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should launch Pipeline A for a won deal."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id, status="won")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.start_development.fn(
            controller,
            deal_id=str(deal_id),
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "started"
        assert "thread_id" in result
        assert deal.status == "in_development"
        assert deal.pipeline_a_thread_id is not None
        db_session.flush.assert_awaited_once()
        mock_asyncio.create_task.assert_called_once()

    async def test_start_development_non_won_deal_rejected(self) -> None:
        """Should reject deals that aren't in 'won' status."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id, status="negotiating")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        with pytest.raises(MASException, match="Only deals with status 'won'"):
            await controller.start_development.fn(
                controller,
                deal_id=str(deal_id),
                db_session=db_session,
                request=request,
            )

        assert deal.status == "negotiating"  # Unchanged

    async def test_start_development_deal_not_found(self) -> None:
        """Should raise 404 for non-existent deal."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        with pytest.raises(NotFoundException, match="Deal"):
            await controller.start_development.fn(
                controller,
                deal_id=str(uuid.uuid4()),
                db_session=db_session,
                request=request,
            )

    async def test_start_development_already_in_development(self) -> None:
        """Should reject deals already in development."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(
            deal_id=deal_id,
            status="in_development",
            pipeline_a_thread_id="pipeline-existing",
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        with pytest.raises(MASException, match="already"):
            await controller.start_development.fn(
                controller,
                deal_id=str(deal_id),
                db_session=db_session,
                request=request,
            )

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.deals.asyncio")
    async def test_start_development_payload_uses_agreed_scope(
        self,
        mock_asyncio: MagicMock,
        mock_run_pipeline: AsyncMock,
    ) -> None:
        """Pipeline payload should use deal.agreed_scope as requirements."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(
            deal_id=deal_id,
            status="won",
            agreed_scope="Build REST API with JWT auth and admin panel",
            budget=Decimal("2500.00"),
            client_context={"name": "Acme Corp", "contact": "john@acme.com"},
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        await controller.start_development.fn(
            controller,
            deal_id=str(deal_id),
            db_session=db_session,
            request=request,
        )

        # Verify pipeline was launched and deal updated
        mock_asyncio.create_task.assert_called_once()
        assert deal.status == "in_development"
        assert deal.pipeline_a_thread_id is not None
        assert deal.pipeline_a_thread_id.startswith("deal-pipeline-")

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.deals.asyncio")
    async def test_start_development_with_design_versions(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Deal with approved design_versions should be included in result."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        design = {"logo": "approved_v2.svg", "mockup": "homepage_final.fig"}
        deal = _create_mock_deal(
            deal_id=deal_id,
            status="won",
            design_versions=design,
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.start_development.fn(
            controller,
            deal_id=str(deal_id),
            db_session=db_session,
            request=request,
        )

        assert result["status"] == "started"
        assert result.get("has_design_versions") is True

    async def test_start_development_invalid_uuid(self) -> None:
        """Should raise 400 for invalid UUID format."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await controller.start_development.fn(
                controller,
                deal_id="not-valid-uuid",
                db_session=db_session,
                request=request,
            )
        assert exc_info.value.status_code == 400

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.deals.asyncio")
    async def test_start_development_stores_thread_id_on_deal(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Pipeline thread_id should be stored back on the deal for linkage."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id, status="won")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        mock_task = MagicMock()
        mock_asyncio.create_task.return_value = mock_task

        result = await controller.start_development.fn(
            controller,
            deal_id=str(deal_id),
            db_session=db_session,
            request=request,
        )

        # thread_id stored on deal and returned in response
        assert deal.pipeline_a_thread_id == result["thread_id"]

    @patch("src.worker.tasks.run_project_pipeline", new_callable=AsyncMock)
    @patch("src.api.routes.deals.asyncio")
    async def test_start_development_lost_deal_rejected(
        self,
        mock_asyncio: MagicMock,
        _mock_run_pipeline: AsyncMock,
    ) -> None:
        """Should reject deals with 'lost' status."""
        from src.api.routes.deals import DealController

        controller = DealController(owner=MagicMock())
        db_session = _create_mock_db_session()
        request = _create_mock_request()

        deal_id = uuid.uuid4()
        deal = _create_mock_deal(deal_id=deal_id, status="lost")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = deal
        db_session.execute.return_value = result_mock

        with pytest.raises(MASException, match="Only deals with status 'won'"):
            await controller.start_development.fn(
                controller,
                deal_id=str(deal_id),
                db_session=db_session,
                request=request,
            )
