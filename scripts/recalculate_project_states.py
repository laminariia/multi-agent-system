#!/usr/bin/env python3
"""Recalculate project states from LangGraph checkpoints.

Disaster recovery tool that rebuilds project/task/artifact state from the
LangGraph checkpoint history.  Useful after database corruption or partial
restore where the ``projects`` / ``tasks`` tables are out of sync with the
actual pipeline execution state stored in checkpoints.

Usage:
    python scripts/recalculate_project_states.py [OPTIONS]

Options:
    --dry-run       Show changes without applying them
    --thread-id ID  Recalculate a single thread (default: all active threads)
    --verbose       Enable debug-level logging
    --help          Show this help message

Environment:
    DATABASE_URL    PostgreSQL connection string (required)

Spec: docs/Full_work/specs/deploy-spec.md "Scenario 2: Database Corruption"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from src.core.models import LanggraphCheckpoint, Project

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class StateChange:
    """A single field change detected during recalculation."""

    table: str
    record_id: str
    field: str
    old_value: Any
    new_value: Any


@dataclass
class RecalculationResult:
    """Outcome of recalculating a single thread's project state."""

    thread_id: str
    status: str  # "ok", "updated", "error", "skipped"
    changes: list[StateChange] = field(default_factory=list)
    error: str | None = None


