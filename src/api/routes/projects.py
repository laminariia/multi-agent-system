"""Project management routes (Kanban).

Projects map directly from the ``projects`` table.  They are created
automatically by Pipeline A when a bid is won, or manually via the API.

Mounted at ``/api/v1/projects``.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from litestar import Controller, get, patch
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.routes import _escape_like
from src.core.models import Project

logger = structlog.get_logger(__name__)


class ProjectController(Controller):
    """Browse and manage active projects (Kanban view)."""

    path = "/api/v1/projects"
    tags = ["projects"]

    # -----------------------------------------------------------------
    # GET /api/v1/projects
    # -----------------------------------------------------------------

    @get(
        "/",
        summary="List projects",
        description="Paginated project list filterable by status, type, and search.",
    )
    async def list_projects(
        self,
        db_session: AsyncSession,
        status: str | None = Parameter(
            default=None,
            description="Filter: planning | in_progress | review | completed | cancelled",
        ),
        type: str | None = Parameter(
            default=None,
            description="Filter: freelance | outreach | manual",
        ),
        kanban_column: str | None = Parameter(
            default=None,
            description="Filter by Kanban column name",
        ),
        search: str | None = Parameter(
            default=None,
            description="Search projects by title (case-insensitive substring)",
        ),
        sort: str | None = Parameter(
            default=None,
            description="Sort order: newest | oldest | deadline_asc | progress_desc",
        ),
        limit: int = Parameter(default=50, ge=1, le=200),
        offset: int = Parameter(default=0, ge=0),
    ) -> dict[str, Any]:
        """Return a filtered, paginated list of projects."""
        stmt = select(Project).options(selectinload(Project.tasks))

        if status is not None:
            stmt = stmt.where(Project.status == status)
        if type is not None:
            stmt = stmt.where(Project.type == type)
        if kanban_column is not None:
            stmt = stmt.where(Project.kanban_column == kanban_column)
        if isinstance(search, str):
            stmt = stmt.where(Project.title.ilike("%" + _escape_like(search) + "%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        sort_map = {
            "newest": Project.created_at.desc(),
            "oldest": Project.created_at.asc(),
            "deadline_asc": Project.deadline.asc().nulls_last(),
            "progress_desc": Project.progress.desc(),
        }
        order = sort_map.get(sort or "", Project.created_at.desc())
        stmt = stmt.order_by(order).limit(limit).offset(offset)
        result = await db_session.execute(stmt)
        projects = result.scalars().unique().all()

        return {
            "total": total,
            "projects": [_project_to_dict(p) for p in projects],
        }

    # -----------------------------------------------------------------
    # GET /api/v1/projects/stats
    # -----------------------------------------------------------------

    @get("/stats", summary="Project statistics for Kanban header")
    async def stats(
        self,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Return aggregated project stats by status and type."""
        status_stmt = select(Project.status, func.count()).group_by(Project.status)
        status_rows = (await db_session.execute(status_stmt)).all()
        by_status = {row[0]: row[1] for row in status_rows}

        type_stmt = select(Project.type, func.count()).group_by(Project.type)
        type_rows = (await db_session.execute(type_stmt)).all()
        by_type = {row[0]: row[1] for row in type_rows}

        total = (await db_session.execute(select(func.count()).select_from(Project))).scalar_one()

        return {
            "total": total,
            "by_status": by_status,
            "by_type": by_type,
        }

    # -----------------------------------------------------------------
    # GET /api/v1/projects/{project_id}
    # -----------------------------------------------------------------

    @get(
        "/{project_id:uuid}",
        summary="Project detail",
        description="Full project detail including tasks and revision count.",
    )
    async def get_project(
        self,
        project_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Retrieve a single project with its tasks.

        Raises:
            NotFoundException: When no project matches the given UUID.
        """
        stmt = (
            select(Project)
            .where(Project.id == project_id)
            .options(
                selectinload(Project.tasks),
                selectinload(Project.revisions),
            )
        )
        result = await db_session.execute(stmt)
        project = result.scalar_one_or_none()

        if project is None:
            raise NotFoundException(detail=f"Project {project_id} not found")

        return _project_to_dict(project, detailed=True)

    # -----------------------------------------------------------------
    # PATCH /api/v1/projects/{project_id}
    # -----------------------------------------------------------------

    @patch(
        "/{project_id:uuid}",
        summary="Update project fields",
        status_code=200,
    )
    async def update_project(
        self,
        project_id: uuid.UUID,
        data: dict[str, Any],
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Update project status, kanban_column, progress, or client_name.

        Raises:
            NotFoundException: When the project does not exist.
        """
        stmt = select(Project).where(Project.id == project_id)
        result = await db_session.execute(stmt)
        project = result.scalar_one_or_none()

        if project is None:
            raise NotFoundException(detail=f"Project {project_id} not found")

        allowed_fields = {"status", "kanban_column", "kanban_order", "progress", "client_name"}
        updated: list[str] = []
        for field in allowed_fields:
            if field in data and data[field] is not None:
                setattr(project, field, data[field])
                updated.append(field)

        await db_session.flush()
        logger.info("project.updated", project_id=str(project_id), fields=updated)

        return {"id": str(project.id), "status": project.status, "updated_fields": updated}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_to_dict(project: Project, *, detailed: bool = False) -> dict[str, Any]:
    """Serialize a Project ORM instance to a dict."""
    d: dict[str, Any] = {
        "id": str(project.id),
        "bid_id": str(project.bid_id) if project.bid_id else None,
        "job_id": str(project.job_id) if project.job_id else None,
        "title": project.title,
        "type": project.type,
        "client_name": project.client_name,
        "status": project.status,
        "progress": project.progress,
        "revision_count": project.revision_count,
        "agreed_amount": float(project.agreed_amount) if project.agreed_amount is not None else None,
        "currency": project.currency,
        "paid_amount": float(project.paid_amount) if project.paid_amount is not None else None,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "completed_at": project.completed_at.isoformat() if project.completed_at else None,
        "kanban_column": project.kanban_column,
        "kanban_order": project.kanban_order,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }
    if detailed:
        d["tasks"] = [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "assigned_agent": t.assigned_agent,
                "priority": t.priority,
                "estimated_hours": float(t.estimated_hours) if t.estimated_hours else None,
            }
            for t in (project.tasks or [])
        ]
        d["revisions"] = len(project.revisions or [])
    else:
        d["task_count"] = len(project.tasks or []) if project.tasks is not None else 0

    return d
