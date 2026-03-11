"""LangGraph StateGraph wiring for the full multi-agent pipeline.

Defines the production graph covering the complete Phase 1 flow::

    Scout -> Bid -> HITL(bid_approval) -> Planner
      -> Dev -> Content -> Design (sequential, MVP)
      -> Critic -> HITL(final_review) -> Packager -> END
             ^  (REVISE: back to Dev, max 3 cycles)

Also retains the legacy ``build_scout_bid_graph()`` for backward
compatibility with tests and scripts that only need the Scout -> Bid
sub-pipeline.

Usage::

    # Full pipeline (no persistence)
    graph = build_full_pipeline_graph()
    result = await graph.ainvoke(initial_state)

    # With persistence (Valkey + PostgreSQL)
    graph = create_graph_with_persistence(valkey, db_pool)
    result = await graph.ainvoke(
        initial_state, config={"configurable": {"thread_id": "abc"}}
    )

    # Convenience runners
    result = await run_full_pipeline(project_context)
    result = await run_scout_bid_pipeline(project_context)   # legacy

    # Resume after HITL approval (bid or final review)
    result = await resume_from_hitl(
        thread_id, {"action": "approve"}, hitl_type="bid_approval"
    )
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import asyncpg
import structlog
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph as CompiledGraph
from redis.asyncio import Redis as AsyncRedis

from src.agents.bid import bid_node
from src.agents.content import content_node
from src.agents.critic import critic_node
from src.agents.design import design_node
from src.agents.dev import dev_node
from src.agents.geo_scout import geo_scout_node
from src.agents.outreach import outreach_node
from src.agents.packager import packager_node
from src.agents.planner import planner_node
from src.agents.scout import scout_node
from src.core.checkpoints import HybridCheckpointSaver
from src.core.config import get_settings
from src.core.state import AgentState, ProjectContext, create_initial_state, update_state, validate_delivery_type
from src.core.tracing import build_langsmith_config

logger = structlog.get_logger(__name__)

# Maximum number of Critic -> Dev revision cycles before escalation.
MAX_REVISION_CYCLES: int = 3


# ---------------------------------------------------------------------------
# Bid submission node
# ---------------------------------------------------------------------------


async def bid_submission_node(state: dict[str, Any]) -> dict[str, Any]:
    """Submit the approved bid to the freelance platform.

    Called after HITL bid approval.  For Freelancer.com bids, calls the
    API to submit.  For other platforms (FL.ru, Kwork) the bid is marked
    as ``manual_submit_required`` since those platforms don't have a
    submission API.

    On failure, the state is updated with the error but the pipeline
    continues to the Planner (the bid was approved, we still want to
    prepare for the project).
    """
    platform = (state.get("project") or {}).get("platform", "")
    artifacts = dict(state.get("artifacts") or {})
    bid_data = artifacts.get("bid", {})

    if platform == "freelancer":
        try:
            from src.adapters.freelancer import FreelancerClient  # noqa: PLC0415
            from src.core.credential_loader import load_platform_credentials  # noqa: PLC0415

            # Try DB-stored credentials first, fall back to env vars.
            db_creds = await load_platform_credentials("freelancer")
            if db_creds:
                client_id = db_creds.get("client_id", "")
                client_secret = db_creds.get("client_secret", "")
            else:
                settings = get_settings()
                client_id = settings.FREELANCER_CLIENT_ID or ""
                client_secret = settings.FREELANCER_CLIENT_SECRET or ""
            client = FreelancerClient(
                client_id=client_id,
                client_secret=client_secret,
            )
            project_id = bid_data.get("project_id") or (state.get("project") or {}).get("job_id", "")
            description = bid_data.get("proposal", "")
            amount = bid_data.get("amount", 0)

            result = await client.submit_bid(
                project_id=project_id,
                description=description,
                amount=amount,
                period=int(bid_data.get("delivery_days", 7)),
            )
            artifacts["bid_submitted"] = True
            artifacts["bid_submission_result"] = result
            logger.info(
                "bid_submitted_freelancer",
                thread_id=state["thread_id"],
                project_id=project_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "bid_submission_failed",
                thread_id=state["thread_id"],
                platform=platform,
                error=str(exc),
            )
            artifacts["bid_submitted"] = False
            artifacts["bid_submission_error"] = str(exc)
    else:
        # FL.ru, Kwork, Upwork -- no auto-submit API
        artifacts["bid_submitted"] = False
        artifacts["manual_submit_required"] = True
        logger.info(
            "bid_manual_submit_required",
            thread_id=state["thread_id"],
            platform=platform,
        )

        # Create a HITL entry so the user sees a dashboard notification
        # to manually submit the bid on the non-API platform.
        try:
            import uuid as _uuid  # noqa: PLC0415

            from src.core.database import get_db_session  # noqa: PLC0415
            from src.core.models import HITLQueue  # noqa: PLC0415

            project_title = (state.get("project") or {}).get("title", "Unknown")
            job_url = (state.get("project") or {}).get("url", "")

            async with get_db_session() as session:
                hitl = HITLQueue(
                    id=_uuid.uuid4(),
                    type="manual_action",
                    priority="urgent",
                    title=f"Manual bid submission on {platform}: {project_title[:200]}",
                    description=(f"Bid approved but {platform} requires manual submission. Submit via the platform."),
                    payload={
                        "thread_id": state["thread_id"],
                        "platform": platform,
                        "job_url": job_url,
                        "bid_amount": bid_data.get("amount", 0),
                        "proposal_preview": (bid_data.get("proposal", ""))[:500],
                    },
                    available_actions=["approve", "skip"],
                    status="pending",
                )
                session.add(hitl)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "manual_submit_hitl_creation_failed",
                thread_id=state["thread_id"],
                error=str(exc),
            )

    return update_state(
        state,
        current_agent="bid_submission",
        next_agent="planner",
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# HITL node functions
# ---------------------------------------------------------------------------


async def hitl_bid_node(state: dict[str, Any]) -> dict[str, Any]:
    """HITL interrupt node for bid approval.

    Pauses the workflow so a human can review and approve/reject the
    generated bid proposal before it is submitted.  On resume the graph
    continues to the Planner node (approve) or terminates (reject).
    """
    logger.info(
        "hitl_bid_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    if state["status"] != "paused":
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_hitl_started_at"] = time.time()
        return update_state(
            state,
            status="paused",
            requires_hitl=True,
            current_agent="hitl_bid",
            artifacts=artifacts,
        )

    return state


async def hitl_review_node(state: dict[str, Any]) -> dict[str, Any]:
    """HITL interrupt node for final delivery review.

    Pauses the workflow so a human can inspect the packaged deliverables
    before the project is marked as completed.  On resume: approve marks
    the workflow complete; reject marks it failed.
    """
    logger.info(
        "hitl_review_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    if state["status"] != "paused":
        return update_state(
            state,
            status="paused",
            requires_hitl=True,
            current_agent="hitl_review",
        )

    return state


async def hitl_dev_launch_node(state: dict[str, Any]) -> dict[str, Any]:
    """HITL interrupt node for dev launch approval.

    Pauses the workflow so a human can explicitly approve starting
    the development cycle after the bid has been submitted.  The dev
    cycle NEVER starts automatically -- operator must approve.
    """
    logger.info(
        "hitl_dev_launch_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    if state["status"] != "paused":
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_hitl_started_at"] = time.time()

        # Create HITLQueue entry for dashboard visibility.
        try:
            from src.core.database import get_db_session  # noqa: PLC0415
            from src.core.models import HITLQueue  # noqa: PLC0415

            project = state.get("project") or {}
            project_id = project.get("project_id", state.get("thread_id", ""))
            project_title = project.get("title", "Unknown project")

            async with get_db_session() as session:
                import uuid as _uuid  # noqa: PLC0415

                hitl = HITLQueue(
                    id=_uuid.uuid4(),
                    type="dev_launch",
                    priority="high",
                    title=f"Launch development: {project_title[:200]}",
                    description=(
                        f"Bid submitted for '{project_title}'. "
                        f"Approve to start the development cycle (Planner → Dev → Critic → Packager)."
                    ),
                    payload={
                        "thread_id": state["thread_id"],
                        "project_id": project_id,
                        "platform": project.get("platform", ""),
                        "bid_amount": project.get("bid_amount"),
                    },
                    available_actions=["approve", "reject", "later"],
                    status="pending",
                )
                session.add(hitl)
                await session.commit()

                return update_state(
                    state,
                    status="paused",
                    requires_hitl=True,
                    hitl_request_id=str(hitl.id),
                    current_agent="hitl_dev_launch",
                    artifacts=artifacts,
                )
        except Exception:  # noqa: BLE001
            logger.exception("hitl_dev_launch_entry_creation_failed", thread_id=state["thread_id"])
            # Fail-open would be dangerous — fail closed instead.
            return update_state(
                state,
                status="paused",
                requires_hitl=True,
                current_agent="hitl_dev_launch",
                artifacts=artifacts,
            )

    return state


async def hitl_outreach_node(state: dict[str, Any]) -> dict[str, Any]:
    """HITL interrupt node for outreach batch approval (email + telegram).

    Pauses the workflow so a human can review and approve/reject
    the generated outreach messages before they are sent.
    """
    logger.info(
        "hitl_outreach_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    if state["status"] != "paused":
        return update_state(
            state,
            status="paused",
            requires_hitl=True,
            current_agent="hitl_outreach",
        )

    return state


# Backward-compat alias
hitl_email_node = hitl_outreach_node


# ---------------------------------------------------------------------------
# Message dispatch node (Pipeline B — multi-channel)
# ---------------------------------------------------------------------------


async def message_dispatch_node(state: dict[str, Any]) -> dict[str, Any]:
    """Send approved outreach messages across all channels after HITL approval.

    Dispatches both email and Telegram DMs based on ``channel_type`` of each
    CampaignLead.  Replaces the single-channel ``email_sending_node``.
    """
    artifacts = dict(state.get("artifacts") or {})

    if not artifacts.get("emails_approved"):
        logger.info(
            "message_dispatch_skipped",
            thread_id=state["thread_id"],
            reason="messages_not_approved",
        )
        return update_state(
            state,
            current_agent="message_dispatch",
            next_agent=None,
            status="completed",
            artifacts=artifacts,
        )

    campaign_id = artifacts.get("campaign_id")
    if not campaign_id:
        logger.warning(
            "message_dispatch_no_campaign",
            thread_id=state["thread_id"],
        )
        artifacts["send_results"] = {"error": "no campaign_id"}
        return update_state(
            state,
            current_agent="message_dispatch",
            next_agent=None,
            status="completed",
            artifacts=artifacts,
        )

    results: dict[str, Any] = {}

    try:
        from sqlalchemy import update as sa_update  # noqa: PLC0415

        from src.core.database import get_db_session  # noqa: PLC0415
        from src.core.models import CampaignLead  # noqa: PLC0415

        async with get_db_session() as session:
            # Mark all pending campaign leads as "approved".
            await session.execute(
                sa_update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_id,
                    CampaignLead.status == "pending",
                )
                .values(status="approved")
            )
            await session.commit()

            # Send emails
            try:
                from src.enrichment.email_sender import send_approved_emails  # noqa: PLC0415

                results["email"] = await send_approved_emails(campaign_id, session)
            except Exception as exc:  # noqa: BLE001
                logger.error("email_dispatch_failed", error=str(exc), exc_info=True)
                results["email"] = {"sent": 0, "failed": 0, "error": str(exc)}

            # Send Telegram DMs
            try:
                from src.enrichment.telegram_sender import send_approved_telegram_dms  # noqa: PLC0415

                results["telegram"] = await send_approved_telegram_dms(campaign_id, session)
            except Exception as exc:  # noqa: BLE001
                logger.error("telegram_dispatch_failed", error=str(exc), exc_info=True)
                results["telegram"] = {"sent": 0, "failed": 0, "error": str(exc)}

        artifacts["send_results"] = results
        # Keep backward-compat key
        artifacts["email_send_result"] = results.get("email", {})

        total_sent = sum(r.get("sent", 0) for r in results.values() if isinstance(r, dict))
        if total_sent == 0:
            logger.warning(
                "message_dispatch_no_sends",
                thread_id=state["thread_id"],
                campaign_id=str(campaign_id),
            )
        else:
            logger.info(
                "message_dispatch_complete",
                thread_id=state["thread_id"],
                campaign_id=str(campaign_id),
                results=results,
            )

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "message_dispatch_failed",
            thread_id=state["thread_id"],
            error=str(exc),
        )
        artifacts["send_results"] = {"error": str(exc)}

    return update_state(
        state,
        current_agent="message_dispatch",
        next_agent=None,
        status="completed",
        artifacts=artifacts,
    )


# Backward-compat alias
email_sending_node = message_dispatch_node


# ---------------------------------------------------------------------------
# Legacy HITL node (kept for backward compatibility with build_scout_bid_graph)
# ---------------------------------------------------------------------------


async def hitl_node(state: dict[str, Any]) -> dict[str, Any]:
    """Generic HITL interrupt node (legacy, Scout -> Bid pipeline only).

    Marks the workflow as paused and returns state unchanged.  The caller
    inspects ``state["requires_hitl"]`` and stops streaming.  Resumption
    is handled externally via :func:`resume_from_hitl`.
    """
    logger.info(
        "hitl_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    if state["status"] != "paused":
        return update_state(state, status="paused", requires_hitl=True)

    return state


# ---------------------------------------------------------------------------
# Dynamic routing -- unified execution-phase routing
# ---------------------------------------------------------------------------

# Valid execution agent nodes for sequence routing.
_VALID_EXECUTION_NODES = frozenset({"dev_node", "content_node", "design_node"})


def _route_next_in_sequence(state: dict[str, Any]) -> str:
    """Unified routing function for the execution phase.

    Replaces individual ``_route_after_dev``, ``_route_after_content``,
    ``_route_after_design``.  Called after each execution agent and after
    Planner (via ``_route_after_planner`` delegation).

    Priority order:
        1. status == "failed" → END
        2. requires_hitl → hitl_review_node
        3. revision_target set → target agent node
        4. Empty sequence → packager_node (consulting)
        5. index >= len(sequence) → critic_node
        6. sequence[index] → next agent node
    """
    if state.get("status") == "failed":
        return END

    if state.get("requires_hitl"):
        return "hitl_review_node"

    revision_target = state.get("revision_target")
    if revision_target:
        target_node = f"{revision_target}_node"
        if target_node in _VALID_EXECUTION_NODES:
            return target_node
        logger.error("invalid_revision_target", target=revision_target)
        return END

    sequence = state.get("agent_sequence", [])
    index = state.get("current_sequence_index", 0)
    skipped = set(state.get("skipped_agents", []))

    if not sequence:
        return "packager_node"

    if index < 0:
        logger.error("negative_sequence_index", index=index)
        return END

    # Advance past any skipped agents.
    while index < len(sequence) and sequence[index] in skipped:
        index += 1

    if index >= len(sequence):
        return "critic_node"

    agent = sequence[index]
    node_name = f"{agent}_node"
    if node_name not in _VALID_EXECUTION_NODES:
        logger.error("invalid_agent_in_sequence", agent=agent, index=index, sequence=sequence)
        return END

    return node_name


# All possible targets from _route_next_in_sequence (used in graph edge maps).
_SEQUENCE_EDGE_MAP: dict[str, str] = {
    "dev_node": "dev_node",
    "content_node": "content_node",
    "design_node": "design_node",
    "critic_node": "critic_node",
    "packager_node": "packager_node",
    "hitl_review_node": "hitl_review_node",
    END: END,
}


# ---------------------------------------------------------------------------
# Routing functions -- Scout/Bid (shared between legacy and full pipeline)
# ---------------------------------------------------------------------------


def _route_after_scout(state: dict[str, Any]) -> str:
    """Determine the next node after the Scout Agent completes.

    Returns:
        ``"bid_node"`` when qualified jobs are found,
        ``END`` when there are no jobs or the workflow has failed.
    """
    if state.get("status") == "failed":
        logger.warning("scout_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("next_agent") == "bid":
        logger.info("scout_route_to_bid", thread_id=state["thread_id"])
        return "bid_node"

    logger.info("scout_route_to_end_no_jobs", thread_id=state["thread_id"])
    return END


def _route_after_bid(state: dict[str, Any]) -> str:
    """Determine the next node after the Bid Agent completes.

    Returns:
        ``"hitl_bid_node"`` when HITL approval is required (normal path),
        ``END`` on failure or when no HITL is needed (defensive).
    """
    if state.get("status") == "failed":
        logger.warning("bid_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("requires_hitl"):
        logger.info("bid_route_to_hitl_bid", thread_id=state["thread_id"])
        return "hitl_bid_node"

    # Defensive: Bid Agent should ALWAYS require HITL, but handle gracefully.
    logger.warning(
        "bid_route_to_end_no_hitl",
        thread_id=state["thread_id"],
        msg="Bid completed without HITL flag -- this is unexpected",
    )
    return END


# ---------------------------------------------------------------------------
# Routing functions -- legacy (Scout -> Bid -> HITL only)
# ---------------------------------------------------------------------------


def _route_after_bid_legacy(state: dict[str, Any]) -> str:
    """Route after Bid in the legacy Scout -> Bid -> HITL graph."""
    if state.get("status") == "failed":
        logger.warning("bid_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("requires_hitl"):
        logger.info("bid_route_to_hitl", thread_id=state["thread_id"])
        return "hitl_node"

    logger.warning(
        "bid_route_to_end_no_hitl",
        thread_id=state["thread_id"],
        msg="Bid completed without HITL flag -- this is unexpected",
    )
    return END


# ---------------------------------------------------------------------------
# Routing functions -- Full pipeline
# ---------------------------------------------------------------------------


def _route_after_hitl_bid(state: dict[str, Any]) -> str:
    """Route after the bid-approval HITL node.

    Returns:
        ``"bid_submission_node"`` if the bid was approved (status not failed),
        ``END`` if the bid was rejected or workflow failed.
    """
    if state.get("status") == "failed":
        logger.info("hitl_bid_route_to_end_failed", thread_id=state["thread_id"])
        return END

    logger.info("hitl_bid_route_to_bid_submission", thread_id=state["thread_id"])
    return "bid_submission_node"


def _route_after_bid_submission(state: dict[str, Any]) -> str:
    """Route after the bid submission node.

    Routes to the dev-launch HITL gate so the operator must explicitly
    approve starting the development cycle.

    Returns:
        ``"hitl_dev_launch_node"`` on success,
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("bid_submission_route_to_end_failed", thread_id=state["thread_id"])
        return END

    logger.info("bid_submission_route_to_dev_launch", thread_id=state["thread_id"])
    return "hitl_dev_launch_node"


