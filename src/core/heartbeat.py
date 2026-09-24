"""Agent heartbeat monitoring system.

Agents call ``heartbeat.ping(agent_name, current_task)`` every 90 seconds.
A background monitor task checks for timeouts (>180 s) and performs up to 3
restart attempts per agent before raising a HITL alert.

Heartbeat data is persisted in both Valkey (fast reads) and PostgreSQL (audit
trail).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import structlog
from redis.asyncio import Redis as AsyncRedis

from src.core.exceptions import HeartbeatTimeoutError

logger = structlog.get_logger(__name__)

# Valkey key prefixes
_HEARTBEAT_KEY_PREFIX = "heartbeat:"
_HEARTBEAT_CHANNEL = "agent:heartbeat"


def _default_heartbeat_config() -> HeartbeatConfig:
    """Create a :class:`HeartbeatConfig` using values from application settings."""
    from src.core.config import get_settings

    s = get_settings()
    return HeartbeatConfig(
        interval_seconds=s.HEARTBEAT_INTERVAL_SECONDS,
        timeout_seconds=s.HEARTBEAT_TIMEOUT_SECONDS,
        max_restarts=s.HEARTBEAT_MAX_RESTARTS,
        monitor_poll_seconds=s.HEARTBEAT_MONITOR_POLL_SECONDS,
    )


@dataclass(frozen=True)
class HeartbeatConfig:
    """Tuning knobs for the heartbeat system."""

    interval_seconds: int = 90
    timeout_seconds: int = 180
    max_restarts: int = 3
    monitor_poll_seconds: int = 30


@dataclass
class AgentHealthInfo:
    """Snapshot of an agent's liveness state."""

    agent_name: str
    status: str  # "healthy" | "unhealthy" | "dead" | "unknown"
    last_heartbeat: float  # UNIX epoch
    last_task: str | None
    restart_count: int
    timeout_seconds: int


