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
from src.core.state import AgentState, ProjectContext, create_initial_state, update_state
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
            project_id = bid_data.get("project_id") or (
                state.get("project") or {}
            ).get("job_id", "")
            description = bid_data.get("proposal", "")
            amount = bid_data.get("amount", 0)

            result = await client.submit_bid(
                project_id=project_id,
                description=description,
                amount=amount,
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
        return update_state(
            state,
            status="paused",
            requires_hitl=True,
            current_agent="hitl_bid",
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


async def hitl_email_node(state: dict[str, Any]) -> dict[str, Any]:
    """HITL interrupt node for email batch approval.

    Pauses the workflow so a human can review and approve/reject
    the generated cold emails before they are sent.
    """
    logger.info(
        "hitl_email_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    if state["status"] != "paused":
        return update_state(
            state,
            status="paused",
            requires_hitl=True,
            current_agent="hitl_email",
        )

    return state


# ---------------------------------------------------------------------------
# Email sending node (Pipeline B)
# ---------------------------------------------------------------------------


async def email_sending_node(state: dict[str, Any]) -> dict[str, Any]:
    """Send approved outreach emails after HITL approval.

    Checks ``artifacts["emails_approved"]`` and, when ``True``, calls
    :func:`~src.enrichment.email_sender.send_approved_emails` with the
    campaign ID from artifacts.  Results are stored back in artifacts.
    """
    artifacts = dict(state.get("artifacts") or {})

    if not artifacts.get("emails_approved"):
        logger.info(
            "email_sending_skipped",
            thread_id=state["thread_id"],
            reason="emails_not_approved",
        )
        return update_state(
            state,
            current_agent="email_sending",
            next_agent=None,
            status="completed",
            artifacts=artifacts,
        )

    campaign_id = artifacts.get("campaign_id")
    if not campaign_id:
        logger.warning(
            "email_sending_no_campaign",
            thread_id=state["thread_id"],
        )
        artifacts["email_send_result"] = {"sent": 0, "failed": 0, "error": "no campaign_id"}
        return update_state(
            state,
            current_agent="email_sending",
            next_agent=None,
            status="completed",
            artifacts=artifacts,
        )

    try:
        from sqlalchemy import update as sa_update  # noqa: PLC0415

        from src.core.database import get_db_session  # noqa: PLC0415
        from src.core.models import CampaignLead  # noqa: PLC0415
        from src.enrichment.email_sender import send_approved_emails  # noqa: PLC0415

        async with get_db_session() as session:
            # Mark all pending campaign leads as "approved" so
            # send_approved_emails() can pick them up for delivery.
            await session.execute(
                sa_update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_id,
                    CampaignLead.status == "pending",
                )
                .values(status="approved")
            )
            await session.commit()

            result = await send_approved_emails(campaign_id, session)

        artifacts["email_send_result"] = result
        logger.info(
            "email_sending_complete",
            thread_id=state["thread_id"],
            campaign_id=str(campaign_id),
            sent=result.get("sent", 0),
            failed=result.get("failed", 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "email_sending_failed",
            thread_id=state["thread_id"],
            error=str(exc),
        )
        artifacts["email_send_result"] = {"sent": 0, "failed": 0, "error": str(exc)}

    return update_state(
        state,
        current_agent="email_sending",
        next_agent=None,
        status="completed",
        artifacts=artifacts,
    )


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

    Always routes to Planner (even on submission failure -- the bid was
    approved and we should prepare for the project).

    Returns:
        ``"planner_node"`` (always),
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("bid_submission_route_to_end_failed", thread_id=state["thread_id"])
        return END

    logger.info("bid_submission_route_to_planner", thread_id=state["thread_id"])
    return "planner_node"


def _route_after_planner(state: dict[str, Any]) -> str:
    """Route after the Planner Agent.

    Returns:
        ``"dev_node"`` to begin implementation,
        ``"hitl_review_node"`` if HITL plan review is requested (optional),
        ``END`` on failure.
    """
    if state.get("status") == "failed":
        logger.warning("planner_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("requires_hitl"):
        logger.info("planner_route_to_hitl_review", thread_id=state["thread_id"])
        return "hitl_review_node"

    if state.get("next_agent") == "dev":
        logger.info("planner_route_to_dev", thread_id=state["thread_id"])
        return "dev_node"

    logger.warning("planner_route_to_end_no_next", thread_id=state["thread_id"])
    return END


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
    - Request minor revisions from Dev (``next_agent == "dev"``, max 3 cycles)
    - Request major revisions from Planner (``next_agent == "planner"``)
    - Escalate to HITL review (``requires_hitl`` — reject or scope_creep)
    - Fail / terminate (fallback)

    Returns:
        ``"packager_node"`` on approval,
        ``"dev_node"`` on minor revision (up to ``MAX_REVISION_CYCLES``),
        ``"planner_node"`` on major revision (re-decomposition),
        ``"hitl_review_node"`` when human review is needed,
        ``END`` on failure or when revision limit is exceeded.
    """
    if state.get("status") == "failed":
        logger.warning("critic_route_to_end_failed", thread_id=state["thread_id"])
        return END

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

    # MINOR REVISION -- send back to Dev, respecting the cycle limit
    if state.get("next_agent") == "dev":
        artifacts = state.get("artifacts") or {}
        revision_count = artifacts.get("_critic_revision_count", 0)
        if revision_count < MAX_REVISION_CYCLES:
            logger.info(
                "critic_route_to_dev_revision",
                thread_id=state["thread_id"],
                revision_count=revision_count,
                max_revisions=MAX_REVISION_CYCLES,
            )
            return "dev_node"
        # Exceeded revision limit -- escalate to HITL
        logger.warning(
            "critic_revision_limit_exceeded",
            thread_id=state["thread_id"],
            revision_count=revision_count,
        )
        return "hitl_review_node"

    # HITL escalation requested by Critic (reject or scope_creep)
    if state.get("requires_hitl"):
        logger.info("critic_route_to_hitl_review", thread_id=state["thread_id"])
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

    For plan_review approvals, routes to ``"dev_node"`` so the pipeline
    continues with implementation.  For final_review or rejections,
    terminates the graph.

    The ``_hitl_type`` artifact is set by ``resume_from_hitl`` to
    distinguish between plan_review and final_review contexts.

    Returns:
        ``"dev_node"`` when a plan_review was approved,
        ``END`` otherwise.
    """
    artifacts = state.get("artifacts") or {}
    hitl_type = artifacts.get("_hitl_type", "")
    status = state.get("status")

    # Plan review approved -> continue to dev
    if hitl_type == "plan_review" and status == "active":
        logger.info(
            "hitl_review_route_to_dev_plan_approved",
            thread_id=state["thread_id"],
        )
        return "dev_node"

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
    """Route after Outreach: HITL email approval or END."""
    if state.get("status") == "failed":
        return END
    if state.get("requires_hitl"):
        return "hitl_email_node"
    # No emails drafted -- completed without HITL
    return END


def _route_after_hitl_email(state: dict[str, Any]) -> str:
    """Route after email HITL: send emails if approved, else END."""
    if state.get("status") == "failed":
        return END
    artifacts = state.get("artifacts") or {}
    if artifacts.get("emails_approved"):
        return "email_sending_node"
    return END


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------

def build_full_pipeline_graph(checkpointer: Any | None = None) -> CompiledGraph:
    """Build and compile the full Phase 1 pipeline graph.

    The graph topology is::

        scout -> bid -> hitl_bid -> planner -> dev -> content -> design
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

    # Bid Submission -> Planner | END
    graph.add_conditional_edges(
        "bid_submission_node",
        _route_after_bid_submission,
        {
            "planner_node": "planner_node",
            END: END,
        },
    )

    # Planner -> Dev | HITL(review) | END
    graph.add_conditional_edges(
        "planner_node",
        _route_after_planner,
        {
            "dev_node": "dev_node",
            "hitl_review_node": "hitl_review_node",
            END: END,
        },
    )

    # Dev -> Content | END
    graph.add_conditional_edges(
        "dev_node",
        _route_after_dev,
        {
            "content_node": "content_node",
            END: END,
        },
    )

    # Content -> Design | END
    graph.add_conditional_edges(
        "content_node",
        _route_after_content,
        {
            "design_node": "design_node",
            END: END,
        },
    )

    # Design -> Critic | END
    graph.add_conditional_edges(
        "design_node",
        _route_after_design,
        {
            "critic_node": "critic_node",
            END: END,
        },
    )

    # Critic -> Packager | Dev (minor revision) | Planner (major revision) | HITL(review) | END
    graph.add_conditional_edges(
        "critic_node",
        _route_after_critic,
        {
            "packager_node": "packager_node",
            "dev_node": "dev_node",
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

    # HITL(review) -> Dev (plan_review approved) | END
    graph.add_conditional_edges(
        "hitl_review_node",
        _route_after_hitl_review,
        {
            "dev_node": "dev_node",
            END: END,
        },
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

    Flow: GeoScout -> Outreach -> [HITL email approval] -> END

    Args:
        checkpointer: Optional checkpoint saver for persistence.

    Returns:
        A compiled LangGraph.
    """
    graph = StateGraph(dict)

    # Nodes
    graph.add_node("geo_scout_node", geo_scout_node)
    graph.add_node("outreach_node", outreach_node)
    graph.add_node("hitl_email_node", hitl_email_node)
    graph.add_node("email_sending_node", email_sending_node)

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
        {"hitl_email_node": "hitl_email_node", END: END},
    )
    graph.add_conditional_edges(
        "hitl_email_node",
        _route_after_hitl_email,
        {"email_sending_node": "email_sending_node", END: END},
    )
    graph.add_edge("email_sending_node", END)

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
        "planner_node", _route_after_planner,
        {"dev_node": "dev_node", "hitl_review_node": "hitl_review_node", END: END},
    )
    graph.add_conditional_edges(
        "dev_node", _route_after_dev,
        {"content_node": "content_node", END: END},
    )
    graph.add_conditional_edges(
        "content_node", _route_after_content,
        {"design_node": "design_node", END: END},
    )
    graph.add_conditional_edges(
        "design_node", _route_after_design,
        {"critic_node": "critic_node", END: END},
    )
    graph.add_conditional_edges(
        "critic_node", _route_after_critic,
        {
            "packager_node": "packager_node",
            "dev_node": "dev_node",
            "planner_node": "planner_node",
            "hitl_review_node": "hitl_review_node",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "packager_node", _route_after_packager,
        {"hitl_review_node": "hitl_review_node", END: END},
    )
    graph.add_conditional_edges(
        "hitl_review_node", _route_after_hitl_review,
        {"dev_node": "dev_node", END: END},
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

        # Auto-detect HITL type from saved state if not provided.
        resolved_type = hitl_type
        if resolved_type is None:
            current_agent = saved_state.get("current_agent", "")
            if current_agent == "hitl_bid":
                resolved_type = "bid_approval"
            elif current_agent == "hitl_review":
                resolved_type = "final_review"
            elif current_agent == "hitl_email":
                resolved_type = "email_approval"
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
                result: AgentState = await graph.ainvoke(resumed_state, config=config)
                logger.info(
                    "hitl_bid_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result

            # Rejection / edit without continuation -- save and return.
            await checkpointer.aput(
                config, resumed_state, {"source": "hitl_resume", "action": action}
            )
            return resumed_state  # type: ignore[return-value]

        # ----- plan_review ------------------------------------------------
        if resolved_type == "plan_review":
            resumed_state = _apply_plan_review(saved_state, action, hitl_response, thread_id)

            # On approval, re-invoke the pipeline so execution
            # continues from hitl_review_node -> dev_node.
            if action == "approve":
                graph = build_full_pipeline_graph(checkpointer=checkpointer)
                result = await graph.ainvoke(resumed_state, config=config)
                logger.info(
                    "hitl_plan_review_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result  # type: ignore[return-value]

            await checkpointer.aput(
                config, resumed_state, {"source": "hitl_resume", "action": action}
            )

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

            await checkpointer.aput(
                config, resumed_state, {"source": "hitl_resume", "action": action}
            )

            logger.info(
                "hitl_review_resumed",
                thread_id=thread_id,
                action=action,
                new_status=resumed_state["status"],
            )
            return resumed_state  # type: ignore[return-value]

        # ----- email_approval (Pipeline B) -----------------------------------
        if resolved_type == "email_approval":
            resumed_state = _apply_email_approval(
                saved_state, action, hitl_response, thread_id
            )

            # On approval, re-invoke Pipeline B graph so the email_sending_node
            # runs and actually sends the approved emails.
            if action in ("approve", "edit"):
                graph = build_pipeline_b_graph(checkpointer=checkpointer)
                result = await graph.ainvoke(resumed_state, config=config)
                logger.info(
                    "hitl_email_resumed_pipeline_finished",
                    thread_id=thread_id,
                    status=result.get("status"),
                )
                return result  # type: ignore[return-value]

            await checkpointer.aput(
                config, resumed_state, {"source": "hitl_resume", "action": action}
            )

            logger.info(
                "hitl_email_resumed",
                thread_id=thread_id,
                action=action,
                new_status=resumed_state["status"],
            )
            return resumed_state  # type: ignore[return-value]

        # ----- unknown type -----------------------------------------------
        raise ValueError(f"Unknown hitl_type={resolved_type!r}")

    finally:
        if _pool_created and _db_pool is not None:
            await _db_pool.close()


# ---------------------------------------------------------------------------
# Internal HITL response helpers
# ---------------------------------------------------------------------------

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
            errors=[*saved_state.get("errors", []), "HITL: bid rejected by human"],
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
            status="active",
            next_agent="planner",
            current_agent="planner",
            artifacts=artifacts,
        )

    # Unknown action -- treat as approve with a warning.
    logger.warning("hitl_bid_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="active",
        next_agent="planner",
        current_agent="planner",
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
        if edits:
            artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            next_agent="dev",
            current_agent="hitl_review",
            artifacts=artifacts,
        )

    # Unknown action -- treat as approve with a warning.
    logger.warning("hitl_plan_review_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="active",
        next_agent="dev",
        current_agent="hitl_review",
        artifacts=artifacts,
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

    # Unknown action -- treat as approve with a warning.
    logger.warning("hitl_review_unknown_action", action=action, thread_id=thread_id)
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="completed",
        next_agent=None,
    )


def _apply_email_approval(
    saved_state: dict[str, Any],
    action: str,
    hitl_response: dict[str, Any],
    thread_id: str,
) -> AgentState:
    """Apply the human's email-approval decision to the saved state.

    On **approve** the emails are marked as approved for sending.
    On **reject** the campaign is cancelled.
    """
    if action == "approve":
        artifacts = dict(saved_state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            current_agent="hitl_email",
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
                "HITL: outreach emails rejected by human",
            ],
        )

    if action == "edit":
        edits = hitl_response.get("edits", {})
        artifacts = dict(saved_state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        if edits:
            artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits
        return update_state(
            saved_state,  # type: ignore[arg-type]
            requires_hitl=False,
            hitl_request_id=None,
            status="active",
            current_agent="hitl_email",
            next_agent=None,
            artifacts=artifacts,
        )

    # Unknown action -- treat as approve with a warning.
    logger.warning("hitl_email_unknown_action", action=action, thread_id=thread_id)
    artifacts = dict(saved_state.get("artifacts") or {})
    artifacts["emails_approved"] = True
    return update_state(
        saved_state,  # type: ignore[arg-type]
        requires_hitl=False,
        hitl_request_id=None,
        status="active",
        current_agent="hitl_email",
        next_agent=None,
        artifacts=artifacts,
    )
