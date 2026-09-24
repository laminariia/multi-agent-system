"""Portfolio management routes.

Provides endpoints for managing portfolio projects that can be attached
to bids as social proof.

Mounted at ``/api/v1/portfolio``.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from litestar import Controller, delete, get, patch, post
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.routes import _escape_like
from src.core.models import PortfolioProject

logger = structlog.get_logger(__name__)


class PortfolioController(Controller):
    """Manage portfolio projects."""

    path = "/api/v1/portfolio"
    tags = ["portfolio"]

    # -----------------------------------------------------------------
    # GET /api/v1/portfolio
    # -----------------------------------------------------------------

    @get(
        "/",
        summary="List portfolio projects",
        description="Paginated portfolio list filterable by platform, status, and search.",
    )
    async def list_portfolio(
        self,
        db_session: AsyncSession,
        platform: str | None = Parameter(
            default=None,
            description="Filter by platform: freelancer | upwork | fl_ru | kwork | direct",
        ),
        status: str | None = Parameter(
            default=None,
            description="Filter: published | draft | archived",
        ),
        search: str | None = Parameter(
            default=None,
            description="Search by title (case-insensitive substring)",
        ),
        limit: int = Parameter(default=20, ge=1, le=100),
        offset: int = Parameter(default=0, ge=0),
    ) -> dict[str, Any]:
        """Return a filtered, paginated list of portfolio projects."""
        stmt = select(PortfolioProject)

        if platform is not None:
            stmt = stmt.where(PortfolioProject.platform == platform)
        if status is not None:
            stmt = stmt.where(PortfolioProject.status == status)
        if isinstance(search, str):
            stmt = stmt.where(PortfolioProject.title.ilike("%" + _escape_like(search) + "%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(PortfolioProject.created_at.desc()).limit(limit).offset(offset)
        result = await db_session.execute(stmt)
        items = result.scalars().all()

        return {
            "total": total,
            "portfolio": [_portfolio_to_dict(p) for p in items],
        }

    # -----------------------------------------------------------------
    # POST /api/v1/portfolio
    # -----------------------------------------------------------------

    @post(
        "/",
        summary="Create portfolio project",
        guards=[require_role("owner", "co_owner")],
        status_code=201,
    )
    async def create_portfolio(
        self,
        data: dict[str, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Create a new portfolio project.

        Required fields: ``title``.
        Optional: ``platform``, ``status``, ``description``, ``tech_stack``,
        ``url``, ``thumbnail_url``.
        """
        title = data.get("title", "").strip()
        if not title:
            from litestar.exceptions import ValidationException  # noqa: PLC0415

            raise ValidationException("Field 'title' is required and must not be empty")

        project = PortfolioProject(
            title=title,
            platform=data.get("platform", "direct"),
            status=data.get("status", "draft"),
            description=data.get("description"),
            tech_stack=data.get("tech_stack"),
            url=data.get("url"),
            thumbnail_url=data.get("thumbnail_url"),
        )
        db_session.add(project)
        await db_session.flush()

        logger.info("portfolio.created", portfolio_id=str(project.id), title=title)

        return {"status": "created", "id": str(project.id), "title": project.title}

    # -----------------------------------------------------------------
    # GET /api/v1/portfolio/{portfolio_id}
    # -----------------------------------------------------------------

    @get(
        "/{portfolio_id:uuid}",
        summary="Portfolio project detail",
    )
    async def get_portfolio(
        self,
        portfolio_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Retrieve a single portfolio project.

        Raises:
            NotFoundException: When no portfolio project matches the given UUID.
        """
        stmt = select(PortfolioProject).where(PortfolioProject.id == portfolio_id)
        result = await db_session.execute(stmt)
        project = result.scalar_one_or_none()

        if project is None:
            raise NotFoundException(detail=f"Portfolio project {portfolio_id} not found")

        return _portfolio_to_dict(project)

    # -----------------------------------------------------------------
    # PATCH /api/v1/portfolio/{portfolio_id}
    # -----------------------------------------------------------------

    @patch(
        "/{portfolio_id:uuid}",
        summary="Update portfolio project",
        guards=[require_role("owner", "co_owner")],
        status_code=200,
    )
    async def update_portfolio(
        self,
        portfolio_id: uuid.UUID,
        data: dict[str, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Update portfolio project fields.

        Raises:
            NotFoundException: When the portfolio project does not exist.
        """
        stmt = select(PortfolioProject).where(PortfolioProject.id == portfolio_id)
        result = await db_session.execute(stmt)
        project = result.scalar_one_or_none()

        if project is None:
            raise NotFoundException(detail=f"Portfolio project {portfolio_id} not found")

        allowed_fields = {"title", "platform", "status", "description", "tech_stack", "url", "thumbnail_url"}
        updated: list[str] = []
        for field in allowed_fields:
            if field in data:
                setattr(project, field, data[field])
                updated.append(field)

        await db_session.flush()
        logger.info("portfolio.updated", portfolio_id=str(portfolio_id), fields=updated)

        return {"id": str(project.id), "updated_fields": updated}

    # -----------------------------------------------------------------
    # DELETE /api/v1/portfolio/{portfolio_id}
    # -----------------------------------------------------------------

    @delete(
        "/{portfolio_id:uuid}",
        summary="Delete portfolio project",
        guards=[require_role("owner", "co_owner")],
        status_code=204,
    )
    async def delete_portfolio(
        self,
        portfolio_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> None:
        """Delete a portfolio project.

        Raises:
            NotFoundException: When the portfolio project does not exist.
        """
        stmt = select(PortfolioProject).where(PortfolioProject.id == portfolio_id)
        result = await db_session.execute(stmt)
        project = result.scalar_one_or_none()

        if project is None:
            raise NotFoundException(detail=f"Portfolio project {portfolio_id} not found")

        await db_session.delete(project)
        logger.info("portfolio.deleted", portfolio_id=str(portfolio_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _portfolio_to_dict(project: PortfolioProject) -> dict[str, Any]:
    """Serialize a PortfolioProject ORM instance to a dict."""
    return {
        "id": str(project.id),
        "title": project.title,
        "platform": project.platform,
        "status": project.status,
        "description": project.description,
        "tech_stack": project.tech_stack,
        "url": project.url,
        "thumbnail_url": project.thumbnail_url,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }
