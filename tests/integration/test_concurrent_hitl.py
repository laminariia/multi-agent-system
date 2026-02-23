"""Integration tests for concurrent HITL decision handling.

Target: src/core/graph.py:1262-1481 — resume_from_hitl().
Pattern: tests/integration/test_pipeline_a_hitl.py, tests/unit/test_hitl_resume.py.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.state import create_initial_state

pytestmark = pytest.mark.asyncio


def _state(**overrides: Any) -> dict[str, Any]:
    """Build a paused HITL state dict."""
    project = {
        "project_id": "p-conc",
        "job_id": "j-conc",
        "platform": "freelancer",
        "client": {"name": "ConcTest"},
        "requirements": "build api",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent="scout", thread_id="t-conc")
    s.update(overrides)
    return s


def _mock_checkpoint(saved_state: dict[str, Any]) -> MagicMock:
    """Create a mock checkpoint tuple wrapping the given state."""
    ct = MagicMock()
    ct.checkpoint = saved_state
    return ct


# ===== P4-1: Concurrent approve same thread =================================


async def test_concurrent_approve_same_thread():
    """Two concurrent approvals for the same thread → ainvoke called twice."""
    saved = _state(status="paused", current_agent="hitl_bid")

    mock_checkpointer = AsyncMock()
    # Return fresh copy each call to avoid mutation interference
    mock_checkpointer.aget_tuple = AsyncMock(side_effect=lambda _: _mock_checkpoint({**saved}))

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        patch("src.core.graph.build_full_pipeline_graph", return_value=mock_graph),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        pool = AsyncMock()
        pool.close = AsyncMock()

        results = await asyncio.gather(
            resume_from_hitl(
                "t-conc",
                {"action": "approve"},
                hitl_type="bid_approval",
                valkey=MagicMock(),
                db_pool=pool,
            ),
            resume_from_hitl(
                "t-conc",
                {"action": "approve"},
                hitl_type="bid_approval",
                valkey=MagicMock(),
                db_pool=pool,
            ),
        )

        assert all(r["status"] == "completed" for r in results)
        assert mock_graph.ainvoke.await_count == 2


# ===== P4-2: Concurrent approve and reject ==================================


async def test_concurrent_approve_and_reject():
    """approve + reject for same thread → one completed, one failed."""
    saved = _state(status="paused", current_agent="hitl_review")

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(side_effect=lambda _: _mock_checkpoint({**saved}))
    mock_checkpointer.aput = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        pool = AsyncMock()
        pool.close = AsyncMock()

        results = await asyncio.gather(
            resume_from_hitl(
                "t-conc",
                {"action": "approve"},
                hitl_type="final_review",
                valkey=MagicMock(),
                db_pool=pool,
            ),
            resume_from_hitl(
                "t-conc",
                {"action": "reject"},
                hitl_type="final_review",
                valkey=MagicMock(),
                db_pool=pool,
            ),
        )

        statuses = sorted(r["status"] for r in results)
        assert statuses == ["completed", "failed"]


# ===== P4-3: Concurrent different threads ===================================


async def test_concurrent_different_threads():
    """Different thread_ids → independent execution, both complete."""
    saved_a = _state(status="paused", current_agent="hitl_review")
    saved_b = _state(status="paused", current_agent="hitl_review")

    def make_cp(config):
        tid = config.get("configurable", {}).get("thread_id", "")
        if tid == "t-a":
            return _mock_checkpoint({**saved_a})
        return _mock_checkpoint({**saved_b})

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(side_effect=make_cp)
    mock_checkpointer.aput = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        pool = AsyncMock()
        pool.close = AsyncMock()

        results = await asyncio.gather(
            resume_from_hitl(
                "t-a",
                {"action": "approve"},
                hitl_type="final_review",
                valkey=MagicMock(),
                db_pool=pool,
            ),
            resume_from_hitl(
                "t-b",
                {"action": "approve"},
                hitl_type="final_review",
                valkey=MagicMock(),
                db_pool=pool,
            ),
        )

        assert results[0]["status"] == "completed"
        assert results[1]["status"] == "completed"
        assert mock_checkpointer.aput.await_count == 2


# ===== P4-4: Rejection saves failed checkpoint ==============================


async def test_rejection_saves_failed_checkpoint():
    """reject → checkpointer.aput called with failed state."""
    saved = _state(status="paused", current_agent="hitl_bid")

    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        result = await resume_from_hitl(
            "t-conc",
            {"action": "reject"},
            hitl_type="bid_approval",
            valkey=MagicMock(),
            db_pool=AsyncMock(),
        )

        assert result["status"] == "failed"
        mock_checkpointer.aput.assert_awaited_once()
        saved_args = mock_checkpointer.aput.call_args
        assert saved_args[0][1]["status"] == "failed"


# ===== P4-5: Resume already completed thread ================================


async def test_resume_already_completed_thread():
    """Resuming a thread with status='completed' → function still runs."""
    saved = _state(status="completed", current_agent="hitl_review")

    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        # resume_from_hitl doesn't guard against already-completed state
        result = await resume_from_hitl(
            "t-conc",
            {"action": "approve"},
            hitl_type="final_review",
            valkey=MagicMock(),
            db_pool=AsyncMock(),
        )

        # _apply_final_review sets status=completed on approve
        assert result["status"] == "completed"
        mock_checkpointer.aput.assert_awaited_once()
