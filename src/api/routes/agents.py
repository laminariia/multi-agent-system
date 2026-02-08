"""Agent management routes.

Provides status monitoring, log retrieval, and control endpoints
(restart / pause / resume) for all agents.  Mounted at ``/api/v1/agents``.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
import structlog
from litestar import Controller, Request, get, post
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.schemas import (
    AgentActionResponseSchema,
    AgentLogListSchema,
    AgentLogSchema,
    AgentStatusListSchema,
    AgentStatusSchema,
)
from src.core.models import AgentHeartbeat, AgentLog, User

logger = structlog.get_logger(__name__)

# Heartbeat staleness threshold: an agent is "dead" if it has not reported
# within this window.
_HEARTBEAT_DEAD_THRESHOLD = timedelta(seconds=180)
_HEARTBEAT_WARN_THRESHOLD = timedelta(seconds=90)


def _compute_system_health(agents: list[AgentStatusSchema]) -> str:
    """Derive an overall system-health label from individual agent statuses."""
    if not agents:
        return "critical"
    dead_count = sum(1 for a in agents if a.status == "dead")
    error_count = sum(1 for a in agents if a.status == "error")
    total = len(agents)
    if dead_count == 0 and error_count == 0:
        return "healthy"
    if (dead_count + error_count) < total:
        return "degraded"
    return "critical"


class AgentController(Controller):
    """Monitoring and control surface for the agent fleet."""

    path = "/api/v1/agents"
    tags = ["agents"]

    # -----------------------------------------------------------------
    # GET /api/v1/agents/status
    # -----------------------------------------------------------------

    @get(
        "/status",
        summary="All agents status overview",
    )
    async def status(
        self,
        db_session: AsyncSession,
    ) -> AgentStatusListSchema:
        """Return the current status of every registered agent.

        Agent rows come from the ``agent_heartbeats`` table.  The status
        is dynamically adjusted: if ``last_heartbeat`` exceeds the dead
        threshold (180 s), the agent is reported as ``dead`` regardless of
        its stored status.
        """
        now = datetime.now(UTC)
        stmt = select(AgentHeartbeat).order_by(AgentHeartbeat.agent_name)
        result = await db_session.execute(stmt)
        rows = result.scalars().all()

        agents: list[AgentStatusSchema] = []
        for row in rows:
            effective_status = row.status
            if row.last_heartbeat is not None:
                age = now - row.last_heartbeat
                if age > _HEARTBEAT_DEAD_THRESHOLD:
                    effective_status = "dead"

            agents.append(
                AgentStatusSchema(
                    name=row.agent_name,
                    display_name=getattr(row, "display_name", None) or row.agent_name.title() + " Agent",
                    pipeline=getattr(row, "pipeline", None),
                    status=effective_status,
                    last_heartbeat=row.last_heartbeat,
                    current_task=row.current_task,
                    restart_count=row.restart_count,
                    error_message=row.error_message,
                )
            )

        system_health = _compute_system_health(agents)

        return AgentStatusListSchema(
            system_health=system_health,
            last_check=now,
            agents=agents,
        )

    # -----------------------------------------------------------------
    # GET /api/v1/agents/{name}/logs
    # -----------------------------------------------------------------

    @get(
        "/{name:str}/logs",
        summary="Agent log history",
    )
    async def logs(
        self,
        name: str,
        db_session: AsyncSession,
        limit: int = Parameter(default=50, ge=1, le=500, description="Max log entries to return"),
        level: str | None = Parameter(default=None, description="Filter by level: info | warning | error"),
        since: datetime | None = Parameter(default=None, description="Only logs after this ISO timestamp"),  # noqa: B008
    ) -> AgentLogListSchema:
        """Return paginated, filterable log entries for a specific agent.

        Raises:
            NotFoundException: If the agent name is not found in heartbeats.
        """
        # Verify agent exists
        hb_stmt = select(AgentHeartbeat).where(AgentHeartbeat.agent_name == name)
        hb_result = await db_session.execute(hb_stmt)
        if hb_result.scalar_one_or_none() is None:
            raise NotFoundException(detail=f"Agent '{name}' not found")

        stmt = select(AgentLog).where(AgentLog.agent_name == name)

        if level is not None:
            stmt = stmt.where(AgentLog.event_type == level)  # legacy column
            # Also try the level-specific field when available
            stmt = select(AgentLog).where(AgentLog.agent_name == name)
            # Filter by event_type matching common levels or by checking details
            level_lower = level.lower()
            if level_lower in ("info", "warning", "error"):
                stmt = stmt.where(
                    AgentLog.event_type.in_([level_lower, level_lower.upper()])
                    | AgentLog.message.ilike(f"%{level_lower}%")
                )

        if since is not None:
            stmt = stmt.where(AgentLog.created_at >= since)

        # Total count for this filter set
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db_session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(AgentLog.created_at.desc()).limit(limit)
        result = await db_session.execute(stmt)
        rows = result.scalars().all()

        log_items = [
            AgentLogSchema(
                id=row.id,
                timestamp=row.created_at,
                level=_infer_level(row),
                event_type=row.event_type,
                message=row.message,
                details=row.details,
            )
            for row in rows
        ]

        return AgentLogListSchema(agent=name, logs=log_items, total=total)

    # -----------------------------------------------------------------
    # POST /api/v1/agents/{name}/restart
    # -----------------------------------------------------------------

    @post(
        "/{name:str}/restart",
        summary="Force restart an agent",
        guards=[require_role("owner")],
    )
    async def restart(
        self,
        name: str,
        db_session: AsyncSession,
        valkey: aioredis.Redis,
        request: Request[User, Token, Any],
    ) -> AgentActionResponseSchema:
        """Publish a restart command via Valkey pub/sub and increment the restart counter."""
        heartbeat = await _get_heartbeat_or_404(name, db_session)

        heartbeat.restart_count += 1
        heartbeat.status = "idle"
        heartbeat.error_message = None
        heartbeat.current_task = None
        await db_session.flush()

        # Publish restart command so the agent supervisor can pick it up
        await valkey.publish(f"agent:command:{name}", "restart")

        logger.info("agent.restart", agent=name, requested_by=str(request.user.id))

        return AgentActionResponseSchema(
            agent=name,
            action="restart",
            status="ok",
            message=f"Agent {name} restart initiated",
        )

    # -----------------------------------------------------------------
    # POST /api/v1/agents/{name}/pause
    # -----------------------------------------------------------------

    @post(
        "/{name:str}/pause",
        summary="Pause agent processing",
        guards=[require_role("owner")],
    )
    async def pause(
        self,
        name: str,
        db_session: AsyncSession,
        valkey: aioredis.Redis,
        request: Request[User, Token, Any],
    ) -> AgentActionResponseSchema:
        """Set the agent status to ``paused`` and publish a pause command."""
        heartbeat = await _get_heartbeat_or_404(name, db_session)

        heartbeat.status = "paused"
        heartbeat.current_task = None
        await db_session.flush()

        await valkey.publish(f"agent:command:{name}", "pause")

        logger.info("agent.pause", agent=name, requested_by=str(request.user.id))

        return AgentActionResponseSchema(
            agent=name,
            action="pause",
            status="ok",
            message=f"Agent {name} paused",
        )

    # -----------------------------------------------------------------
    # POST /api/v1/agents/{name}/resume
    # -----------------------------------------------------------------

    @post(
        "/{name:str}/resume",
        summary="Resume agent processing",
        guards=[require_role("owner")],
    )
    async def resume(
        self,
        name: str,
        db_session: AsyncSession,
        valkey: aioredis.Redis,
        request: Request[User, Token, Any],
    ) -> AgentActionResponseSchema:
        """Set the agent status back to ``idle`` and publish a resume command."""
        heartbeat = await _get_heartbeat_or_404(name, db_session)

        heartbeat.status = "idle"
        await db_session.flush()

        await valkey.publish(f"agent:command:{name}", "resume")

        logger.info("agent.resume", agent=name, requested_by=str(request.user.id))

        return AgentActionResponseSchema(
            agent=name,
            action="resume",
            status="ok",
            message=f"Agent {name} resumed",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_heartbeat_or_404(name: str, db_session: AsyncSession) -> AgentHeartbeat:
    """Look up an ``AgentHeartbeat`` row or raise 404."""
    stmt = select(AgentHeartbeat).where(AgentHeartbeat.agent_name == name)
    result = await db_session.execute(stmt)
    heartbeat = result.scalar_one_or_none()
    if heartbeat is None:
        raise NotFoundException(detail=f"Agent '{name}' not found")
    return heartbeat


def _infer_level(log_row: AgentLog) -> str:
    """Best-effort inference of the log level from the event_type or message."""
    et = (log_row.event_type or "").lower()
    if et in ("error", "warning", "info"):
        return et
    if "error" in et or "fail" in et:
        return "error"
    if "warn" in et:
        return "warning"
    return "info"