@dataclass
class RecalculationReport:
    """Summary report for the full recalculation run."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    dry_run: bool = False
    total_threads: int = 0
    ok_count: int = 0
    updated_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    results: list[RecalculationResult] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return sum(len(r.changes) for r in self.results)


# ---------------------------------------------------------------------------
# State extraction from checkpoint
# ---------------------------------------------------------------------------

# Canonical mapping: checkpoint state_data keys -> projects table columns
_PROJECT_FIELD_MAP: dict[str, str] = {
    "status": "status",
    "current_agent": "status",  # maps to project status derivation
}

# Valid project statuses (from models.py Project.status)
VALID_PROJECT_STATUSES = frozenset(
    {"planning", "in_progress", "review", "revision", "completed", "cancelled", "failed"}
)

# Agent -> implied project status
AGENT_TO_PROJECT_STATUS: dict[str, str] = {
    "scout": "planning",
    "bid": "planning",
    "planner": "planning",
    "dev": "in_progress",
    "content": "in_progress",
    "design": "in_progress",
    "critic": "review",
    "packager": "completed",
}


def derive_project_status(state: dict[str, Any]) -> str:
    """Derive the project status from checkpoint state.

    Uses the pipeline status and current agent to determine what the
    project status should be.
    """
    pipeline_status = state.get("status", "active")

    if pipeline_status == "completed":
        return "completed"
    if pipeline_status == "failed":
        return "failed"
    if pipeline_status == "paused":
        # HITL pending -> keep current status or mark as review
        return "review"

    current_agent = state.get("current_agent", "")
    return AGENT_TO_PROJECT_STATUS.get(current_agent, "in_progress")


def extract_progress(state: dict[str, Any]) -> int:
    """Estimate project progress percentage from checkpoint state.

    Based on the agent sequence position relative to the total pipeline.
    """
    pipeline_status = state.get("status", "active")
    if pipeline_status == "completed":
        return 100
    if pipeline_status == "failed":
        return 0

    current_index = state.get("current_sequence_index", 0)
    sequence = state.get("agent_sequence", [])
    total = len(sequence) if sequence else 8  # default pipeline length

    if total == 0:
        return 0

    # Clamp to 0-99 (100 = completed only)
    return min(int((current_index / total) * 100), 99)


def extract_revision_count(state: dict[str, Any]) -> int:
    """Count revision cycles from checkpoint state."""
    return state.get("recovery_attempted", 0)


def extract_artifacts_summary(state: dict[str, Any]) -> dict[str, list[str]]:
    """Extract artifact keys from checkpoint state."""
    artifacts = state.get("artifacts", {})
    if isinstance(artifacts, dict):
        return {k: v if isinstance(v, list) else [str(v)] for k, v in artifacts.items()}
    return {}


# ---------------------------------------------------------------------------
# Core recalculation engine
# ---------------------------------------------------------------------------


async def load_checkpoint_state(
    session: Any,
    thread_id: str,
) -> dict[str, Any] | None:
    """Load the latest checkpoint state for a thread.

    Returns None if no checkpoint exists.
    """

    stmt = (
        select(LanggraphCheckpoint)
        .where(LanggraphCheckpoint.thread_id == thread_id)
        .order_by(LanggraphCheckpoint.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalars().first()

    if row is None:
        return None

    return dict(row.state_data) if row.state_data else {}


async def load_active_threads(session: Any) -> list[str]:
    """Load all thread IDs with active/paused checkpoints."""

    stmt = (
        select(LanggraphCheckpoint.thread_id)
        .where(
            LanggraphCheckpoint.status.in_(["active", "paused", "failed"]),
        )
        .distinct()
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def load_project_by_thread(session: Any, thread_id: str) -> Any | None:
    """Find a project linked to a pipeline thread.

    Projects are linked via ``bid -> job -> project`` or directly via
    the project_id in checkpoint state_data.
    """

    # First: try to find project by checking state_data for project_id
    state = await load_checkpoint_state(session, thread_id)
    if state is None:
        return None

    project_ctx = state.get("project", {})
    project_id = project_ctx.get("project_id") if isinstance(project_ctx, dict) else None

    if project_id:
        stmt = select(Project).where(Project.id == project_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    return None


async def recalculate_thread(
    session: Any,
    thread_id: str,
    *,
    dry_run: bool = False,
) -> RecalculationResult:
    """Recalculate project state for a single pipeline thread.

    Compares the current project record with what the checkpoint says
    the state should be. Reports and optionally applies differences.
    """
    result = RecalculationResult(thread_id=thread_id, status="ok")

    try:
        # 1. Load checkpoint state
        state = await load_checkpoint_state(session, thread_id)
        if state is None:
            result.status = "skipped"
            result.error = "No checkpoint found"
            logger.info("recalculate.no_checkpoint", thread_id=thread_id)
            return result

        # 2. Load linked project
        project = await load_project_by_thread(session, thread_id)
        if project is None:
            result.status = "skipped"
            result.error = "No linked project found"
            logger.info("recalculate.no_project", thread_id=thread_id)
            return result

        # 3. Derive expected values from checkpoint
        expected_status = derive_project_status(state)
        expected_progress = extract_progress(state)
        expected_revisions = extract_revision_count(state)

        # 4. Compare with current project record
        current_status = getattr(project, "status", None)
        current_progress = getattr(project, "progress", None)
        current_revisions = getattr(project, "revision_count", None)

        changes: list[StateChange] = []

        if current_status != expected_status and expected_status in VALID_PROJECT_STATUSES:
            changes.append(
                StateChange(
                    table="projects",
                    record_id=str(project.id),
                    field="status",
                    old_value=current_status,
                    new_value=expected_status,
                )
            )

        if current_progress != expected_progress:
            changes.append(
                StateChange(
                    table="projects",
                    record_id=str(project.id),
                    field="progress",
                    old_value=current_progress,
                    new_value=expected_progress,
                )
            )

        if current_revisions != expected_revisions:
            changes.append(
                StateChange(
                    table="projects",
                    record_id=str(project.id),
                    field="revision_count",
                    old_value=current_revisions,
                    new_value=expected_revisions,
                )
            )

        if not changes:
            result.status = "ok"
            logger.info("recalculate.no_changes", thread_id=thread_id)
            return result

        result.changes = changes
        result.status = "updated"

        # 5. Apply changes (unless dry run)
        if not dry_run:
            for change in changes:
                setattr(project, change.field, change.new_value)
                logger.info(
                    "recalculate.applied",
                    thread_id=thread_id,
                    field=change.field,
                    old=change.old_value,
                    new=change.new_value,
                )
            project.updated_at = datetime.now(UTC)
            await session.flush()
        else:
            for change in changes:
                logger.info(
                    "recalculate.would_apply",
                    thread_id=thread_id,
                    field=change.field,
                    old=change.old_value,
                    new=change.new_value,
                )

        return result

    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
        logger.error(
            "recalculate.error",
            thread_id=thread_id,
            error=str(exc),
            exc_info=True,
        )
        return result


async def recalculate_all(
    session: Any,
    *,
    dry_run: bool = False,
    thread_ids: list[str] | None = None,
) -> RecalculationReport:
    """Recalculate project states for all active threads (or specified ones).

    Args:
        session: Async SQLAlchemy session.
        dry_run: If True, report changes without applying.
        thread_ids: Specific threads to process. None = all active threads.

    Returns:
        RecalculationReport with per-thread results and summary.
    """
    report = RecalculationReport(dry_run=dry_run)

    if thread_ids is None:
        thread_ids = await load_active_threads(session)

    report.total_threads = len(thread_ids)
    logger.info("recalculate.start", total_threads=report.total_threads, dry_run=dry_run)

    for tid in thread_ids:
        result = await recalculate_thread(session, tid, dry_run=dry_run)
        report.results.append(result)

        if result.status == "ok":
            report.ok_count += 1
        elif result.status == "updated":
            report.updated_count += 1
        elif result.status == "error":
            report.error_count += 1
        elif result.status == "skipped":
            report.skipped_count += 1

    if not dry_run:
        await session.commit()

    report.finished_at = datetime.now(UTC)

    logger.info(
        "recalculate.complete",
        total=report.total_threads,
        ok=report.ok_count,
        updated=report.updated_count,
        errors=report.error_count,
        skipped=report.skipped_count,
        total_changes=report.total_changes,
        dry_run=dry_run,
    )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Recalculate MAS project states from LangGraph checkpoints.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/recalculate_project_states.py --dry-run\n"
            "  python scripts/recalculate_project_states.py --thread-id abc123\n"
            "  python scripts/recalculate_project_states.py --verbose\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show changes without applying them",
    )
    parser.add_argument(
        "--thread-id",
        type=str,
        default=None,
        help="Recalculate a single thread (default: all active threads)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable debug-level logging",
    )
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    """Async entry point."""
    import structlog

    log_level = "DEBUG" if args.verbose else "INFO"
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.get_level_from_name(log_level),
        ),
    )

    from src.core.database import get_db_session

    thread_ids = [args.thread_id] if args.thread_id else None

    async with get_db_session() as session:
        report = await recalculate_all(
            session,
            dry_run=args.dry_run,
            thread_ids=thread_ids,
        )

    # Print summary
    prefix = "[DRY RUN] " if report.dry_run else ""
    print(f"\n{prefix}=== Recalculation Report ===")
    print(f"Threads processed: {report.total_threads}")
    print(f"  OK (no changes):  {report.ok_count}")
    print(f"  Updated:          {report.updated_count}")
    print(f"  Skipped:          {report.skipped_count}")
    print(f"  Errors:           {report.error_count}")
    print(f"  Total changes:    {report.total_changes}")

    if report.total_changes > 0:
        print("\nChanges:")
        for result in report.results:
            for change in result.changes:
                print(
                    f"  [{result.thread_id[:12]}] "
                    f"{change.table}.{change.field}: "
                    f"{change.old_value} -> {change.new_value}"
                )

    if report.error_count > 0:
        print("\nErrors:")
        for result in report.results:
            if result.error:
                print(f"  [{result.thread_id[:12]}] {result.error}")

    return 1 if report.error_count > 0 else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
