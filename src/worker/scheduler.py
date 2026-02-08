"""APScheduler-based background scheduler for periodic MAS tasks.

Runs:
- Scout cycle every 5 minutes (job discovery).
- Metrics collection every 60 seconds.
- Heartbeat cleanup every 10 minutes.
"""
from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger(__name__)


class WorkerScheduler:
    """Manages periodic background tasks via APScheduler.

    Args:
        scout_interval_minutes: How often to run the Scout cycle.
        metrics_interval_seconds: How often to collect metrics.
        heartbeat_cleanup_minutes: How often to clean up stale heartbeats.
    """

    def __init__(
        self,
        *,
        scout_interval_minutes: int = 5,
        metrics_interval_seconds: int = 60,
        heartbeat_cleanup_minutes: int = 10,
    ) -> None:
        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
        )
        self._scout_interval = scout_interval_minutes
        self._metrics_interval = metrics_interval_seconds
        self._heartbeat_interval = heartbeat_cleanup_minutes

    async def start(self) -> None:
        """Register all periodic jobs and start the scheduler."""
        self._scheduler.add_job(
            _run_scout_cycle,
            trigger=IntervalTrigger(minutes=self._scout_interval),
            id="scout_cycle",
            name="Scout job discovery",
            replace_existing=True,
        )

        self._scheduler.add_job(
            _collect_metrics,
            trigger=IntervalTrigger(seconds=self._metrics_interval),
            id="metrics_collection",
            name="Metrics collection",
            replace_existing=True,
        )

        self._scheduler.add_job(
            _cleanup_heartbeats,
            trigger=IntervalTrigger(minutes=self._heartbeat_interval),
            id="heartbeat_cleanup",
            name="Heartbeat cleanup",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info(
            "scheduler_started",
            scout_interval_min=self._scout_interval,
            metrics_interval_sec=self._metrics_interval,
            heartbeat_interval_min=self._heartbeat_interval,
        )

    async def stop(self) -> None:
        """Shut down the scheduler gracefully."""
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")

    @property
    def running(self) -> bool:
        return self._scheduler.running


# ---------------------------------------------------------------------------
# Scheduled job functions
# ---------------------------------------------------------------------------


async def _run_scout_cycle() -> None:
    """Execute a single Scout Agent cycle with distributed lock."""
    from src.core.database import get_valkey

    valkey = get_valkey()

    # Distributed lock: prevent overlapping scout runs
    lock_acquired = await valkey.set("scout:lock", "1", nx=True, ex=300)
    if not lock_acquired:
        logger.debug("scout_cycle_skipped", reason="lock held by another instance")
        return

    try:
        logger.info("scout_cycle_start")
        await _execute_scout()
        logger.info("scout_cycle_complete")
    except Exception:
        logger.exception("scout_cycle_failed")
    finally:
        try:
            await valkey.delete("scout:lock")
        except Exception:
            logger.warning("scout_lock_release_failed", exc_info=True)


async def _execute_scout() -> None:
    """Run the Scout Agent node with a fresh state."""
    from src.core.state import ProjectContext, create_initial_state

    state = create_initial_state(
        project=ProjectContext(
            project_id="scout-cycle",
            job_id="",
            platform="all",
            client={},
            requirements="Automated scout cycle",
            budget=0,
            deadline=None,
        ),
        first_agent="scout",
    )

    from src.agents.scout import scout_node

    await scout_node(state)


async def _collect_metrics() -> None:
    """Collect system-level metrics and update Prometheus gauges."""
    try:
        from sqlalchemy import func, select

        from src.core.database import get_db_session
        from src.monitoring.metrics import get_metrics

        metrics = get_metrics()

        async with get_db_session() as session:
            from src.core.models import HITLQueue

            # HITL queue sizes
            for hitl_type in ("bid_approval", "final_review"):
                stmt = select(func.count()).where(
                    HITLQueue.status == "pending",
                    HITLQueue.type == hitl_type,
                )
                result = await session.execute(stmt)
                count = result.scalar() or 0
                metrics.hitl_queue_size.labels(type=hitl_type).set(count)

    except Exception:
        logger.debug("metrics_collection_failed", exc_info=True)


async def _cleanup_heartbeats() -> None:
    """Remove stale heartbeat entries from Valkey."""
    try:
        from src.core.database import get_valkey

        valkey = get_valkey()

        # Scan for heartbeat keys older than timeout
        deleted = 0
        async for key in valkey.scan_iter("heartbeat:*"):
            ttl = await valkey.ttl(key)
            if ttl == -1:  # No expiry set — clean up
                await valkey.delete(key)
                deleted += 1

        if deleted:
            logger.info("heartbeat_cleanup", deleted=deleted)

    except Exception:
        logger.debug("heartbeat_cleanup_failed", exc_info=True)