def _route_after_hitl_dev_launch(state: dict[str, Any]) -> str:
    """Route after the dev-launch HITL node.

    Returns:
        ``"planner_node"`` if dev launch was approved,
        ``END`` if rejected or failed.
    """
    if state.get("status") == "failed":
        logger.info("hitl_dev_launch_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("status") == "active":
        logger.info("hitl_dev_launch_route_to_planner", thread_id=state["thread_id"])
        return "planner_node"

    # Still paused or unknown status
    logger.info("hitl_dev_launch_route_to_end", thread_id=state["thread_id"], status=state.get("status"))
    return END


def _route_after_planner(state: dict[str, Any]) -> str:
    """Route after the Planner Agent.

    Delegates to ``_route_next_in_sequence`` for dynamic execution-phase
    routing.  HITL plan_review check is done first.

    Returns:
        First agent in ``agent_sequence`` (dynamic),
        ``"hitl_review_node"`` if HITL plan review is requested,
        ``"packager_node"`` for consulting (empty sequence),
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("planner_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("requires_hitl"):
        logger.info("planner_route_to_hitl_review", thread_id=state["thread_id"])
        return "hitl_review_node"

    # Delegate to unified routing
    result = _route_next_in_sequence(state)
    logger.info("planner_route_dynamic", thread_id=state["thread_id"], target=result)
    return result


def _route_after_dev(state: dict[str, Any]) -> str:
    """Route after the Dev Agent.

    Returns:
        ``"content_node"`` to proceed to Content Agent,
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("dev_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("next_agent") == "content":
        logger.info("dev_route_to_content", thread_id=state["thread_id"])
        return "content_node"

    logger.warning("dev_route_to_end_no_next", thread_id=state["thread_id"])
    return END


def _route_after_content(state: dict[str, Any]) -> str:
    """Route after the Content Agent.

    Returns:
        ``"design_node"`` to proceed to Design Agent,
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("content_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("next_agent") == "design":
        logger.info("content_route_to_design", thread_id=state["thread_id"])
        return "design_node"

    logger.warning("content_route_to_end_no_next", thread_id=state["thread_id"])
    return END


def _route_after_design(state: dict[str, Any]) -> str:
    """Route after the Design Agent.

    Returns:
        ``"critic_node"`` to proceed to quality review,
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("design_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("next_agent") == "critic":
        logger.info("design_route_to_critic", thread_id=state["thread_id"])
        return "critic_node"

    logger.warning("design_route_to_end_no_next", thread_id=state["thread_id"])
    return END


def _route_after_critic(state: dict[str, Any]) -> str:
    """Route after the Critic Agent -- the most complex routing point.

    The Critic may:
    - Approve and forward to Packager (``next_agent == "packager"``)
    - Request minor revisions via ``revision_target`` (any execution agent)
    - Request minor revisions via ``next_agent == "dev"`` (legacy compat)
    - Request major revisions from Planner (``next_agent == "planner"``)
    - Escalate to HITL review (``requires_hitl`` — reject or scope_creep)
    - Fail / terminate (fallback)

    Returns:
        ``"packager_node"`` on approval,
        ``"{revision_target}_node"`` on minor revision (any execution agent),
        ``"planner_node"`` on major revision (re-decomposition),
        ``"hitl_review_node"`` when human review is needed,
        ``END`` on failure or when revision limit is exceeded.
    """
    if state.get("status") == "failed":
        logger.warning("critic_route_to_end_failed", thread_id=state["thread_id"])
        return END

    # HITL escalation requested by Critic (reject or scope_creep)
    if state.get("requires_hitl"):
        logger.info("critic_route_to_hitl_review", thread_id=state["thread_id"])
        return "hitl_review_node"

    # APPROVED -- forward to Packager
    if state.get("next_agent") == "packager":
        logger.info("critic_route_to_packager", thread_id=state["thread_id"])
        return "packager_node"

    # MAJOR REVISION -- send back to Planner for re-decomposition
    if state.get("next_agent") == "planner":
        logger.info(
            "critic_route_to_planner_major_revision",
            thread_id=state["thread_id"],
        )
        return "planner_node"

    # --- Revision limit check (shared by revision_target and legacy next_agent=dev) ---
    artifacts = state.get("artifacts") or {}
    count_data = artifacts.get("_critic_revision_count", [])
    revision_count = 0
    if isinstance(count_data, list) and count_data:
        try:
            revision_count = int(count_data[0])
        except (ValueError, TypeError):
            pass
    elif isinstance(count_data, (int, float)):
        revision_count = int(count_data)

    # MINOR REVISION via revision_target (new dynamic routing)
    revision_target = state.get("revision_target")
    if revision_target:
        target_node = f"{revision_target}_node"
        if target_node in _VALID_EXECUTION_NODES:
            if revision_count < MAX_REVISION_CYCLES:
                logger.info(
                    "critic_route_to_revision_target",
                    thread_id=state["thread_id"],
                    target=revision_target,
                    revision_count=revision_count,
                )
                return target_node
            logger.warning(
                "critic_revision_limit_exceeded",
                thread_id=state["thread_id"],
                revision_count=revision_count,
            )
            return "hitl_review_node"

    # MINOR REVISION via next_agent=dev (legacy compat)
    if state.get("next_agent") == "dev":
        if revision_count < MAX_REVISION_CYCLES:
            logger.info(
                "critic_route_to_dev_revision",
                thread_id=state["thread_id"],
                revision_count=revision_count,
                max_revisions=MAX_REVISION_CYCLES,
            )
            return "dev_node"
        logger.warning(
            "critic_revision_limit_exceeded",
            thread_id=state["thread_id"],
            revision_count=revision_count,
        )
        return "hitl_review_node"

    logger.warning("critic_route_to_end_no_next", thread_id=state["thread_id"])
    return END


def _route_after_packager(state: dict[str, Any]) -> str:
    """Route after the Packager Agent.

    Packager ALWAYS requires HITL for final delivery review.

    Returns:
        ``"hitl_review_node"`` (normal path),
        ``END`` on failure (defensive).
    """
    if state.get("status") == "failed":
        logger.warning("packager_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("requires_hitl"):
        logger.info("packager_route_to_hitl_review", thread_id=state["thread_id"])
        return "hitl_review_node"

    # Defensive: Packager should ALWAYS require HITL.
    logger.warning(
        "packager_route_to_end_no_hitl",
        thread_id=state["thread_id"],
        msg="Packager completed without HITL flag -- this is unexpected",
    )
    return END


def _route_after_hitl_review(state: dict[str, Any]) -> str:
    """Route after the HITL review node.

    For plan_review approvals, delegates to ``_route_next_in_sequence``
    so the pipeline continues with the first agent in the dynamic
    sequence (not hardcoded to dev).  For final_review or rejections,
    terminates the graph.

    The ``_hitl_type`` artifact is set by ``resume_from_hitl`` to
    distinguish between plan_review and final_review contexts.

    Returns:
        First agent in ``agent_sequence`` when plan_review approved,
        ``END`` otherwise.
    """
    artifacts = state.get("artifacts") or {}
    hitl_type = artifacts.get("_hitl_type", "")
    status = state.get("status")

    # Plan review approved -> delegate to dynamic routing
    if hitl_type == "plan_review" and status == "active":
        result = _route_next_in_sequence(state)
        logger.info(
            "hitl_review_route_plan_approved",
            thread_id=state["thread_id"],
            target=result,
        )
        return result

    logger.info(
        "hitl_review_route_to_end",
        thread_id=state["thread_id"],
        status=status,
        hitl_type=hitl_type,
    )
    return END


# ---------------------------------------------------------------------------
# Routing functions -- Pipeline B (Geo Scout -> Outreach)
# ---------------------------------------------------------------------------


def _route_after_geo_scout(state: dict[str, Any]) -> str:
    """Route after GeoScout: outreach or END."""
    if state.get("status") == "failed":
        return END
    if state.get("next_agent") == "outreach":
        return "outreach_node"
    return END


def _route_after_outreach(state: dict[str, Any]) -> str:
    """Route after Outreach: HITL outreach approval or END."""
    if state.get("status") == "failed":
        return END
    if state.get("requires_hitl"):
        return "hitl_outreach_node"
    # No messages drafted -- completed without HITL
    return END


def _route_after_hitl_outreach(state: dict[str, Any]) -> str:
    """Route after outreach HITL: dispatch messages if approved, else END."""
    if state.get("status") == "failed":
        return END
    artifacts = state.get("artifacts") or {}
    if artifacts.get("emails_approved"):
        return "message_dispatch_node"
    return END


# Backward-compat alias
_route_after_hitl_email = _route_after_hitl_outreach


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def build_full_pipeline_graph(checkpointer: Any | None = None) -> CompiledGraph:
    """Build and compile the full Phase 1 pipeline graph.

    The graph topology is::

        scout -> bid -> hitl_bid -> bid_submission -> hitl_dev_launch
          -> planner -> dev -> content -> design
          -> critic -> packager -> hitl_review -> END
                ^--- (revision loop, max 3 cycles) ---|

    Args:
        checkpointer: An optional ``BaseCheckpointSaver`` instance (e.g.
            :class:`HybridCheckpointSaver`).  When provided the graph
            persists state after each node, enabling pause/resume for HITL.

    Returns:
        A compiled LangGraph ready for ``ainvoke`` / ``astream``.
    """
    graph = StateGraph(dict)

    # -- Add nodes ----------------------------------------------------------
    graph.add_node("scout_node", scout_node)
    graph.add_node("bid_node", bid_node)
    graph.add_node("hitl_bid_node", hitl_bid_node)
    graph.add_node("bid_submission_node", bid_submission_node)
    graph.add_node("hitl_dev_launch_node", hitl_dev_launch_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("dev_node", dev_node)
    graph.add_node("content_node", content_node)
    graph.add_node("design_node", design_node)
    graph.add_node("critic_node", critic_node)
    graph.add_node("packager_node", packager_node)
    graph.add_node("hitl_review_node", hitl_review_node)

    # -- Set entry point ----------------------------------------------------
    graph.set_entry_point("scout_node")

    # -- Conditional edges --------------------------------------------------

    # Scout -> Bid | END
    graph.add_conditional_edges(
        "scout_node",
        _route_after_scout,
        {
            "bid_node": "bid_node",
            END: END,
        },
    )

    # Bid -> HITL(bid) | END
    graph.add_conditional_edges(
        "bid_node",
        _route_after_bid,
        {
            "hitl_bid_node": "hitl_bid_node",
            END: END,
        },
    )

    # HITL(bid) -> Bid Submission | END
    graph.add_conditional_edges(
        "hitl_bid_node",
        _route_after_hitl_bid,
        {
            "bid_submission_node": "bid_submission_node",
            END: END,
        },
    )

    # Bid Submission -> HITL(dev_launch) | END
    graph.add_conditional_edges(
        "bid_submission_node",
        _route_after_bid_submission,
        {
            "hitl_dev_launch_node": "hitl_dev_launch_node",
            END: END,
        },
    )

    # HITL(dev_launch) -> Planner | END
    graph.add_conditional_edges(
        "hitl_dev_launch_node",
        _route_after_hitl_dev_launch,
        {
            "planner_node": "planner_node",
            END: END,
        },
    )

    # Planner -> dynamic sequence | HITL(plan_review) | Packager (consulting) | END
    graph.add_conditional_edges(
        "planner_node",
        _route_after_planner,
        _SEQUENCE_EDGE_MAP,
    )

    # Execution agents -> dynamic sequence (unified routing)
    for agent_node in ("dev_node", "content_node", "design_node"):
        graph.add_conditional_edges(
            agent_node,
            _route_next_in_sequence,
            _SEQUENCE_EDGE_MAP,
        )

    # Critic -> Packager | revision_target | Planner (major) | HITL | END
    graph.add_conditional_edges(
        "critic_node",
        _route_after_critic,
        {
            "packager_node": "packager_node",
            "dev_node": "dev_node",
            "content_node": "content_node",
            "design_node": "design_node",
            "planner_node": "planner_node",
            "hitl_review_node": "hitl_review_node",
            END: END,
        },
    )

    # Packager -> HITL(review) | END
    graph.add_conditional_edges(
        "packager_node",
        _route_after_packager,
        {
            "hitl_review_node": "hitl_review_node",
            END: END,
        },
    )

    # HITL(review) -> dynamic sequence (plan_review) | END
    graph.add_conditional_edges(
        "hitl_review_node",
        _route_after_hitl_review,
        _SEQUENCE_EDGE_MAP,
    )

    # -- Compile ------------------------------------------------------------
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "full_pipeline_graph_compiled",
        has_checkpointer=checkpointer is not None,
    )
    return compiled


def build_scout_bid_graph(checkpointer: Any | None = None) -> CompiledGraph:
    """Build and compile the legacy Scout -> Bid -> HITL StateGraph.

    Retained for backward compatibility with existing tests and scripts.
    For new code prefer :func:`build_full_pipeline_graph`.

    Args:
        checkpointer: An optional ``BaseCheckpointSaver`` instance.

    Returns:
        A compiled LangGraph ready for ``ainvoke`` / ``astream``.
    """
    graph = StateGraph(dict)

    # -- Add nodes ----------------------------------------------------------
    graph.add_node("scout_node", scout_node)
    graph.add_node("bid_node", bid_node)
    graph.add_node("hitl_node", hitl_node)

    # -- Set entry point ----------------------------------------------------
    graph.set_entry_point("scout_node")

    # -- Conditional edges --------------------------------------------------
    graph.add_conditional_edges(
        "scout_node",
        _route_after_scout,
        {
            "bid_node": "bid_node",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "bid_node",
        _route_after_bid_legacy,
        {
            "hitl_node": "hitl_node",
            END: END,
        },
    )

    # -- HITL node always terminates the graph (legacy) ---------------------
    graph.add_edge("hitl_node", END)

    # -- Compile ------------------------------------------------------------
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("scout_bid_graph_compiled", has_checkpointer=checkpointer is not None)
    return compiled


def build_pipeline_b_graph(checkpointer: Any | None = None) -> CompiledGraph:
    """Build and compile the Pipeline B (Outreach) graph.

    Flow: GeoScout -> Outreach -> [HITL outreach approval] -> Message Dispatch -> END

    Args:
        checkpointer: Optional checkpoint saver for persistence.

    Returns:
        A compiled LangGraph.
    """
    graph = StateGraph(dict)

    # Nodes
    graph.add_node("geo_scout_node", geo_scout_node)
    graph.add_node("outreach_node", outreach_node)
    graph.add_node("hitl_outreach_node", hitl_outreach_node)
    graph.add_node("message_dispatch_node", message_dispatch_node)

    # Entry point
    graph.set_entry_point("geo_scout_node")

    # Edges
    graph.add_conditional_edges(
        "geo_scout_node",
        _route_after_geo_scout,
        {"outreach_node": "outreach_node", END: END},
    )
    graph.add_conditional_edges(
        "outreach_node",
        _route_after_outreach,
        {"hitl_outreach_node": "hitl_outreach_node", END: END},
    )
    graph.add_conditional_edges(
        "hitl_outreach_node",
        _route_after_hitl_outreach,
        {"message_dispatch_node": "message_dispatch_node", END: END},
    )
    graph.add_edge("message_dispatch_node", END)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("pipeline_b_graph_compiled", has_checkpointer=checkpointer is not None)
    return compiled


def build_planner_pipeline_graph(checkpointer: Any | None = None) -> CompiledGraph:
    """Build a pipeline that starts at Planner (skipping Scout/Bid/HITL).

    Used by ``run_project_pipeline`` for won projects where the bid has
    already been approved and submitted.  The flow is::

        Planner -> Dev -> Content -> Design -> Critic
          -> Packager -> HITL(review) -> END

    Args:
        checkpointer: Optional checkpoint saver for persistence.

    Returns:
        A compiled LangGraph.
    """
    graph = StateGraph(dict)

    graph.add_node("planner_node", planner_node)
    graph.add_node("dev_node", dev_node)
    graph.add_node("content_node", content_node)
    graph.add_node("design_node", design_node)
    graph.add_node("critic_node", critic_node)
    graph.add_node("packager_node", packager_node)
    graph.add_node("hitl_review_node", hitl_review_node)

    graph.set_entry_point("planner_node")

    graph.add_conditional_edges(
        "planner_node",
        _route_after_planner,
        _SEQUENCE_EDGE_MAP,
    )
    for agent_node in ("dev_node", "content_node", "design_node"):
        graph.add_conditional_edges(
            agent_node,
            _route_next_in_sequence,
            _SEQUENCE_EDGE_MAP,
        )
    graph.add_conditional_edges(
        "critic_node",
        _route_after_critic,
        {
            "packager_node": "packager_node",
            "dev_node": "dev_node",
            "content_node": "content_node",
            "design_node": "design_node",
            "planner_node": "planner_node",
            "hitl_review_node": "hitl_review_node",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "packager_node",
        _route_after_packager,
        {"hitl_review_node": "hitl_review_node", END: END},
    )
    graph.add_conditional_edges(
        "hitl_review_node",
        _route_after_hitl_review,
        _SEQUENCE_EDGE_MAP,
    )

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("planner_pipeline_graph_compiled", has_checkpointer=checkpointer is not None)
    return compiled


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------


def create_graph_with_persistence(
    valkey: AsyncRedis,
    db_pool: asyncpg.Pool,
    *,
    full_pipeline: bool = True,
    pipeline_b: bool = False,
    planner_pipeline: bool = False,
) -> CompiledGraph:
    """Build a graph backed by hybrid persistence.

    Args:
        valkey: An ``AsyncRedis`` client connected to Valkey.
        db_pool: An ``asyncpg.Pool`` connected to PostgreSQL.
        full_pipeline: When ``True`` (default) builds the full Phase 1
            pipeline.  When ``False`` builds the legacy Scout -> Bid graph.
            Ignored when *pipeline_b* or *planner_pipeline* is ``True``.
        pipeline_b: When ``True`` builds the Pipeline B (Outreach) graph.
        planner_pipeline: When ``True`` builds the Planner pipeline
            (skipping Scout/Bid/HITL).  Used by ``run_project_pipeline``.

    Returns:
        A compiled graph with :class:`HybridCheckpointSaver` attached.
    """
    checkpointer = HybridCheckpointSaver(valkey=valkey, db_pool=db_pool)
    if pipeline_b:
        return build_pipeline_b_graph(checkpointer=checkpointer)
    if planner_pipeline:
        return build_planner_pipeline_graph(checkpointer=checkpointer)
    if full_pipeline:
        return build_full_pipeline_graph(checkpointer=checkpointer)
    return build_scout_bid_graph(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Convenience runners
# ---------------------------------------------------------------------------


async def run_full_pipeline(
    project_context: ProjectContext,
    thread_id: str | None = None,
) -> AgentState:
    """Run the full Phase 1 pipeline end-to-end (no persistence).

    This is a convenience wrapper for scripts, tests, and one-shot CLI
    invocations.  For production use, prefer
    :func:`create_graph_with_persistence` and invoke the graph directly.

    Args:
        project_context: The :class:`ProjectContext` describing the target
            project / search criteria.
        thread_id: Optional thread identifier.  A UUID4 hex string is
            generated when omitted.

    Returns:
        The final ``AgentState`` after the graph terminates (either at END
        or paused at an HITL node).
    """
    tid = thread_id or uuid.uuid4().hex
    initial_state = create_initial_state(
        project=project_context,
        first_agent="scout",
        thread_id=tid,
    )

    graph = build_full_pipeline_graph()
    config = build_langsmith_config(
        pipeline_name="full_pipeline",
        thread_id=tid,
    )

    logger.info("full_pipeline_start", thread_id=tid)
    result: AgentState = await graph.ainvoke(initial_state, config=config or None)
    logger.info(
        "full_pipeline_finished",
        thread_id=tid,
        status=result.get("status"),
        requires_hitl=result.get("requires_hitl"),
        current_agent=result.get("current_agent"),
    )
    return result


async def run_scout_bid_pipeline(
    project_context: ProjectContext,
    thread_id: str | None = None,
) -> AgentState:
    """Run the legacy Scout -> Bid -> HITL pipeline end-to-end.

    Retained for backward compatibility.  Builds a graph **without**
    persistence.  For production use, prefer
    :func:`create_graph_with_persistence`.

    Args:
        project_context: The :class:`ProjectContext` describing the target
            project / search criteria.
        thread_id: Optional thread identifier.  A UUID4 hex string is
            generated when omitted.

    Returns:
        The final ``AgentState`` after the graph terminates (either at END
        or paused at the HITL node).
    """
    tid = thread_id or uuid.uuid4().hex
    initial_state = create_initial_state(
        project=project_context,
        first_agent="scout",
        thread_id=tid,
    )

    graph = build_scout_bid_graph()
    config = build_langsmith_config(
        pipeline_name="scout_bid_pipeline",
        thread_id=tid,
    )

    logger.info("pipeline_start", thread_id=tid)
    result: AgentState = await graph.ainvoke(initial_state, config=config or None)
    logger.info(
        "pipeline_finished",
        thread_id=tid,
        status=result.get("status"),
        requires_hitl=result.get("requires_hitl"),
    )
    return result


async def run_pipeline_b(
    city: str,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> AgentState:
    """Run Pipeline B (Geo Scout -> Outreach) for a city.

    Args:
        city: City name to scan for offline businesses.
        thread_id: Optional thread identifier.
        user_id: Optional user UUID string for credential loading.

    Returns:
        Final AgentState after pipeline completes or pauses for HITL.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    tid = thread_id or uuid.uuid4().hex
    # Create a minimal project context for Pipeline B
    project = ProjectContext(
        project_id=f"pipeline_b_{tid[:8]}",
        job_id="",
        platform="outreach",
        client={},
        requirements=city,
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    initial_state = create_initial_state(
        project=project,
        first_agent="geoscout",
        thread_id=tid,
        user_id=user_id,
    )
    # Set city in artifacts for the geo_scout agent
    initial_state["artifacts"] = {"_scan_city": city}

    graph = build_pipeline_b_graph()
    config = build_langsmith_config(
        pipeline_name="pipeline_b",
        thread_id=tid,
        extra_metadata={"city": city},
    )

    logger.info("pipeline_b_start", thread_id=tid, city=city)
    result: AgentState = await graph.ainvoke(initial_state, config=config or None)
    logger.info(
        "pipeline_b_finished",
        thread_id=tid,
        status=result.get("status"),
        requires_hitl=result.get("requires_hitl"),
    )
    return result


# ---------------------------------------------------------------------------
# HITL resume
# ---------------------------------------------------------------------------


async def resume_from_hitl(
    thread_id: str,
    hitl_response: dict[str, Any],
    *,
    hitl_type: str | None = None,
    valkey: AsyncRedis | None = None,
    db_pool: asyncpg.Pool | None = None,
) -> AgentState:
    """Resume a paused workflow after HITL approval.

    Loads the latest checkpoint for *thread_id*, merges the human's
    response into the state, and -- for the full pipeline -- re-invokes
    the graph so execution continues to downstream agents.

    Args:
        thread_id: The workflow thread to resume.
        hitl_response: A dict containing the human's decision, e.g.::

            {
                "action": "approve",  # or "reject", "edit"
                "bid_ids": ["..."],
                "edits": {...},       # optional modifications
            }

        hitl_type: The type of HITL interruption.  One of
            ``"bid_approval"`` or ``"final_review"``.  When ``None`` the
            function auto-detects from the saved ``current_agent`` field.
        valkey: Async Valkey client.  When ``None`` the default client
            from :func:`~src.core.database.get_valkey` is used.
        db_pool: Async PostgreSQL pool.  When ``None`` a temporary pool
            is created from settings (caller should provide one in
            production).

    Returns:
        The final ``AgentState`` after the resumed graph terminates.

    Raises:
        ValueError: If no checkpoint is found for the given *thread_id*,
            or if the HITL type cannot be determined.
    """
    from src.core.database import get_valkey  # noqa: PLC0415

    settings = get_settings()
    _valkey = valkey or get_valkey()

    # Build a temporary pool if the caller didn't supply one.
    _pool_created = False
    _db_pool = db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=5)
        _pool_created = True

    try:
        checkpointer = HybridCheckpointSaver(valkey=_valkey, db_pool=_db_pool)

        # Load the latest checkpoint for this thread.
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            raise ValueError(f"No checkpoint found for thread_id={thread_id!r}")

        # Reconstruct the state from the checkpoint.
        saved_state: dict[str, Any] = checkpoint_tuple.checkpoint
        action = hitl_response.get("action", "approve")

        # Check if HITL has expired (24-hour max age).
        artifacts = saved_state.get("artifacts") or {}
        hitl_started = artifacts.get("_hitl_started_at", 0)
        if hitl_started and (time.time() - hitl_started > 86400):
            logger.warning("hitl_expired_24h", thread_id=thread_id)
            return {"status": "failed", "error": "HITL approval expired after 24 hours"}  # type: ignore[return-value]

        # Auto-detect HITL type from saved state if not provided.
        resolved_type = hitl_type
        if resolved_type is None:
            current_agent = saved_state.get("current_agent", "")
            if current_agent == "hitl_bid":
                resolved_type = "bid_approval"
            elif current_agent == "hitl_review":
                resolved_type = "final_review"
            elif current_agent == "hitl_dev_launch":
                resolved_type = "dev_launch"
            elif current_agent in ("hitl_email", "hitl_outreach"):
                resolved_type = "outreach_approval"
            else:
                # Fall back to legacy behaviour (Phase 1 Scout -> Bid only).
                resolved_type = "bid_approval"
                logger.warning(
                    "hitl_type_auto_detected_fallback",
                    thread_id=thread_id,
                    current_agent=current_agent,
                    resolved_type=resolved_type,
                )

        logger.info(
            "hitl_resume_start",
            thread_id=thread_id,
            action=action,
            hitl_type=resolved_type,
        )

        # ----- bid_approval -----------------------------------------------
        if resolved_type == "bid_approval":
            resumed_state = _apply_bid_approval(saved_state, action, hitl_response, thread_id)

            # On approval, re-invoke the full pipeline graph so execution
            # continues to the Planner and beyond.
            if action == "approve":
                graph = build_full_pipeline_graph(checkpointer=checkpointer)
                try:
                    result: AgentState = await asyncio.wait_for(
                        graph.ainvoke(resumed_state, config=config),
                        timeout=300,
                    )
                except TimeoutError:
                    logger.error("hitl_resume_timed_out", thread_id=thread_id, hitl_type="bid_approval")
                    return {"status": "failed", "error": "HITL resume timed out"}  # type: ignore[return-value]
                logger.info(
                    "hitl_bid_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result

            # Rejection / edit without continuation -- save and return.
            await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})
            return resumed_state  # type: ignore[return-value]

        # ----- dev_launch -------------------------------------------------
        if resolved_type == "dev_launch":
            resumed_state = _apply_dev_launch(saved_state, action, hitl_response, thread_id)

            if action == "approve":
                graph = build_full_pipeline_graph(checkpointer=checkpointer)
                try:
                    result = await asyncio.wait_for(
                        graph.ainvoke(resumed_state, config=config),
                        timeout=600,
                    )
                except TimeoutError:
                    logger.error("hitl_resume_timed_out", thread_id=thread_id, hitl_type="dev_launch")
                    return {"status": "failed", "error": "HITL resume timed out"}  # type: ignore[return-value]
                logger.info(
                    "hitl_dev_launch_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result  # type: ignore[return-value]

            await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})
            return resumed_state  # type: ignore[return-value]

        # ----- plan_review ------------------------------------------------
        if resolved_type == "plan_review":
            resumed_state = _apply_plan_review(saved_state, action, hitl_response, thread_id)

            # On approval, re-invoke the pipeline so execution
            # continues from hitl_review_node -> dev_node.
            if action == "approve":
                graph = build_full_pipeline_graph(checkpointer=checkpointer)
                try:
                    result = await asyncio.wait_for(
                        graph.ainvoke(resumed_state, config=config),
                        timeout=300,
                    )
                except TimeoutError:
                    logger.error("hitl_resume_timed_out", thread_id=thread_id, hitl_type="plan_review")
                    return {"status": "failed", "error": "HITL resume timed out"}  # type: ignore[return-value]
                logger.info(
                    "hitl_plan_review_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result  # type: ignore[return-value]

            await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})

            logger.info(
                "hitl_plan_review_resumed",
                thread_id=thread_id,
                action=action,
                new_status=resumed_state["status"],
            )
            return resumed_state  # type: ignore[return-value]

        # ----- final_review -----------------------------------------------
        if resolved_type == "final_review":
            resumed_state = _apply_final_review(saved_state, action, hitl_response, thread_id)

            await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})

            logger.info(
                "hitl_review_resumed",
                thread_id=thread_id,
                action=action,
                new_status=resumed_state["status"],
            )
            return resumed_state  # type: ignore[return-value]

        # ----- outreach_approval (Pipeline B, email + telegram) ---------------
        if resolved_type in ("email_approval", "outreach_approval"):
            resumed_state = _apply_outreach_approval(saved_state, action, hitl_response, thread_id)

            # On approval, re-invoke Pipeline B graph so the message_dispatch_node
            # runs and actually sends the approved messages.
            if action in ("approve", "edit"):
                graph = build_pipeline_b_graph(checkpointer=checkpointer)
                try:
                    result = await asyncio.wait_for(
                        graph.ainvoke(resumed_state, config=config),
                        timeout=300,
                    )
                except TimeoutError:
                    logger.error("hitl_resume_timed_out", thread_id=thread_id, hitl_type="outreach_approval")
                    return {"status": "failed", "error": "HITL resume timed out"}  # type: ignore[return-value]
                logger.info(
                    "hitl_outreach_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result  # type: ignore[return-value]

            await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})

            logger.info(
                "hitl_outreach_resumed",
                thread_id=thread_id,
                action=action,
                new_status=resumed_state["status"],
            )
            return resumed_state  # type: ignore[return-value]

        if resolved_type == "agent_failure":
            resumed_state = _apply_agent_failure_recovery(saved_state, action, hitl_response, thread_id)

            # On resume/skip/manual, re-invoke Pipeline A so the sequence continues.
            if resumed_state.get("status") == "active":
                graph = build_full_pipeline_graph(checkpointer=checkpointer)
                try:
                    result = await asyncio.wait_for(
                        graph.ainvoke(resumed_state, config=config),
                        timeout=600,
                    )
                except TimeoutError:
                    logger.error("hitl_resume_timed_out", thread_id=thread_id, hitl_type="agent_failure")
                    return {"status": "failed", "error": "HITL resume timed out"}  # type: ignore[return-value]
                logger.info(
                    "hitl_agent_failure_resumed_pipeline_finished",
                    thread_id=thread_id,
                    action=action,
                    status=result.get("status"),
                )
                return result  # type: ignore[return-value]

            # Failed (unknown action) — just checkpoint and return.
            await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})
            return resumed_state  # type: ignore[return-value]

        # ----- unknown type -----------------------------------------------
        raise ValueError(f"Unknown hitl_type={resolved_type!r}")

    finally:
        if _pool_created and _db_pool is not None:
            await _db_pool.close()


# ---------------------------------------------------------------------------
# Internal HITL response helpers
# ---------------------------------------------------------------------------


def _enrich_project_from_bid(saved_state: dict[str, Any]) -> dict[str, Any]:
    """Build a rich project context from approved bid + job data for Planner.

    Merges the existing ``state["project"]`` with details from the bid artifact
    and ``current_job`` so the Planner has the full picture.
    """
    project = dict(saved_state.get("project") or {})
    job = saved_state.get("current_job") or {}
    artifacts = saved_state.get("artifacts") or {}
    bid_artifact = artifacts.get("bid") or {}

    # Fill in fields from job data if not already present.
    if not project.get("requirements"):
        project["requirements"] = job.get("description", "")
    if not project.get("title"):
        project["title"] = job.get("title", "")
    if not project.get("budget"):
        project["budget"] = job.get("budget", {})
    if not project.get("skills"):
        project["skills"] = job.get("skills", [])
    if not project.get("deadline"):
        project["deadline"] = job.get("deadline")
    if not project.get("platform"):
        project["platform"] = job.get("platform", "")

    # Attach bid reference so Planner knows which bid was approved.
    if bid_artifact:
        project["bid_id"] = bid_artifact.get("id")
        project["bid_amount"] = bid_artifact.get("amount")

    return project


def _apply_bid_approval(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> AgentState:
    """Apply the human's bid-approval decision to the saved state.

    On **approve** the graph should continue to the Planner, so we set
    ``next_agent="planner"`` and ``status="active"``.
    """
    if action == "approve":
        project = _enrich_project_from_bid(saved_state)
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            next_agent="planner",
            current_agent="planner",
            project=project,
        )

    if action == "reject":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="failed",
            next_agent=None,
            errors=[*saved_state.get("errors", []), "HITL: bid rejected by human"],
        )

    if action == "edit":
        edits = hitl_response.get("edits", {})
        artifacts = dict(saved_state.get("artifacts") or {})
        if edits:
            artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits
        project = _enrich_project_from_bid(saved_state)
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            next_agent="planner",
            current_agent="planner",
            artifacts=artifacts,
            project=project,
        )

    # Unknown action -- fail closed to prevent accidental approval.
    logger.error("hitl_bid_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="failed",
        next_agent=None,
        errors=[
            *saved_state.get("errors", []),
            f"HITL: unknown bid action '{action}' — rejected for safety",
        ],
    )


def _apply_dev_launch(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> AgentState:
    """Apply the human's dev-launch decision to the saved state.

    On **approve** the graph continues to Planner.
    On **reject** the workflow is terminated.
    """
    if action == "approve":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            next_agent="planner",
            current_agent="planner",
        )

    if action == "reject":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="failed",
            next_agent=None,
            errors=[*saved_state.get("errors", []), "HITL: dev launch rejected by operator"],
        )

    if action == "later":
        # Operator defers — keep paused, do NOT resume pipeline.
        logger.info("hitl_dev_launch_deferred", thread_id=thread_id)
        return update_state(
            saved_state,  # type: ignore[arg-type]
            status="paused",
            requires_hitl=True,
        )

    # Unknown action -- fail closed.
    logger.error("hitl_dev_launch_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="failed",
        next_agent=None,
        errors=[
            *saved_state.get("errors", []),
            f"HITL: unknown dev_launch action '{action}' — rejected for safety",
        ],
    )


def _apply_plan_review(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> AgentState:
    """Apply the human's plan-review decision to the saved state.

    On **approve** the graph should continue to the Dev node, so we set
    ``next_agent="dev"`` and ``status="active"``.  The ``_hitl_type``
    artifact is set so ``_route_after_hitl_review`` routes correctly.
    """
    artifacts = dict(saved_state.get("artifacts") or {})
    artifacts["_hitl_type"] = "plan_review"

    if action == "approve":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            next_agent="dev",
            current_agent="hitl_review",
            artifacts=artifacts,
        )

    if action == "reject":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="failed",
            next_agent=None,
            artifacts=artifacts,
            errors=[*saved_state.get("errors", []), "HITL: plan rejected by human"],
        )

    if action == "edit":
        edits = hitl_response.get("edits", {})
        overrides: dict[str, Any] = {}

        if edits:
            artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits

            # --- Apply agent_sequence edit ---
            if "agent_sequence" in edits:
                valid_agents = {n.removesuffix("_node") for n in _VALID_EXECUTION_NODES}
                original_seq = list(saved_state.get("agent_sequence") or [])
                artifacts["_original_sequence"] = [original_seq]

                raw_seq = edits["agent_sequence"]
                if isinstance(raw_seq, list):
                    filtered = [a for a in raw_seq if a in valid_agents]
                    if len(filtered) != len(raw_seq):
                        logger.warning(
                            "plan_edit_invalid_agents_removed",
                            original=raw_seq,
                            filtered=filtered,
                            thread_id=thread_id,
                        )
                    overrides["agent_sequence"] = filtered
                    overrides["current_sequence_index"] = 0

            # --- Apply delivery_type edit ---
            if "delivery_type" in edits:
                overrides["delivery_type"] = validate_delivery_type(edits["delivery_type"])

            artifacts["_plan_edit_applied"] = [True]

        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            next_agent="dev",
            current_agent="hitl_review",
            artifacts=artifacts,
            **overrides,
        )

    # Unknown action -- fail closed to prevent accidental approval.
    logger.error("hitl_plan_review_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="failed",
        next_agent=None,
        artifacts=artifacts,
        errors=[
            *saved_state.get("errors", []),
            f"HITL: unknown plan review action '{action}' — rejected for safety",
        ],
    )


def _apply_final_review(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> AgentState:
    """Apply the human's final-review decision to the saved state."""
    if action == "approve":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="completed",
            next_agent=None,
        )

    if action == "reject":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="failed",
            next_agent=None,
            errors=[
                *saved_state.get("errors", []),
                "HITL: final delivery rejected by human",
            ],
        )

    if action == "edit":
        edits = hitl_response.get("edits", {})
        artifacts = dict(saved_state.get("artifacts") or {})
        if edits:
            artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="completed",
            next_agent=None,
            artifacts=artifacts,
        )

    # Unknown action -- fail closed to prevent accidental approval.
    logger.error("hitl_review_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="failed",
        next_agent=None,
        errors=[
            *saved_state.get("errors", []),
            f"HITL: unknown final review action '{action}' — rejected for safety",
        ],
    )


def _apply_outreach_approval(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> AgentState:
    """Apply the human's outreach-approval decision to the saved state.

    On **approve** all channels (email + telegram) are marked approved.
    On **reject** the campaign is cancelled.
    """
    if action == "approve":
        artifacts = dict(saved_state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        artifacts["telegram_approved"] = True
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            current_agent="hitl_outreach",
            next_agent=None,
            artifacts=artifacts,
        )

    if action == "reject":
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="failed",
            next_agent=None,
            errors=[
                *saved_state.get("errors", []),
                "HITL: outreach messages rejected by human",
            ],
        )

    if action == "edit":
        edits = hitl_response.get("edits", {})
        artifacts = dict(saved_state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        artifacts["telegram_approved"] = True
        if edits:
            artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            current_agent="hitl_outreach",
            next_agent=None,
            artifacts=artifacts,
        )

    # Unknown action -- fail closed to prevent accidental approval.
    logger.error("hitl_outreach_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="failed",
        next_agent=None,
        errors=[
            *saved_state.get("errors", []),
            f"HITL: unknown outreach approval action '{action}' — rejected for safety",
        ],
    )


# Backward-compat alias
_apply_email_approval = _apply_outreach_approval


def _apply_agent_failure_recovery(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Apply a recovery action after an execution agent failure.

    Args:
        saved_state: The paused state with ``failed_agent`` set.
        action: One of ``"resume"``, ``"skip"``, or ``"manual"``.
        hitl_response: The operator's response, may contain ``manual_artifacts``.
        thread_id: The workflow thread ID for logging.

    Returns:
        Updated state dict ready for graph re-invocation.
    """
    failed_agent = saved_state.get("failed_agent", "")

    if action == "resume":
        logger.info(
            "agent_failure_resume",
            thread_id=thread_id,
            agent=failed_agent,
            attempt=saved_state.get("recovery_attempted", 0) + 1,
        )
        return update_state(
            saved_state,  # type: ignore[arg-type]
            status="active",
            requires_hitl=False,
            hitl_request_id=None,
            recovery_attempted=saved_state.get("recovery_attempted", 0) + 1,
            # Keep failed_agent and current_sequence_index — graph retries same agent.
        )

    if action == "skip":
        skipped = list(saved_state.get("skipped_agents", []))
        if failed_agent and failed_agent not in skipped:
            skipped.append(failed_agent)
        logger.info(
            "agent_failure_skip",
            thread_id=thread_id,
            agent=failed_agent,
            skipped_agents=skipped,
        )
        return update_state(
            saved_state,  # type: ignore[arg-type]
            status="active",
            requires_hitl=False,
            hitl_request_id=None,
            failed_agent=None,
            failure_reason=None,
            skipped_agents=skipped,
            current_sequence_index=saved_state.get("current_sequence_index", 0) + 1,
        )

    if action == "manual":
        manual_artifacts = hitl_response.get("manual_artifacts", {})
        artifacts = dict(saved_state.get("artifacts") or {})
        # Merge operator-provided artifacts for the failed agent.
        for agent_name, agent_artifacts in manual_artifacts.items():
            artifacts[agent_name] = agent_artifacts
        logger.info(
            "agent_failure_manual",
            thread_id=thread_id,
            agent=failed_agent,
            manual_agents=list(manual_artifacts.keys()),
        )
        return update_state(
            saved_state,  # type: ignore[arg-type]
            status="active",
            requires_hitl=False,
            hitl_request_id=None,
            failed_agent=None,
            failure_reason=None,
            artifacts=artifacts,
            current_sequence_index=saved_state.get("current_sequence_index", 0) + 1,
        )

    # Unknown action — fail closed.
    logger.error("agent_failure_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        status="failed",
        next_agent=None,
        errors=[
            *saved_state.get("errors", []),
            f"HITL: unknown agent_failure action '{action}'",
        ],
    )
