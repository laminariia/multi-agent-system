"""Task definitions for the background worker queue.

Each task function receives a payload dict and executes the corresponding
pipeline step.
"""
from __future__ import annotations

from typing import Any

import structlog

from src.core.state import ProjectContext, create_initial_state

logger = structlog.get_logger(__name__)


async def run_scout_cycle(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a full Scout Agent cycle.

    Returns a summary dict with job IDs found.
    """
    from src.agents.scout import scout_node

    state = create_initial_state(
        project=ProjectContext(
            project_id="scout-queue-task",
            job_id="",
            platform=payload.get("platform", "all") if payload else "all",
            client={},
            requirements="Queued scout cycle",
            budget=0,
            deadline=None,
        ),
        first_agent="scout",
    )

    result = await scout_node(state)
    scout_artifacts = (result.get("artifacts") or {}).get("scout", [])
    logger.info("scout_cycle_task_complete", jobs_found=len(scout_artifacts))

    return {
        "task": "scout_cycle",
        "jobs_found": len(scout_artifacts),
        "job_ids": scout_artifacts[:10],  # Limit logged IDs
    }


async def run_project_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the implementation pipeline for a won project.

    Starts at the Planner node (skipping Scout/Bid/HITL which have
    already completed by the time a project is won).

    Uses a checkpointed graph so that HITL pauses (plan_review,
    final_review) can be resumed later via ``resume_from_hitl``.

    Expects payload with: project_id, job_id, platform, requirements, budget.
    """
    import asyncpg  # noqa: PLC0415

    from src.core.config import get_settings  # noqa: PLC0415
    from src.core.database import get_valkey  # noqa: PLC0415
    from src.core.graph import create_graph_with_persistence  # noqa: PLC0415

    project = ProjectContext(
        project_id=payload["project_id"],
        job_id=payload.get("job_id", ""),
        platform=payload.get("platform", "freelancer"),
        client=payload.get("client", {}),
        requirements=payload.get("requirements", ""),
        budget=payload.get("budget", 0),
        deadline=payload.get("deadline"),
    )

    thread_id = f"pipeline-{project['project_id']}"
    state = create_initial_state(
        project=project,
        first_agent="planner",
        thread_id=thread_id,
    )

    settings = get_settings()
    valkey = get_valkey()
    db_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=5)

    try:
        graph = create_graph_with_persistence(
            valkey=valkey, db_pool=db_pool, planner_pipeline=True,
        )
        config = {"configurable": {"thread_id": thread_id}}
        result = await graph.ainvoke(state, config=config)
    finally:
        await db_pool.close()

    final_status = result.get("status", "unknown")
    logger.info(
        "project_pipeline_complete",
        project_id=project["project_id"],
        thread_id=thread_id,
        status=final_status,
    )

    return {
        "task": "project_pipeline",
        "project_id": project["project_id"],
        "thread_id": thread_id,
        "status": final_status,
    }


async def run_bid_generation(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate bids for a list of qualified job IDs.

    Expects payload with: job_ids (list of UUID strings).
    """
    from src.agents.bid import bid_node

    state = create_initial_state(
        project=ProjectContext(
            project_id="bid-task",
            job_id="",
            platform="all",
            client={},
            requirements="",
            budget=0,
            deadline=None,
        ),
        first_agent="bid",
    )

    state["artifacts"] = {"scout": payload.get("job_ids", [])}

    result = await bid_node(state)
    bid_artifacts = (result.get("artifacts") or {}).get("bid", [])

    return {
        "task": "bid_generation",
        "bids_created": len(bid_artifacts),
    }


async def run_pipeline_b_scan(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a Pipeline B geo-scan for a given city.

    Expects payload with:
        city (str, required): City name to scan for offline businesses.
        thread_id (str, optional): Override for the LangGraph thread ID.

    Raises:
        ValueError: If ``city`` is missing or empty in the payload.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from src.core.graph import build_pipeline_b_graph  # noqa: PLC0415

    city = payload.get("city", "")
    if not city:
        raise ValueError("pipeline_b_scan requires 'city' in payload")

    thread_id = payload.get("thread_id") or None

    project = ProjectContext(
        project_id=f"pipeline_b_{city[:20]}",
        job_id="",
        platform="outreach",
        client={},
        requirements=city,
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )

    state = create_initial_state(
        project=project,
        first_agent="geoscout",
        thread_id=thread_id,
    )
    # Set city in artifacts for the geo_scout agent
    state["artifacts"] = {"_scan_city": city}

    graph = build_pipeline_b_graph()
    result = await graph.ainvoke(state)

    final_status = result.get("status", "unknown")
    logger.info(
        "pipeline_b_scan_complete",
        city=city,
        status=final_status,
    )

    return {
        "task": "pipeline_b_scan",
        "city": city,
        "status": final_status,
    }


# Task dispatcher
TASK_REGISTRY: dict[str, Any] = {
    "scout_cycle": run_scout_cycle,
    "project_pipeline": run_project_pipeline,
    "bid_generation": run_bid_generation,
    "pipeline_b_scan": run_pipeline_b_scan,
}


async def dispatch_task(task_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch a task by type name.

    Returns the task result dict.

    Raises:
        ValueError: If the task type is not registered.
    """
    handler = TASK_REGISTRY.get(task_type)
    if handler is None:
        raise ValueError(f"Unknown task type: {task_type}")

    logger.info("task_dispatch", task_type=task_type)
    return await handler(payload or {})
