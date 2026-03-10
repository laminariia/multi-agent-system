"""Task definitions for the background worker queue.

Each task function receives a payload dict and executes the corresponding
pipeline step.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import asyncpg
import structlog

from src.core.config import get_settings
from src.core.database import get_db_session, get_valkey
from src.core.graph import create_graph_with_persistence
from src.core.state import ProjectContext, create_initial_state

logger = structlog.get_logger(__name__)

_PIPELINE_TIMEOUT_SECONDS = int(os.environ.get("PIPELINE_TIMEOUT_SECONDS", "1800"))


async def run_scout_cycle(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a full Scout Agent cycle.

    Returns a summary dict with job IDs found.
    """
    from src.agents.scout import scout_node  # noqa: PLC0415

    user_id = payload.get("user_id") if payload else None
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
        user_id=user_id,
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
        user_id=payload.get("user_id"),
    )

    settings = get_settings()
    valkey = get_valkey()
    db_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=5)

    try:
        graph = create_graph_with_persistence(
            valkey=valkey,
            db_pool=db_pool,
            planner_pipeline=True,
        )
        config = {"configurable": {"thread_id": thread_id}}

        try:
            result = await asyncio.wait_for(
                graph.ainvoke(state, config=config),
                timeout=_PIPELINE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.error(
                "pipeline_timeout",
                project_id=project["project_id"],
                thread_id=thread_id,
                timeout_seconds=_PIPELINE_TIMEOUT_SECONDS,
            )
            # Create HITL alert for pipeline timeout (P3.14)
            try:
                import uuid as _uuid  # noqa: PLC0415

                from src.core.models import HITLQueue  # noqa: PLC0415

                async with get_db_session() as session:
                    hitl = HITLQueue(
                        id=_uuid.uuid4(),
                        type="pipeline_timeout",
                        priority="urgent",
                        title=f"Pipeline timed out for project {project['project_id']}",
                        payload={
                            "project_id": project["project_id"],
                            "thread_id": thread_id,
                            "timeout_seconds": _PIPELINE_TIMEOUT_SECONDS,
                        },
                        available_actions=["retry", "cancel"],
                        status="pending",
                    )
                    session.add(hitl)
            except Exception:  # noqa: BLE001
                logger.exception("pipeline_timeout_hitl_creation_failed")

            return {
                "task": "project_pipeline",
                "project_id": project["project_id"],
                "thread_id": thread_id,
                "status": "timeout",
            }
    finally:
        await db_pool.close()

    final_status = result.get("status", "unknown")
    logger.info(
        "project_pipeline_complete",
        project_id=project["project_id"],
        thread_id=thread_id,
        status=final_status,
    )

    # P3.13: Record decision pattern for terminal pipeline states.
    await _record_decision_pattern(result)

    return {
        "task": "project_pipeline",
        "project_id": project["project_id"],
        "thread_id": thread_id,
        "status": final_status,
    }


async def _record_decision_pattern(result: dict[str, Any]) -> None:
    """Record a decision pattern from a completed pipeline (P3.13).

    Best-effort: errors are logged and swallowed so they never
    affect the pipeline result returned to the caller.
    """
    try:
        from src.core.container import get_container  # noqa: PLC0415
        from src.core.decision_memory import extract_pattern_from_state  # noqa: PLC0415

        dm = get_container().decision_memory
        if dm is None:
            return

        pattern = extract_pattern_from_state(result)
        if pattern is None:
            return  # Non-terminal status — nothing to record.

        await dm.record_outcome(pattern)
        logger.info(
            "decision_pattern_recorded",
            project_type=pattern.project_type,
            outcome=pattern.outcome,
        )
    except Exception:  # noqa: BLE001
        logger.debug("decision_pattern_recording_failed", exc_info=True)


async def run_bid_generation(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate bids for a list of qualified job IDs.

    Expects payload with: job_ids (list of UUID strings).
    """
    from src.agents.bid import bid_node  # noqa: PLC0415

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
        user_id=payload.get("user_id"),
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
        user_id=payload.get("user_id"),
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
