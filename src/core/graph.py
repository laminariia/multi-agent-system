"""LangGraph StateGraph wiring for the Scout -> Bid -> HITL pipeline.

Defines the Phase 1 graph: Scout discovers qualified jobs, Bid generates
proposals, and the HITL node pauses execution for human approval before
any bid is submitted.

Usage::

    # One-shot (no persistence)
    graph = build_scout_bid_graph()
    result = await graph.ainvoke(initial_state)

    # With persistence (Valkey + PostgreSQL)
    graph = create_graph_with_persistence(valkey, db_pool)
    result = await graph.ainvoke(initial_state, config={"configurable": {"thread_id": "abc"}})

    # Convenience runner
    result = await run_scout_bid_pipeline(project_context)

    # Resume after HITL approval
    result = await resume_from_hitl(thread_id, {"action": "approve", "bid_ids": [...]})
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import structlog
from langgraph.graph import END, StateGraph
from langgraph.graph.graph import CompiledGraph
from redis.asyncio import Redis as AsyncRedis

from src.agents.bid import bid_node
from src.agents.scout import scout_node
from src.core.checkpoints import HybridCheckpointSaver
from src.core.config import get_settings
from src.core.state import AgentState, ProjectContext, create_initial_state, update_state

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

async def hitl_node(state: AgentState) -> AgentState:
    """Human-in-the-Loop interrupt node.

    This node marks the workflow as paused and returns the state unchanged.
    The actual "interrupt" behaviour is external: the caller inspects
    ``state["requires_hitl"]`` / ``state["status"] == "paused"`` and stops
    streaming.  Resumption is handled by loading the checkpoint and
    re-injecting the state via :func:`resume_from_hitl`.

    In later phases this node may fan out to additional agents (e.g.
    Planner, Dev, Content) after the human approves.
    """
    logger.info(
        "hitl_node_entered",
        thread_id=state["thread_id"],
        hitl_request_id=state.get("hitl_request_id"),
        status=state["status"],
    )

    # Ensure the paused flag is set (bid_node already does this, but be
    # defensive in case the node is reached via a different path).
    if state["status"] != "paused":
        return update_state(state, status="paused", requires_hitl=True)

    return state


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def _route_after_scout(state: AgentState) -> str:
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


def _route_after_bid(state: AgentState) -> str:
    """Determine the next node after the Bid Agent completes.

    Returns:
        ``"hitl_node"`` when HITL approval is required (the normal path),
        ``END`` on failure or when no HITL is needed (defensive).
    """
    if state.get("status") == "failed":
        logger.warning("bid_route_to_end_failed", thread_id=state["thread_id"])
        return END

    if state.get("requires_hitl"):
        logger.info("bid_route_to_hitl", thread_id=state["thread_id"])
        return "hitl_node"

    # Defensive: Bid Agent should ALWAYS require HITL, but handle gracefully.
    logger.warning(
        "bid_route_to_end_no_hitl",
        thread_id=state["thread_id"],
        msg="Bid completed without HITL flag -- this is unexpected",
    )
    return END


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_scout_bid_graph(checkpointer: Any | None = None) -> CompiledGraph:
    """Build and compile the Scout -> Bid -> HITL StateGraph.

    Args:
        checkpointer: An optional ``BaseCheckpointSaver`` instance (e.g.
            :class:`HybridCheckpointSaver`).  When provided the graph
            persists state after each node, enabling pause/resume for HITL.

    Returns:
        A compiled LangGraph ready for ``ainvoke`` / ``astream``.
    """
    graph = StateGraph(AgentState)

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
        _route_after_bid,
        {
            "hitl_node": "hitl_node",
            END: END,
        },
    )

    # -- HITL node always terminates the graph (Phase 1) --------------------
    graph.add_edge("hitl_node", END)

    # -- Compile ------------------------------------------------------------
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("scout_bid_graph_compiled", has_checkpointer=checkpointer is not None)
    return compiled


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------

def create_graph_with_persistence(
    valkey: AsyncRedis,
    db_pool: asyncpg.Pool,
) -> CompiledGraph:
    """Build the Scout -> Bid -> HITL graph backed by hybrid persistence.

    Args:
        valkey: An ``AsyncRedis`` client connected to Valkey.
        db_pool: An ``asyncpg.Pool`` connected to PostgreSQL.

    Returns:
        A compiled graph with :class:`HybridCheckpointSaver` attached.
    """
    checkpointer = HybridCheckpointSaver(valkey=valkey, db_pool=db_pool)
    return build_scout_bid_graph(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Convenience runners
# ---------------------------------------------------------------------------

async def run_scout_bid_pipeline(
    project_context: ProjectContext,
    thread_id: str | None = None,
) -> AgentState:
    """Run the full Scout -> Bid -> HITL pipeline end-to-end.

    This is a convenience wrapper intended for scripts, tests, and one-shot
    CLI invocations.  It builds a graph **without** persistence.  For
    production use, prefer :func:`create_graph_with_persistence` and invoke
    the graph directly so that checkpoints are saved.

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

    logger.info("pipeline_start", thread_id=tid)
    result: AgentState = await graph.ainvoke(initial_state)
    logger.info(
        "pipeline_finished",
        thread_id=tid,
        status=result.get("status"),
        requires_hitl=result.get("requires_hitl"),
    )
    return result


