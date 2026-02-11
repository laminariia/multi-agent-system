"""Orchestrator management routes.

Exposes the autonomous orchestrator runner's state to the dashboard.
Mounted at ``/api/v1/orchestrator``.
"""

from __future__ import annotations

from typing import Any

import structlog
from litestar import Controller, Request, get, post
from litestar.security.jwt import Token

from src.api.guards import require_role
from src.api.schemas import (
    GoalAddRequestSchema,
    GoalAddResponseSchema,
    GoalListResponseSchema,
    GoalSchema,
    HealthDimensionSchema,
    HealthProblemSchema,
    HealthReportSchema,
    LogLineSchema,
    LogResponseSchema,
    MilestoneSchema,
    OrchestratorStartRequestSchema,
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
    async def status(self) -> OrchestratorStatusSchema:
        """Return whether the runner is alive, with goal and health summaries."""
        data = _svc.get_status()
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
        data: OrchestratorStartRequestSchema,
        request: Request[User, Token, Any],
    ) -> OrchestratorStartResponseSchema:
        """Launch the runner as a detached process."""
        try:
            result = _svc.start_runner(
                total_hours=data.total_hours,
                session_minutes=data.session_minutes,
            )
        except (RuntimeError, FileNotFoundError) as exc:
            from litestar.exceptions import ClientException

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
            from litestar.exceptions import ClientException

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
        status: str | None = None,
    ) -> GoalListResponseSchema:
        """Return goals from ``goals.yaml``, optionally filtered by status."""
        data = _svc.list_goals(status_filter=status)
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
        request: Request[User, Token, Any],
    ) -> GoalAddResponseSchema:
        """Append a new goal to ``goals.yaml``."""
        result = _svc.add_goal(
            title=data.title,
            priority=data.priority,
            category=data.category,
        )
        logger.info("orchestrator.api.goal_added", user=str(request.user.id))
        return GoalAddResponseSchema(**result)

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
