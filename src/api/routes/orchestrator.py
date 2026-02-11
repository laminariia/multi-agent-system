"""Orchestrator management routes.

Exposes the autonomous orchestrator runner's state to the dashboard.
Mounted at ``/api/v1/orchestrator``.
"""

from __future__ import annotations

from typing import Any

import structlog
from litestar import Controller, Request, delete, get, post
from litestar.exceptions import ClientException, NotFoundException
from litestar.security.jwt import Token
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.schemas import (
    GoalAddRequestSchema,
    GoalAddResponseSchema,
    GoalDeleteResponseSchema,
    GoalListResponseSchema,
    GoalSchema,
    HealthDimensionSchema,
    HealthProblemSchema,
    HealthReportSchema,
    LogLineSchema,
    LogResponseSchema,
    MilestoneSchema,
    OrchestratorStartResponseSchema,
    OrchestratorStatusSchema,
    OrchestratorStopResponseSchema,
    PhaseSchema,
)
from src.api.services.orchestrator import OrchestratorService
from src.core.models import User

logger = structlog.get_logger(__name__)

_svc = OrchestratorService()


class OrchestratorController(Controller):
    """REST interface for the autonomous orchestrator runner."""

    path = "/api/v1/orchestrator"
    tags = ["orchestrator"]

    # ------------------------------------------------------------------
    # GET /api/v1/orchestrator/status
    # ------------------------------------------------------------------

    @get(
        "/status",
        summary="Orchestrator runner status",
    )
    async def status(self, db_session: AsyncSession) -> OrchestratorStatusSchema:
        """Return whether the runner is alive, with goal and health summaries."""
        data = await _svc.get_status(db_session)
        return OrchestratorStatusSchema(**data)

    # ------------------------------------------------------------------
    # POST /api/v1/orchestrator/start
    # ------------------------------------------------------------------

    @post(
        "/start",
        summary="Start the orchestrator runner",
        guards=[require_role("owner", "co_owner")],
    )
    async def start(
        self,
        request: Request[User, Token, Any],
    ) -> OrchestratorStartResponseSchema:
        """Launch the runner as a detached process (agent-driven sessions)."""
        try:
            result = _svc.start_runner()
        except (RuntimeError, FileNotFoundError) as exc:
            raise ClientException(detail=str(exc), status_code=409) from exc

        logger.info("orchestrator.api.started", user=str(request.user.id))
        return OrchestratorStartResponseSchema(**result)

    # ------------------------------------------------------------------
    # POST /api/v1/orchestrator/stop
    # ------------------------------------------------------------------

    @post(
        "/stop",
        summary="Stop the orchestrator runner",
        guards=[require_role("owner", "co_owner")],
    )
    async def stop(
        self,
        request: Request[User, Token, Any],
    ) -> OrchestratorStopResponseSchema:
        """Terminate the runner process."""
        try:
            result = _svc.stop_runner()
        except RuntimeError as exc:
            raise ClientException(detail=str(exc), status_code=409) from exc

        logger.info("orchestrator.api.stopped", user=str(request.user.id))
        return OrchestratorStopResponseSchema(**result)

    # ------------------------------------------------------------------
    # GET /api/v1/orchestrator/goals
    # ------------------------------------------------------------------

    @get(
        "/goals",
        summary="List orchestrator goals",
    )
    async def list_goals(
        self,
        db_session: AsyncSession,
        status: str | None = None,
    ) -> GoalListResponseSchema:
        """Return goals from the database, optionally filtered by status."""
        data = await _svc.list_goals(db_session, status_filter=status)
        return GoalListResponseSchema(
            goals=[GoalSchema(**g) for g in data["goals"]],
            total=data["total"],
            pending=data["pending"],
            completed=data["completed"],
            failed=data["failed"],
        )

    # ------------------------------------------------------------------
    # POST /api/v1/orchestrator/goals
    # ------------------------------------------------------------------

    @post(
        "/goals",
        summary="Add a new goal",
        guards=[require_role("owner", "co_owner")],
    )
    async def add_goal(
        self,
        data: GoalAddRequestSchema,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> GoalAddResponseSchema:
        """Create a new goal in the database."""
        result = await _svc.add_goal(
            db_session,
            title=data.title,
            priority=data.priority,
            category=data.category,
        )
        logger.info("orchestrator.api.goal_added", user=str(request.user.id))
        return GoalAddResponseSchema(**result)

    # ------------------------------------------------------------------
    # DELETE /api/v1/orchestrator/goals/{goal_id}
    # ------------------------------------------------------------------

    @delete(
        "/goals/{goal_id:str}",
        status_code=200,
        summary="Delete a goal",
        guards=[require_role("owner", "co_owner")],
    )
    async def delete_goal(
        self,
        goal_id: str,
        db_session: AsyncSession,
        request: Request[User, Token, Any],
    ) -> GoalDeleteResponseSchema:
        """Delete a goal by its human-readable ID (e.g. g_001)."""
        try:
            result = await _svc.delete_goal(db_session, goal_id)
        except KeyError as exc:
            raise NotFoundException(detail=str(exc)) from exc

        logger.info("orchestrator.api.goal_deleted", user=str(request.user.id), goal_id=goal_id)
        return GoalDeleteResponseSchema(**result)

    # ------------------------------------------------------------------
    # GET /api/v1/orchestrator/health
    # ------------------------------------------------------------------

    @get(
        "/health",
        summary="Health report (8 dimensions)",
    )
    async def health(self) -> HealthReportSchema:
        """Return the latest health report."""
        data = _svc.get_health()
        return HealthReportSchema(
            overall_grade=data["overall_grade"],
            score=data["score"],
            dimensions={
                k: HealthDimensionSchema(**v) for k, v in data["dimensions"].items()
            },
            problems=[HealthProblemSchema(**p) for p in data["problems"]],
        )

    # ------------------------------------------------------------------
    # GET /api/v1/orchestrator/milestones
    # ------------------------------------------------------------------

    @get(
        "/milestones",
        summary="Project milestones from vision.md",
    )
    async def milestones(self) -> list[PhaseSchema]:
        """Return phases with milestones."""
        phases = _svc.get_milestones()
        return [
            PhaseSchema(
                number=p["number"],
                title=p["title"],
                is_future=p.get("is_future", False),
                milestones=[MilestoneSchema(**m) for m in p.get("milestones", [])],
            )
            for p in phases
        ]

    # ------------------------------------------------------------------
    # GET /api/v1/orchestrator/logs
    # ------------------------------------------------------------------

    @get(
        "/logs",
        summary="Runner log tail",
    )
    async def logs(
        self,
        n: int = 20,
        date: str | None = None,
    ) -> LogResponseSchema:
        """Return the last *n* lines of the runner log."""
        data = _svc.get_logs(n=min(n, 200), date=date)
        return LogResponseSchema(
            lines=[LogLineSchema(**ln) for ln in data["lines"]],
            total=data["total"],
            log_file=data["log_file"],
        )
