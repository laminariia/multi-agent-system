"""Service layer for orchestrator operations.

Bridges the REST API controllers with the database for goals and the
shared orchestrator parsers for file-based state (health, vision, logs).
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import OrchestratorGoal
from src.orchestrator.parsers import (
    ORCH_DIR,
    PID_FILE,
    RUNNER_SCRIPT,
    get_runner_log_path,
    is_runner_alive,
    parse_health_report,
    parse_vision_md,
    tail_file,
)

logger = structlog.get_logger(__name__)


def _goal_to_dict(goal: OrchestratorGoal) -> dict:
    """Convert an OrchestratorGoal ORM instance to a plain dict."""
    return {
        "id": goal.goal_id,
        "title": goal.title,
        "priority": goal.priority,
        "category": goal.category,
        "status": goal.status,
        "result": goal.result,
        "completed_at": goal.completed_at.isoformat() if goal.completed_at else None,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
    }


class OrchestratorService:
    """Stateless service encapsulating orchestrator business logic."""

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @staticmethod
    async def get_status(session: AsyncSession) -> dict:
        """Return runner status with goal and health summaries."""
        alive, pid = is_runner_alive()

        # Goal counts from DB
        pending = (await session.execute(
            select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "pending"),
        )).scalar_one()
        completed = (await session.execute(
            select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "completed"),
        )).scalar_one()
        failed = (await session.execute(
            select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "failed"),
        )).scalar_one()

        health = parse_health_report()
        grade = health.get("overall_grade")
        score = health.get("score")

        uptime_seconds = None
        mode = None
        if alive:
            log_path = get_runner_log_path()
            if log_path:
                lines = tail_file(log_path, 50)
                for line in lines:
                    if "Mode:" in line:
                        if m := re.search(r"Mode:\s*(\S+)", line):
                            mode = m.group(1)
                try:
                    mtime = PID_FILE.stat().st_mtime
                    uptime_seconds = int(datetime.now().timestamp() - mtime)
                except OSError:
                    pass

        return {
            "alive": alive,
            "pid": pid,
            "uptime_seconds": uptime_seconds,
            "mode": mode,
            "goals_pending": pending,
            "goals_completed": completed,
            "goals_failed": failed,
            "health_grade": grade,
            "health_score": score,
        }

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    @staticmethod
    def start_runner() -> dict:
        """Start the orchestrator runner as a detached process.

        The agent decides when to finish each session based on work
        completion (code review clean, all goals done, etc.).

        Raises:
            RuntimeError: If the runner is already alive or the script is missing.
        """
        alive, _ = is_runner_alive()
        if alive:
            raise RuntimeError("Orchestrator is already running")

        if not RUNNER_SCRIPT.exists():
            raise FileNotFoundError(f"Runner script not found: {RUNNER_SCRIPT}")

        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(RUNNER_SCRIPT),
            "-Mode", "self-direct",
        ]

        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            ),
        )

        ORCH_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(proc.pid))

        logger.info("orchestrator.started", pid=proc.pid)

        return {
            "status": "started",
            "pid": proc.pid,
            "message": "Orchestrator started in self-direct mode (agent-driven sessions)",
        }

    @staticmethod
    def stop_runner() -> dict:
        """Stop the running orchestrator.

        Raises:
            RuntimeError: If the runner is not running.
        """
        alive, pid = is_runner_alive()
        if not alive:
            # Clean up stale PID file
            PID_FILE.unlink(missing_ok=True)
            raise RuntimeError("Orchestrator is not running")

        try:
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/T", "/F"],  # noqa: S607
                capture_output=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.error("orchestrator.stop_failed", error=str(exc), pid=pid)
            raise RuntimeError(f"Failed to stop runner: {exc}") from exc

        PID_FILE.unlink(missing_ok=True)
        logger.info("orchestrator.stopped", pid=pid)

        return {
            "status": "stopped",
            "message": f"Orchestrator stopped (PID {pid})",
        }

    # ------------------------------------------------------------------
    # Goals (async DB)
    # ------------------------------------------------------------------

    @staticmethod
    async def list_goals(session: AsyncSession, status_filter: str | None = None) -> dict:
        """Return goals with optional status filter."""
        stmt = select(OrchestratorGoal).order_by(OrchestratorGoal.created_at)
        if status_filter:
            stmt = stmt.where(OrchestratorGoal.status == status_filter)
        result = await session.execute(stmt)
        goals = [_goal_to_dict(g) for g in result.scalars().all()]

        # Counts always reflect all goals
        pending = (await session.execute(
            select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "pending"),
        )).scalar_one()
        completed = (await session.execute(
            select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "completed"),
        )).scalar_one()
        failed = (await session.execute(
            select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "failed"),
        )).scalar_one()

        return {
            "goals": goals,
            "total": len(goals),
            "pending": pending,
            "completed": completed,
            "failed": failed,
        }

    @staticmethod
    async def add_goal(
        session: AsyncSession,
        title: str,
        priority: str = "medium",
        category: str = "feature",
    ) -> dict:
        """Add a new goal to the database and return its ID."""
        # Determine next goal_id
        max_num_result = await session.execute(
            select(func.max(OrchestratorGoal.goal_id)),
        )
        max_id = max_num_result.scalar_one_or_none()
        if max_id and (m := re.match(r"g_(\d+)", max_id)):
            next_num = int(m.group(1)) + 1
        else:
            next_num = 1
        new_id = f"g_{next_num:03d}"

        goal = OrchestratorGoal(
            goal_id=new_id,
            title=title,
            priority=priority,
            category=category,
            context="Added via API",
            success_criteria=["Task completed successfully"],
        )
        session.add(goal)
        await session.flush()

        logger.info("orchestrator.goal_added", goal_id=new_id, title=title)
        return {
            "id": new_id,
            "title": title,
            "message": "Goal added successfully",
        }

    @staticmethod
    async def delete_goal(session: AsyncSession, goal_id: str) -> dict:
        """Delete a goal by its human-readable goal_id.

        Raises:
            KeyError: If the goal_id does not exist.
        """
        result = await session.execute(
            select(OrchestratorGoal).where(OrchestratorGoal.goal_id == goal_id),
        )
        goal = result.scalar_one_or_none()
        if goal is None:
            raise KeyError(f"Goal not found: {goal_id}")

        await session.delete(goal)
        await session.flush()

        logger.info("orchestrator.goal_deleted", goal_id=goal_id)
        return {
            "goal_id": goal_id,
            "message": "Goal deleted successfully",
        }

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @staticmethod
    def get_health() -> dict:
        """Return the health report."""
        report = parse_health_report()
        if not report:
            return {
                "overall_grade": "?",
                "score": 0,
                "dimensions": {},
                "problems": [],
            }
        return {
            "overall_grade": report.get("overall_grade", "?"),
            "score": report.get("score", 0),
            "dimensions": {
                k: {"grade": v.get("grade", "?"), "notes": v.get("notes")}
                for k, v in report.get("dimensions", {}).items()
            },
            "problems": report.get("problems", []),
        }

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    @staticmethod
    def get_milestones() -> list[dict]:
        """Return phases with milestones from vision.md."""
        return parse_vision_md()

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    @staticmethod
    def get_logs(n: int = 20, date: str | None = None) -> dict:
        """Return the last N lines of the runner log."""
        log_path = get_runner_log_path(date=date)

        if not log_path or not log_path.exists():
            return {"lines": [], "total": 0, "log_file": None}

        raw_lines = tail_file(log_path, n)

        lines = []
        for line in raw_lines:
            level = "INFO"
            timestamp = None
            if "[ERROR]" in line or "[ERR]" in line:
                level = "ERROR"
            elif "[WARN]" in line:
                level = "WARN"
            if m := re.match(r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]", line):
                timestamp = m.group(1)
            lines.append({"line": line, "level": level, "timestamp": timestamp})

        return {
            "lines": lines,
            "total": len(lines),
            "log_file": log_path.name,
        }