async def resume_from_hitl(
    thread_id: str,
    hitl_response: dict[str, Any],
    *,
    valkey: AsyncRedis | None = None,
    db_pool: asyncpg.Pool | None = None,
) -> AgentState:
    """Resume a paused workflow after HITL approval.

    Loads the latest checkpoint for *thread_id*, merges the human's
    response into the state, and re-invokes the graph.  In Phase 1 the
    graph immediately reaches END after the HITL node, but later phases
    will route to downstream agents (Planner, Dev, etc.).

    Args:
        thread_id: The workflow thread to resume.
        hitl_response: A dict containing the human's decision, e.g.::

            {
                "action": "approve",  # or "reject", "edit"
                "bid_ids": ["..."],
                "edits": {...},       # optional modifications
            }

        valkey: Async Valkey client.  When ``None`` the default client
            from :func:`~src.core.database.get_valkey` is used.
        db_pool: Async PostgreSQL pool.  When ``None`` a temporary pool
            is created from settings (caller should provide one in
            production).

    Returns:
        The final ``AgentState`` after the resumed graph terminates.

    Raises:
        ValueError: If no checkpoint is found for the given *thread_id*.
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
        build_scout_bid_graph(checkpointer=checkpointer)

        # Load the latest checkpoint for this thread.
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            raise ValueError(f"No checkpoint found for thread_id={thread_id!r}")

        # Reconstruct the state from the checkpoint and apply the HITL response.
        saved_state: dict[str, Any] = checkpoint_tuple.checkpoint
        action = hitl_response.get("action", "approve")

        if action == "approve":
            resumed_state = update_state(
                saved_state,  # type: ignore[arg-type]
                requires_hitl=False,
                hitl_request_id=None,
                status="completed",  # Phase 1: approve -> completed
                next_agent=None,
            )
        elif action == "reject":
            resumed_state = update_state(
                saved_state,  # type: ignore[arg-type]
                requires_hitl=False,
                hitl_request_id=None,
                status="failed",
                next_agent=None,
                errors=[*saved_state.get("errors", []), "HITL: bid rejected by human"],
            )
        elif action == "edit":
            # Apply human edits to the proposal, then mark as completed.
            edits = hitl_response.get("edits", {})
            artifacts = dict(saved_state.get("artifacts") or {})
            if edits:
                artifacts["hitl_edits"] = [edits] if not isinstance(edits, list) else edits
            resumed_state = update_state(
                saved_state,  # type: ignore[arg-type]
                requires_hitl=False,
                hitl_request_id=None,
                status="completed",
                next_agent=None,
                artifacts=artifacts,
            )
        else:
            logger.warning("hitl_unknown_action", action=action, thread_id=thread_id)
            resumed_state = update_state(
                saved_state,  # type: ignore[arg-type]
                requires_hitl=False,
                hitl_request_id=None,
                status="completed",
                next_agent=None,
            )

        logger.info(
            "hitl_resumed",
            thread_id=thread_id,
            action=action,
            new_status=resumed_state["status"],
        )

        # In Phase 1, the graph ends after HITL, so we simply return the
        # updated state.  In later phases we would re-invoke the graph:
        #   result = await graph.ainvoke(resumed_state, config=config)
        #   return result
        #
        # For now, save the updated state to the checkpointer and return.
        await checkpointer.aput(config, resumed_state, {"source": "hitl_resume", "action": action})

        return resumed_state  # type: ignore[return-value]

    finally:
        if _pool_created and _db_pool is not None:
            await _db_pool.close()
