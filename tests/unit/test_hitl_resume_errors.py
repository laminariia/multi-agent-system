"""Tests for DB failure resilience in resume_from_hitl.

Target: src/core/graph.py:1302-1481 — pool creation, checkpoint ops, finally cleanup.
Pattern: tests/unit/test_hitl_resume.py — mocked checkpoint/graph patches.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.state import create_initial_state

pytestmark = pytest.mark.asyncio


def _state(**overrides: Any) -> dict[str, Any]:
    """Build a paused state suitable for resume_from_hitl tests."""
    project = {
        "project_id": "p-err",
        "job_id": "j-err",
        "platform": "freelancer",
        "client": {"name": "ErrTest"},
        "requirements": "page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent="scout", thread_id="t-err")
    s.update(overrides)
    return s


# ===== P5-1: Pool creation failure ==========================================


async def test_resume_db_pool_creation_fails():
    """asyncpg.create_pool raises OSError → propagates to caller."""
    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.asyncpg") as mock_asyncpg,
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")
        mock_asyncpg.create_pool = AsyncMock(side_effect=OSError("connection refused"))

        from src.core.graph import resume_from_hitl

        with pytest.raises(OSError, match="connection refused"):
            await resume_from_hitl(
                "t-err",
                {"action": "approve"},
                hitl_type="bid_approval",
                valkey=MagicMock(),
                db_pool=None,  # triggers auto-pool creation
            )


# ===== P5-2: Checkpoint load failure ========================================


async def test_resume_checkpoint_load_fails():
    """aget_tuple raises ConnectionError → propagates, auto-pool closed."""
    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(side_effect=ConnectionError("db gone"))

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        patch("src.core.graph.asyncpg") as mock_asyncpg,
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

        from src.core.graph import resume_from_hitl

        with pytest.raises(ConnectionError, match="db gone"):
            await resume_from_hitl(
                "t-err",
                {"action": "approve"},
                hitl_type="bid_approval",
                valkey=MagicMock(),
                db_pool=None,  # auto-pool
            )

        # Auto-created pool must be closed in finally
        mock_pool.close.assert_awaited_once()


# ===== P5-3: Checkpoint save failure ========================================


async def test_resume_checkpoint_save_fails():
    """aput raises OSError on rejection → propagates, auto-pool closed."""
    saved = _state(status="paused", current_agent="hitl_review")
    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock(side_effect=OSError("disk full"))

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        patch("src.core.graph.asyncpg") as mock_asyncpg,
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

        from src.core.graph import resume_from_hitl

        with pytest.raises(OSError, match="disk full"):
            await resume_from_hitl(
                "t-err",
                {"action": "reject"},
                hitl_type="final_review",
                valkey=MagicMock(),
                db_pool=None,  # auto-pool
            )

        mock_pool.close.assert_awaited_once()


# ===== P5-4: Graph invoke failure ==========================================


async def test_resume_graph_invoke_fails():
    """graph.ainvoke raises Exception → propagates, auto-pool closed."""
    saved = _state(status="paused", current_agent="hitl_bid")
    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph crashed"))

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        patch("src.core.graph.build_full_pipeline_graph", return_value=mock_graph),
        patch("src.core.graph.asyncpg") as mock_asyncpg,
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

        from src.core.graph import resume_from_hitl

        with pytest.raises(RuntimeError, match="graph crashed"):
            await resume_from_hitl(
                "t-err",
                {"action": "approve"},
                hitl_type="bid_approval",
                valkey=MagicMock(),
                db_pool=None,
            )

        mock_pool.close.assert_awaited_once()


# ===== P5-5: Auto-pool closed in finally ====================================


async def test_auto_pool_closed_in_finally():
    """Auto-created pool → close() called in finally even on success."""
    saved = _state(status="paused", current_agent="hitl_review")
    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
        patch("src.core.graph.asyncpg") as mock_asyncpg,
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

        from src.core.graph import resume_from_hitl

        await resume_from_hitl(
            "t-err",
            {"action": "approve"},
            hitl_type="final_review",
            valkey=MagicMock(),
            db_pool=None,  # auto-pool
        )

        mock_pool.close.assert_awaited_once()


# ===== P5-6: Provided pool NOT closed =======================================


async def test_provided_pool_not_closed():
    """Caller-provided pool → NOT closed in finally."""
    saved = _state(status="paused", current_agent="hitl_review")
    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)
    mock_checkpointer.aput = AsyncMock()

    caller_pool = AsyncMock()
    caller_pool.close = AsyncMock()

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        await resume_from_hitl(
            "t-err",
            {"action": "approve"},
            hitl_type="final_review",
            valkey=MagicMock(),
            db_pool=caller_pool,  # caller-provided
        )

        caller_pool.close.assert_not_awaited()


# ===== P5-7: HITL expired after 24h =========================================


async def test_resume_hitl_expired_24h():
    """Checkpoint with _hitl_started_at > 24h ago → status='failed'."""
    old_timestamp = time.time() - 90000  # ~25 hours ago
    saved = _state(
        status="paused",
        current_agent="hitl_bid",
        artifacts={"_hitl_started_at": old_timestamp},
    )
    mock_checkpoint_tuple = MagicMock()
    mock_checkpoint_tuple.checkpoint = saved

    mock_checkpointer = AsyncMock()
    mock_checkpointer.aget_tuple = AsyncMock(return_value=mock_checkpoint_tuple)

    with (
        patch("src.core.graph.get_settings") as mock_settings,
        patch("src.core.graph.HybridCheckpointSaver", return_value=mock_checkpointer),
    ):
        mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x/y")

        from src.core.graph import resume_from_hitl

        result = await resume_from_hitl(
            "t-err",
            {"action": "approve"},
            hitl_type="bid_approval",
            valkey=MagicMock(),
            db_pool=AsyncMock(),
        )

        assert result["status"] == "failed"
        assert "expired" in result.get("error", "").lower()
