"""Pipeline execution progress tracking via Valkey.

Stores real-time progress of pipeline execution so the dashboard can
display which agent is currently running, how many are completed, and
what the overall status is.

Key pattern: ``pipeline-progress:{thread_id}``
TTL: 24 hours (auto-cleanup after pipeline ends)

All operations are best-effort — a Valkey failure never blocks agent
execution.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Valkey key prefix
_KEY_PREFIX = "pipeline-progress"

# TTL in seconds (24 hours)
_PROGRESS_TTL_SECONDS = 86400

# Fixed agents that always run outside the dynamic sequence
_FIXED_AGENTS = {"planner", "critic", "packager"}


class PipelineProgressTracker:
    """Read/write pipeline execution progress in Valkey."""

    def __init__(self, valkey: Any) -> None:
        self._valkey = valkey

    def _key(self, thread_id: str) -> str:
        return f"{_KEY_PREFIX}:{thread_id}"

    def _calculate_total(self, sequence: list[str]) -> int:
        """Total agents = dynamic sequence + planner + critic + packager.

        For empty sequences (consulting projects), total = planner + packager.
        """
        if not sequence:
            return 2  # planner + packager
        return len(sequence) + 3  # sequence + planner + critic + packager

    async def publish_agent_started(
        self,
        agent_name: str,
        state: dict[str, Any],
    ) -> None:
        """Record that an agent has started execution."""
        try:
            thread_id = state["thread_id"]
            sequence = state.get("agent_sequence", [])
            index = state.get("current_sequence_index", 0)

            existing = await self._get_raw(thread_id)
            completed = existing.get("completed_agents", []) if existing else []
            started_at = existing.get("started_at") if existing else None

            if not started_at:
                started_at = datetime.now(tz=UTC).isoformat()

            progress = {
                "thread_id": thread_id,
                "agent_sequence": sequence,
                "current_agent": agent_name,
                "current_index": index,
                "total_agents": self._calculate_total(sequence),
                "completed_agents": completed,
                "status": "running",
                "started_at": started_at,
                "updated_at": datetime.now(tz=UTC).isoformat(),
            }

            await self._valkey.set(
                self._key(thread_id),
                json.dumps(progress),
                ex=_PROGRESS_TTL_SECONDS,
            )
        except Exception:
            logger.debug("progress_publish_start_failed", agent=agent_name, exc_info=True)

    async def publish_agent_completed(
        self,
        agent_name: str,
        state: dict[str, Any],
    ) -> None:
        """Record that an agent has finished execution successfully."""
        try:
            thread_id = state["thread_id"]
            existing = await self._get_raw(thread_id)

            if existing:
                completed = list(existing.get("completed_agents", []))
                if agent_name not in completed:
                    completed.append(agent_name)
                existing["completed_agents"] = completed
                existing["updated_at"] = datetime.now(tz=UTC).isoformat()

                await self._valkey.set(
                    self._key(thread_id),
                    json.dumps(existing),
                    ex=_PROGRESS_TTL_SECONDS,
                )
        except Exception:
            logger.debug("progress_publish_complete_failed", agent=agent_name, exc_info=True)

    async def publish_status(
        self,
        thread_id: str,
        status: str,
        current_agent: str | None = None,
    ) -> None:
        """Update the pipeline status (paused, failed, completed)."""
        try:
            existing = await self._get_raw(thread_id)
            if existing:
                existing["status"] = status
                if current_agent:
                    existing["current_agent"] = current_agent
                existing["updated_at"] = datetime.now(tz=UTC).isoformat()

                await self._valkey.set(
                    self._key(thread_id),
                    json.dumps(existing),
                    ex=_PROGRESS_TTL_SECONDS,
                )
        except Exception:
            logger.debug("progress_publish_status_failed", status=status, exc_info=True)

    async def get_progress(self, thread_id: str) -> dict[str, Any] | None:
        """Read current progress for a pipeline thread."""
        try:
            return await self._get_raw(thread_id)
        except Exception:
            logger.debug("progress_get_failed", thread_id=thread_id, exc_info=True)
            return None

    async def _get_raw(self, thread_id: str) -> dict[str, Any] | None:
        """Fetch and parse progress JSON from Valkey."""
        raw = await self._valkey.get(self._key(thread_id))
        if raw is None:
            return None
        return json.loads(raw)

    def calculate_progress_pct(self, progress: dict[str, Any]) -> int:
        """Calculate the completion percentage from a progress dict.

        Args:
            progress: Progress data with ``completed_agents`` and ``total_agents``.

        Returns:
            Integer percentage (0-100).
        """
        total = progress.get("total_agents", 0)
        if total <= 0:
            return 0
        completed = len(progress.get("completed_agents", []))
        return int(completed / total * 100)

    def build_ws_event(self, progress: dict[str, Any]) -> dict[str, Any]:
        """Build a WebSocket event payload for the dashboard progress bar.

        The event type is ``pipeline:progress`` and contains all fields
        the dashboard needs to render a real-time progress indicator.

        Args:
            progress: Progress data from Valkey.

        Returns:
            Dict suitable for ``publish_event(channels, "pipeline:progress", event)``.
        """
        return {
            "type": "pipeline:progress",
            "thread_id": progress.get("thread_id", ""),
            "current_agent": progress.get("current_agent"),
            "completed_agents": progress.get("completed_agents", []),
            "total_agents": progress.get("total_agents", 0),
            "progress_pct": self.calculate_progress_pct(progress),
        }


def get_progress_tracker() -> PipelineProgressTracker | None:
    """Get a PipelineProgressTracker using the global Valkey connection.

    Returns None if Valkey is not available.
    """
    try:
        from src.core.database import get_valkey  # noqa: PLC0415

        valkey = get_valkey()
        return PipelineProgressTracker(valkey)
    except Exception:
        logger.debug("progress_tracker_unavailable", exc_info=True)
        return None
