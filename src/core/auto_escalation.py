"""Automatic HITL escalation for critical system events.

Creates HITL alerts when P0/P1 edge cases are detected:
- Account ban on any platform
- LLM API consecutive failures
- Capacity overflow (too many active projects)
- Agent crash after max retries
- Email bounce rate exceeds threshold

All escalation functions are designed to never crash the calling agent --
every public function wraps its logic in try/except and logs failures
instead of propagating them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select

from src.core.database import get_db_session
from src.core.models import EmailCampaign, HITLQueue

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# GC-safe task references (prevent fire-and-forget tasks from being collected)
# ---------------------------------------------------------------------------
_background_tasks: set[asyncio.Task[Any]] = set()

# ---------------------------------------------------------------------------
# LLM failure tracking (module-level, in-memory)
# ---------------------------------------------------------------------------
_consecutive_failures: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Duplicate prevention helper
# ---------------------------------------------------------------------------


async def _has_pending_escalation(
    session: Any,
    escalation_type: str,
    dedup_key: str,
    dedup_value: str,
) -> bool:
    """Check if a pending HITL alert already exists for this event.

    Prevents duplicate alerts by checking for an existing pending entry
    whose payload contains the same dedup_key=dedup_value and matching type.
    """
    # Use JSON containment to check payload field
    stmt = (
        select(func.count())
        .select_from(HITLQueue)
        .where(
            HITLQueue.type == escalation_type,
            HITLQueue.status == "pending",
            HITLQueue.payload.contains({dedup_key: dedup_value}),
        )
    )
    result = await session.execute(stmt)
    count = result.scalar() or 0
    return count > 0


# ---------------------------------------------------------------------------
# Platform ban escalation
# ---------------------------------------------------------------------------


async def escalate_platform_ban(
    platform: str,
    account_id: str | None = None,
    error_details: str = "",
) -> str | None:
    """Create urgent HITL alert when a platform account is banned.

    Returns HITL entry ID or None on failure.
    """
    try:
        async with get_db_session() as session:
            # Duplicate prevention: one pending alert per platform
            if await _has_pending_escalation(session, "alert", "platform", platform):
                logger.debug(
                    "escalation_duplicate_skipped",
                    type="platform_ban",
                    platform=platform,
                )
                return None

            hitl_id = uuid.uuid4()
            hitl = HITLQueue(
                id=hitl_id,
                type="alert",
                priority="urgent",
                title=f"Platform ban detected: {platform}",
                description=(
                    f"Account on '{platform}' has been banned or suspended. "
                    f"Immediate action required to prevent data loss and resume operations."
                ),
                payload={
                    "platform": platform,
                    "account_id": account_id,
                    "error_details": error_details[:500] if error_details else "",
                    "detected_at": datetime.now(UTC).isoformat(),
                    "escalation_source": "auto_escalation",
                },
                available_actions=["acknowledge", "switch_account", "pause_platform"],
                status="pending",
            )
            session.add(hitl)

        logger.warning(
            "escalation_created",
            type="platform_ban",
            platform=platform,
            hitl_id=str(hitl_id),
        )
        return str(hitl_id)

    except Exception:
        logger.exception("escalation_failed", type="platform_ban", platform=platform)
        return None


# ---------------------------------------------------------------------------
# LLM failure escalation
# ---------------------------------------------------------------------------


async def escalate_llm_failure(
    agent_name: str,
    consecutive_failures: int,
    last_error: str = "",
) -> str | None:
    """Create HITL alert when LLM API fails 3+ consecutive times.

    Returns HITL entry ID or None if threshold not met or on failure.
    """
    if consecutive_failures < 3:
        return None

    try:
        async with get_db_session() as session:
            # Duplicate prevention: one pending alert per agent
            if await _has_pending_escalation(session, "alert", "agent_name", agent_name):
                logger.debug(
                    "escalation_duplicate_skipped",
                    type="llm_failure",
                    agent_name=agent_name,
                )
                return None

            hitl_id = uuid.uuid4()
            hitl = HITLQueue(
                id=hitl_id,
                type="alert",
                priority="high",
                title=f"LLM failures: {agent_name} ({consecutive_failures} consecutive)",
                description=(
                    f"Agent '{agent_name}' has experienced {consecutive_failures} consecutive "
                    f"LLM API failures. The agent may be unable to function until the "
                    f"issue is resolved."
                ),
                payload={
                    "agent_name": agent_name,
                    "consecutive_failures": consecutive_failures,
                    "last_error": last_error[:500] if last_error else "",
                    "detected_at": datetime.now(UTC).isoformat(),
                    "escalation_source": "auto_escalation",
                },
                available_actions=["acknowledge", "switch_model", "pause_agent"],
                status="pending",
            )
            session.add(hitl)

        logger.warning(
            "escalation_created",
            type="llm_failure",
            agent_name=agent_name,
            consecutive_failures=consecutive_failures,
            hitl_id=str(hitl_id),
        )
        return str(hitl_id)

    except Exception:
        logger.exception("escalation_failed", type="llm_failure", agent_name=agent_name)
        return None


# ---------------------------------------------------------------------------
# Capacity overflow escalation
# ---------------------------------------------------------------------------


async def escalate_capacity_overflow(
    active_project_count: int,
    threshold: int = 10,
) -> str | None:
    """Create HITL alert when active projects exceed threshold.

    Returns HITL entry ID or None if threshold not exceeded or on failure.
    """
    if active_project_count <= threshold:
        return None

    try:
        async with get_db_session() as session:
            # Duplicate prevention: only one pending capacity warning at a time
            if await _has_pending_escalation(
                session,
                "capacity_warning",
                "escalation_source",
                "auto_escalation",
            ):
                logger.debug(
                    "escalation_duplicate_skipped",
                    type="capacity_overflow",
                )
                return None

            hitl_id = uuid.uuid4()
            hitl = HITLQueue(
                id=hitl_id,
                type="capacity_warning",
                priority="high",
                title=f"Capacity overflow: {active_project_count} active projects (threshold: {threshold})",
                description=(
                    f"The system has {active_project_count} active projects, exceeding the "
                    f"configured threshold of {threshold}. New job scouting should be paused "
                    f"until capacity is freed."
                ),
                payload={
                    "active_project_count": active_project_count,
                    "threshold": threshold,
                    "detected_at": datetime.now(UTC).isoformat(),
                    "escalation_source": "auto_escalation",
                },
                available_actions=["acknowledge", "pause_scout", "increase_limit"],
                status="pending",
            )
            session.add(hitl)

        logger.warning(
            "escalation_created",
            type="capacity_overflow",
            active_count=active_project_count,
            threshold=threshold,
            hitl_id=str(hitl_id),
        )
        return str(hitl_id)

    except Exception:
        logger.exception("escalation_failed", type="capacity_overflow")
        return None


# ---------------------------------------------------------------------------
# Agent crash escalation
# ---------------------------------------------------------------------------


async def escalate_agent_crash(
    agent_name: str,
    crash_count: int,
    thread_id: str | None = None,
    last_error: str = "",
) -> str | None:
    """Create HITL alert when agent crashes 3+ times.

    Returns HITL entry ID or None if threshold not met or on failure.
    """
    if crash_count < 3:
        return None

    try:
        async with get_db_session() as session:
            # Duplicate prevention: one pending agent_failure per agent+thread
            dedup_value = f"{agent_name}:{thread_id or 'global'}"
            if await _has_pending_escalation(session, "agent_failure", "dedup_key", dedup_value):
                logger.debug(
                    "escalation_duplicate_skipped",
                    type="agent_crash",
                    agent_name=agent_name,
                )
                return None

            hitl_id = uuid.uuid4()
            hitl = HITLQueue(
                id=hitl_id,
                type="agent_failure",
                priority="urgent",
                title=f"Agent crash: {agent_name} ({crash_count} crashes)",
                description=(
                    f"Agent '{agent_name}' has crashed {crash_count} times. "
                    f"Manual intervention is required to diagnose and resolve the issue."
                ),
                payload={
                    "agent_name": agent_name,
                    "crash_count": crash_count,
                    "thread_id": thread_id,
                    "last_error": last_error[:500] if last_error else "",
                    "detected_at": datetime.now(UTC).isoformat(),
                    "escalation_source": "auto_escalation",
                    "dedup_key": dedup_value,
                },
                available_actions=["acknowledge", "restart", "skip_agent", "manual_fix"],
                status="pending",
            )
            session.add(hitl)

        logger.warning(
            "escalation_created",
            type="agent_crash",
            agent_name=agent_name,
            crash_count=crash_count,
            hitl_id=str(hitl_id),
        )
        return str(hitl_id)

    except Exception:
        logger.exception("escalation_failed", type="agent_crash", agent_name=agent_name)
        return None


# ---------------------------------------------------------------------------
# Bounce rate escalation
# ---------------------------------------------------------------------------


async def escalate_bounce_rate(
    campaign_id: str,
    bounce_rate: float,
    threshold: float = 0.10,
) -> str | None:
    """Create HITL alert and pause campaign when bounce rate > threshold.

    Returns HITL entry ID or None if threshold not exceeded or on failure.
    """
    if bounce_rate <= threshold:
        return None

    try:
        async with get_db_session() as session:
            # Duplicate prevention: one pending alert per campaign
            if await _has_pending_escalation(session, "alert", "campaign_id", campaign_id):
                logger.debug(
                    "escalation_duplicate_skipped",
                    type="bounce_rate",
                    campaign_id=campaign_id,
                )
                return None

            hitl_id = uuid.uuid4()
            hitl = HITLQueue(
                id=hitl_id,
                type="alert",
                priority="high",
                title=f"High bounce rate: {bounce_rate:.1%} (campaign {campaign_id[:8]}...)",
                description=(
                    f"Email campaign {campaign_id} has a bounce rate of {bounce_rate:.1%}, "
                    f"exceeding the {threshold:.0%} threshold. The campaign has been "
                    f"automatically paused to protect sender reputation."
                ),
                payload={
                    "campaign_id": campaign_id,
                    "bounce_rate": round(bounce_rate, 4),
                    "threshold": threshold,
                    "detected_at": datetime.now(UTC).isoformat(),
                    "escalation_source": "auto_escalation",
                },
                available_actions=["acknowledge", "resume_campaign", "stop_campaign"],
                status="pending",
            )
            session.add(hitl)

            # Also pause the campaign
            try:
                campaign_uuid = uuid.UUID(campaign_id)
                stmt = select(EmailCampaign).where(EmailCampaign.id == campaign_uuid)
                result = await session.execute(stmt)
                campaign = result.scalars().first()
                if campaign and campaign.status != "paused":
                    campaign.status = "paused"
                    logger.info(
                        "campaign_auto_paused",
                        campaign_id=campaign_id,
                        bounce_rate=bounce_rate,
                    )
            except (ValueError, TypeError):
                # Invalid UUID format -- skip campaign pause but still create alert
                logger.warning(
                    "campaign_pause_skipped_invalid_id",
                    campaign_id=campaign_id,
                )

        logger.warning(
            "escalation_created",
            type="bounce_rate",
            campaign_id=campaign_id,
            bounce_rate=round(bounce_rate, 4),
            hitl_id=str(hitl_id),
        )
        return str(hitl_id)

    except Exception:
        logger.exception("escalation_failed", type="bounce_rate", campaign_id=campaign_id)
        return None


# ---------------------------------------------------------------------------
# LLM failure tracking helper
# ---------------------------------------------------------------------------


async def track_llm_result(agent_name: str, success: bool, error: str = "") -> None:
    """Track consecutive LLM failures and escalate at threshold.

    Call this after every LLM call to maintain the failure counter.
    On success, resets the counter. On failure, increments and escalates
    at 3+ consecutive failures.
    """
    if success:
        _consecutive_failures[agent_name] = 0
        return

    _consecutive_failures[agent_name] = _consecutive_failures.get(agent_name, 0) + 1
    count = _consecutive_failures[agent_name]

    if count >= 3:
        # Fire-and-forget escalation to avoid blocking the caller
        task = asyncio.create_task(
            escalate_llm_failure(agent_name, count, error),
            name=f"escalate-llm-{agent_name}-{count}",
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


def get_consecutive_failures(agent_name: str) -> int:
    """Return the current consecutive failure count for an agent (for testing)."""
    return _consecutive_failures.get(agent_name, 0)


def reset_failure_tracking() -> None:
    """Reset all failure tracking state (for testing)."""
    _consecutive_failures.clear()


# ---------------------------------------------------------------------------
# Fire-and-forget escalation helper
# ---------------------------------------------------------------------------


def fire_and_forget_escalation(coro: Any) -> None:
    """Schedule an escalation coroutine as a fire-and-forget task.

    Stores task reference in _background_tasks to prevent GC collection.
    Used by ConstrainedAgent.invoke() to escalate without blocking.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("fire_and_forget_no_loop", exc_info=True)
        return

    task = loop.create_task(coro, name="fire-and-forget-escalation")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
