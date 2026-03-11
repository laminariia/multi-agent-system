"""Crash recovery — resume stale pipeline threads on startup.

On application startup, finds threads stuck in 'active' status without
recent heartbeat and attempts to resume them from their last LangGraph
checkpoint. HITL-pending threads are never auto-resumed.

Spec: docs/Full_work/specs/infrastructure-spec.md §3 (Crash Recovery)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from src.core.models import HITLQueue, LanggraphCheckpoint

logger = structlog.get_logger(__name__)

MAX_CRASH_RETRIES = 3
STALE_THRESHOLD_MINUTES = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StaleThread:
    """A pipeline thread that appears to have crashed mid-execution."""

    thread_id: str
    status: str
    current_agent: str | None
    state_data: dict[str, Any]
    created_at: datetime


@dataclass
class CrashRecoveryResult:
    """Outcome of a single crash recovery attempt."""

    action: str  # "resumed", "wait_for_hitl", "escalated", "no_checkpoint", "error"
    thread_id: str
    final_status: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Find stale threads
# ---------------------------------------------------------------------------


async def find_stale_threads(session: Any) -> list[StaleThread]:
    """Find pipeline threads stuck in 'active' without recent checkpoint activity.

    Returns threads where:
    - status = 'active'
    - requires_hitl = False (HITL-pending threads are not auto-resumed)
    - created_at < now() - STALE_THRESHOLD_MINUTES
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    stmt = select(LanggraphCheckpoint).where(
        LanggraphCheckpoint.status == "active",
        LanggraphCheckpoint.requires_hitl.is_(False),
        LanggraphCheckpoint.created_at < cutoff,
    )

    result = await session.execute(stmt)
    rows = result.scalars().all()

    return [
        StaleThread(
            thread_id=row.thread_id,
            status=row.status or "active",
            current_agent=row.current_agent,
            state_data=row.state_data or {},
            created_at=row.created_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Entry node detection
# ---------------------------------------------------------------------------


def _determine_entry_node(state: dict[str, Any]) -> str:
    """Determine the graph node to resume from based on checkpoint state.

    Strategy: restart at the current agent (agents are idempotent by design).
    """
    current = state.get("current_agent")
    if current:
        return f"{current}_node"

    next_agent = state.get("next_agent")
    if next_agent:
        return f"{next_agent}_node"

    # Last resort: start from the beginning
    return "scout_node"


# ---------------------------------------------------------------------------
# HITL alert for unrecoverable crash
# ---------------------------------------------------------------------------


async def create_crash_hitl(
    thread_id: str,
    state: dict[str, Any],
    session: Any,
) -> None:
    """Create an urgent HITL alert for a crash that exceeded max retries."""
    current_agent = state.get("current_agent", "unknown")
    retry_count = state.get("_crash_retry_count", 0)

    hitl_entry = HITLQueue(
        type="crash_recovery",
        priority="urgent",
        status="pending",
        title=f"Agent crash: {current_agent} (thread {thread_id[:8]}...)",
        description=(f"Agent {current_agent} crashed after {retry_count} retries. Manual intervention required."),
        payload={
            "thread_id": thread_id,
            "current_agent": current_agent,
            "status": state.get("status"),
            "retry_count": retry_count,
            "last_error": state.get("_last_error"),
            "artifacts": list(state.get("artifacts", {}).keys()),
        },
        available_actions=["retry", "skip_agent", "abort_pipeline"],
    )
    session.add(hitl_entry)
    await session.flush()

    logger.warning(
        "crash_recovery.hitl_created",
        thread_id=thread_id,
        current_agent=current_agent,
        retry_count=retry_count,
    )


# ---------------------------------------------------------------------------
# Resume a single thread
# ---------------------------------------------------------------------------


async def resume_from_crash(
    thread_id: str,
    session: Any,
) -> CrashRecoveryResult:
    """Attempt to resume a crashed pipeline thread from its last checkpoint.

    Returns a CrashRecoveryResult describing what happened.
    """
    # 1. Load latest checkpoint
    stmt = (
        select(LanggraphCheckpoint)
        .where(LanggraphCheckpoint.thread_id == thread_id)
        .order_by(LanggraphCheckpoint.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    checkpoint = result.scalars().first()

    if checkpoint is None:
        logger.warning("crash_recovery.no_checkpoint", thread_id=thread_id)
        return CrashRecoveryResult(action="no_checkpoint", thread_id=thread_id)

    state = dict(checkpoint.state_data) if checkpoint.state_data else {}
    status = state.get("status", checkpoint.status or "unknown")

    # 2. HITL pending — do not auto-resume
    if checkpoint.requires_hitl or state.get("requires_hitl"):
        logger.info("crash_recovery.hitl_pending", thread_id=thread_id)
        return CrashRecoveryResult(action="wait_for_hitl", thread_id=thread_id)

    # 3. Failed with max retries — escalate
    retry_count = state.get("_crash_retry_count", 0)
    if status == "failed" and retry_count >= MAX_CRASH_RETRIES:
        logger.warning(
            "crash_recovery.max_retries_exceeded",
            thread_id=thread_id,
            retry_count=retry_count,
        )
        await create_crash_hitl(thread_id, state, session)
        return CrashRecoveryResult(action="escalated", thread_id=thread_id)

    # 4. Prepare state for resume
    if status == "failed":
        state["status"] = "active"
        state["_crash_retry_count"] = retry_count + 1
    elif status == "active":
        state["_crash_retry_count"] = retry_count + 1

    # 5. Build graph and invoke
    try:
        from src.core.graph import build_full_pipeline_graph  # noqa: PLC0415

        graph = build_full_pipeline_graph()
        final_state = await graph.ainvoke(state)

        logger.info(
            "crash_recovery.resumed",
            thread_id=thread_id,
            final_status=final_state.get("status"),
        )
        return CrashRecoveryResult(
            action="resumed",
            thread_id=thread_id,
            final_status=final_state.get("status"),
        )
    except Exception as exc:
        logger.error(
            "crash_recovery.resume_failed",
            thread_id=thread_id,
            error=str(exc),
            exc_info=True,
        )
        return CrashRecoveryResult(
            action="error",
            thread_id=thread_id,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Startup recovery orchestration
# ---------------------------------------------------------------------------


async def startup_crash_recovery(session: Any) -> dict[str, int]:
    """Find and attempt to resume all stale pipeline threads.

    Called from the application lifespan on startup. Non-blocking — errors
    are logged but never propagated.

    Returns a summary dict: {found, resumed, escalated, skipped, errors}.
    """
    summary: dict[str, int] = {
        "found": 0,
        "resumed": 0,
        "escalated": 0,
        "skipped": 0,
        "errors": 0,
    }

    threads = await find_stale_threads(session)
    summary["found"] = len(threads)

    if not threads:
        logger.info("crash_recovery.startup_no_stale_threads")
        return summary

    logger.info("crash_recovery.startup_found_stale", count=len(threads))

    for thread in threads:
        try:
            result = await resume_from_crash(thread.thread_id, session)

            if result.action == "resumed":
                summary["resumed"] += 1
            elif result.action == "escalated":
                summary["escalated"] += 1
            elif result.action in ("wait_for_hitl", "no_checkpoint"):
                summary["skipped"] += 1
            else:
                summary["errors"] += 1

            logger.info(
                "crash_recovery.thread_result",
                thread_id=thread.thread_id,
                action=result.action,
            )
        except Exception:
            summary["errors"] += 1
            logger.error(
                "crash_recovery.thread_failed",
                thread_id=thread.thread_id,
                exc_info=True,
            )

    logger.info("crash_recovery.startup_complete", **summary)
    return summary