class HeartbeatMonitor:
    """Central liveness monitor for all agents.

    Args:
        valkey: Async Valkey (redis-py) client.
        db_pool: ``asyncpg.Pool`` for PostgreSQL persistence.
        config: Timing and retry parameters.
        restart_callback: Async function ``(agent_name) -> None`` invoked to
            restart a timed-out agent.
        hitl_callback: Async function ``(agent_name, message) -> None`` invoked
            when restart attempts are exhausted.
    """

    _MAX_TRACKED_AGENTS = 1000

    def __init__(
        self,
        valkey: AsyncRedis,
        db_pool: Any,
        config: HeartbeatConfig | None = None,
        restart_callback: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        hitl_callback: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.valkey = valkey
        self.db_pool = db_pool
        self.config = config or _default_heartbeat_config()
        self._restart_callback = restart_callback
        self._hitl_callback = hitl_callback

        # In-memory tracking (authoritative fast path)
        self._last_seen: dict[str, float] = {}
        self._last_task: dict[str, str | None] = {}
        self._restart_counts: dict[str, int] = {}
        self._monitor_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Agent-side API
    # ------------------------------------------------------------------

    async def ping(self, agent_name: str, current_task: str | None = None) -> None:
        """Record a heartbeat from an agent.

        Should be called every ``config.interval_seconds`` (default 90 s).
        """
        now = time.time()

        # Evict oldest entry when tracking limit is exceeded.
        if len(self._last_seen) >= self._MAX_TRACKED_AGENTS and agent_name not in self._last_seen:
            oldest = min(self._last_seen, key=self._last_seen.get)  # type: ignore[arg-type]
            self._last_seen.pop(oldest, None)
            self._last_task.pop(oldest, None)
            self._restart_counts.pop(oldest, None)

        self._last_seen[agent_name] = now
        self._last_task[agent_name] = current_task

        # Persist to Valkey (fast path)
        payload = json.dumps(
            {
                "agent_name": agent_name,
                "timestamp": now,
                "task": current_task,
            }
        )
        key = f"{_HEARTBEAT_KEY_PREFIX}{agent_name}"
        await self.valkey.set(key, payload, ex=self.config.timeout_seconds * 2)

        # Publish for any listeners (dashboard, other monitors)
        await self.valkey.publish(_HEARTBEAT_CHANNEL, payload)

        # Persist to PostgreSQL (audit trail)
        if self.db_pool is not None:
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO agent_heartbeats (agent_name, current_task, heartbeat_at)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (agent_name)
                        DO UPDATE SET current_task = $2, heartbeat_at = NOW()
                        """,
                        agent_name,
                        current_task,
                    )
            except Exception:
                logger.warning("heartbeat_db_persist_failed", agent=agent_name, exc_info=True)

        # Reset restart count on successful heartbeat
        self._restart_counts[agent_name] = 0

        logger.debug("heartbeat_received", agent=agent_name, task=current_task)

    # ------------------------------------------------------------------
    # Monitoring lifecycle
    # ------------------------------------------------------------------

    async def start_monitoring(self) -> None:
        """Start the background monitoring loop."""
        if self._monitor_task is not None and not self._monitor_task.done():
            logger.warning("heartbeat_monitor_already_running")
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="heartbeat-monitor")
        logger.info("heartbeat_monitor_started", poll_interval=self.config.monitor_poll_seconds)

    async def stop_monitoring(self) -> None:
        """Cancel the monitoring background task."""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
            logger.info("heartbeat_monitor_stopped")

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def get_agent_status(self, agent_name: str) -> dict[str, Any]:
        """Return a status dict for a single agent."""
        info = self._build_health_info(agent_name)
        return {
            "agent_name": info.agent_name,
            "status": info.status,
            "last_heartbeat": info.last_heartbeat,
            "last_task": info.last_task,
            "restart_count": info.restart_count,
            "timeout_seconds": info.timeout_seconds,
        }

    async def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        """Return health info for every known agent."""
        return {name: await self.get_agent_status(name) for name in self._last_seen}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_health_info(self, agent_name: str) -> AgentHealthInfo:
        last_ts = self._last_seen.get(agent_name, 0.0)
        elapsed = time.time() - last_ts if last_ts > 0 else float("inf")
        restart_count = self._restart_counts.get(agent_name, 0)

        if last_ts == 0.0:
            status = "unknown"
        elif elapsed <= self.config.timeout_seconds:
            status = "healthy"
        elif restart_count >= self.config.max_restarts:
            status = "dead"
        else:
            status = "unhealthy"

        return AgentHealthInfo(
            agent_name=agent_name,
            status=status,
            last_heartbeat=last_ts,
            last_task=self._last_task.get(agent_name),
            restart_count=restart_count,
            timeout_seconds=self.config.timeout_seconds,
        )

    async def _monitor_loop(self) -> None:
        """Periodically scan for timed-out agents."""
        while True:
            try:
                await asyncio.sleep(self.config.monitor_poll_seconds)
                await self._check_timeouts()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("heartbeat_monitor_error", exc_info=True)

    async def _check_timeouts(self) -> None:
        now = time.time()
        for agent_name, last_ts in list(self._last_seen.items()):
            elapsed = now - last_ts
            if elapsed <= self.config.timeout_seconds:
                continue

            await self._handle_timeout(agent_name, elapsed)

    async def _handle_timeout(self, agent_name: str, elapsed: float) -> None:
        self._restart_counts[agent_name] = self._restart_counts.get(agent_name, 0) + 1
        count = self._restart_counts[agent_name]

        logger.warning(
            "agent_heartbeat_timeout",
            agent=agent_name,
            elapsed_s=round(elapsed, 1),
            restart_attempt=count,
            max_restarts=self.config.max_restarts,
        )

        if count > self.config.max_restarts:
            # Free memory for dead agents.
            self._last_seen.pop(agent_name, None)
            self._last_task.pop(agent_name, None)

            msg = (
                f"Agent '{agent_name}' exceeded max restarts ({self.config.max_restarts}). "
                f"Last heartbeat {round(elapsed, 0)}s ago."
            )
            logger.error("agent_dead_hitl_required", agent=agent_name, message=msg)

            if self._hitl_callback is not None:
                await self._hitl_callback(agent_name, msg)

            raise HeartbeatTimeoutError(
                msg,
                agent_name=agent_name,
                last_heartbeat_ts=self._last_seen.get(agent_name, 0.0),
                timeout_seconds=self.config.timeout_seconds,
                restart_count=count,
            )

        # Attempt restart
        if self._restart_callback is not None:
            try:
                await self._restart_callback(agent_name)
                logger.info("agent_restart_initiated", agent=agent_name, attempt=count)
            except Exception:
                logger.error("agent_restart_failed", agent=agent_name, attempt=count, exc_info=True)
        else:
            logger.warning("no_restart_callback_configured", agent=agent_name)

        # Log restart event to DB
        if self.db_pool is not None:
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO agent_restart_log (agent_name, restart_attempt, reason)
                        VALUES ($1, $2, $3)
                        """,
                        agent_name,
                        count,
                        f"Heartbeat timeout ({round(elapsed, 1)}s)",
                    )
            except Exception:
                logger.warning("restart_log_db_failed", agent=agent_name, exc_info=True)
