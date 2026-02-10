"""Extended unit tests for src.core.graph — HITL nodes, graph builders, runners, resume.

Covers: hitl_bid_node, hitl_review_node, hitl_node (legacy),
build_scout_bid_graph, create_graph_with_persistence,
run_full_pipeline, run_scout_bid_pipeline,
_apply_bid_approval, _apply_final_review, resume_from_hitl.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.graph import (
    _apply_bid_approval,
    _apply_final_review,
    build_full_pipeline_graph,
    build_scout_bid_graph,
    hitl_bid_node,
    hitl_node,
    hitl_review_node,
)
from src.core.state import AgentState, create_initial_state

pytestmark = pytest.mark.asyncio


def _state(**overrides: Any) -> AgentState:
    project = {
        "project_id": "p1", "job_id": "j1", "platform": "freelancer",
        "client": {"name": "T"}, "requirements": "page", "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent="scout", thread_id="t-graph")
    s.update(overrides)  # type: ignore[typeddict-item]
    return s


# ===== HITL node tests =======================================================

async def test_hitl_bid_node_sets_paused():
    s = _state(status="active")
    result = await hitl_bid_node(s)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "hitl_bid"


async def test_hitl_bid_node_already_paused():
    s = _state(status="paused")
    result = await hitl_bid_node(s)
    assert result is s  # unchanged


async def test_hitl_review_node_sets_paused():
    s = _state(status="active")
    result = await hitl_review_node(s)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "hitl_review"


async def test_hitl_review_node_already_paused():
    s = _state(status="paused")
    result = await hitl_review_node(s)
    assert result is s


async def test_hitl_node_legacy_sets_paused():
    s = _state(status="active")
    result = await hitl_node(s)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True


async def test_hitl_node_legacy_already_paused():
    s = _state(status="paused")
    result = await hitl_node(s)
    assert result is s


# ===== Graph builders ========================================================

def test_build_scout_bid_graph_compiles():
    graph = build_scout_bid_graph()
    assert graph is not None


@patch("src.core.graph.StateGraph")
def test_build_scout_bid_graph_with_checkpointer(mock_sg_cls):
    mock_graph = MagicMock()
    mock_sg_cls.return_value = mock_graph
    cp = MagicMock()
    result = build_scout_bid_graph(checkpointer=cp)
    mock_graph.compile.assert_called_once_with(checkpointer=cp)
    assert result is mock_graph.compile.return_value


def test_build_full_pipeline_graph_compiles():
    graph = build_full_pipeline_graph()
    assert graph is not None


@patch("src.core.graph.build_full_pipeline_graph")
@patch("src.core.graph.HybridCheckpointSaver")
def test_create_graph_with_persistence_full(mock_saver, mock_build):
    from src.core.graph import create_graph_with_persistence
    valkey = MagicMock()
    db_pool = MagicMock()
    graph = create_graph_with_persistence(valkey, db_pool, full_pipeline=True)
    mock_saver.assert_called_once_with(valkey=valkey, db_pool=db_pool)
    mock_build.assert_called_once_with(checkpointer=mock_saver.return_value)
    assert graph is mock_build.return_value


@patch("src.core.graph.build_scout_bid_graph")
@patch("src.core.graph.HybridCheckpointSaver")
def test_create_graph_with_persistence_legacy(mock_saver, mock_build):
    from src.core.graph import create_graph_with_persistence
    v, p = MagicMock(), MagicMock()
    graph = create_graph_with_persistence(v, p, full_pipeline=False)
    mock_saver.assert_called_once_with(valkey=v, db_pool=p)
    mock_build.assert_called_once_with(checkpointer=mock_saver.return_value)
    assert graph is mock_build.return_value


# ===== Convenience runners ===================================================

@patch("src.core.graph.build_full_pipeline_graph")
async def test_run_full_pipeline(mock_build):
    from src.core.graph import run_full_pipeline

    final = _state(status="completed")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=final)
    mock_build.return_value = mock_graph

    project = {
        "project_id": "p1", "job_id": "j1", "platform": "freelancer",
        "client": {"name": "T"}, "requirements": "page", "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    result = await run_full_pipeline(project, thread_id="tid-1")
    assert result["status"] == "completed"
    mock_graph.ainvoke.assert_awaited_once()


@patch("src.core.graph.build_full_pipeline_graph")
async def test_run_full_pipeline_auto_thread(mock_build):
    from src.core.graph import run_full_pipeline

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=_state(status="paused"))
    mock_build.return_value = mock_graph

    project = {
        "project_id": "p1", "job_id": "j1", "platform": "freelancer",
        "client": {"name": "T"}, "requirements": "page", "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    result = await run_full_pipeline(project)
    assert result is not None


@patch("src.core.graph.build_scout_bid_graph")
async def test_run_scout_bid_pipeline(mock_build):
    from src.core.graph import run_scout_bid_pipeline

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=_state(status="paused"))
    mock_build.return_value = mock_graph

    project = {
        "project_id": "p1", "job_id": "j1", "platform": "freelancer",
        "client": {"name": "T"}, "requirements": "page", "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    result = await run_scout_bid_pipeline(project, thread_id="tid-2")
    assert result["status"] == "paused"


@patch("src.core.graph.build_scout_bid_graph")
async def test_run_scout_bid_pipeline_auto_thread(mock_build):
    from src.core.graph import run_scout_bid_pipeline

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=_state())
    mock_build.return_value = mock_graph

    project = {
        "project_id": "p1", "job_id": "j1", "platform": "freelancer",
        "client": {"name": "T"}, "requirements": "page", "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    result = await run_scout_bid_pipeline(project)
    assert result is not None


# ===== _apply_bid_approval ===================================================

def test_apply_bid_approval_approve():
    s = _state(status="paused", requires_hitl=True, current_agent="hitl_bid")
    result = _apply_bid_approval(s, "approve", {}, "t1")
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"
    assert result["requires_hitl"] is False


def test_apply_bid_approval_reject():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_bid_approval(s, "reject", {}, "t1")
    assert result["status"] == "failed"
    assert any("bid rejected" in e for e in result["errors"])


def test_apply_bid_approval_edit_with_edits():
    s = _state(status="paused", requires_hitl=True)
    edits = {"bid_amount": 400}
    result = _apply_bid_approval(s, "edit", {"edits": edits}, "t1")
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"
    assert "hitl_edits" in result["artifacts"]


def test_apply_bid_approval_edit_no_edits():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_bid_approval(s, "edit", {}, "t1")
    assert result["status"] == "active"
    assert "hitl_edits" not in result.get("artifacts", {})


def test_apply_bid_approval_unknown_action():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_bid_approval(s, "banana", {}, "t1")
    assert result["status"] == "active"
    assert result["next_agent"] == "planner"


# ===== _apply_final_review ===================================================

def test_apply_final_review_approve():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_final_review(s, "approve", {}, "t1")
    assert result["status"] == "completed"
    assert result["requires_hitl"] is False


def test_apply_final_review_reject():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_final_review(s, "reject", {}, "t1")
    assert result["status"] == "failed"
    assert any("final delivery rejected" in e for e in result["errors"])


def test_apply_final_review_edit_with_edits():
    s = _state(status="paused", requires_hitl=True)
    edits = {"notes": "change color"}
    result = _apply_final_review(s, "edit", {"edits": edits}, "t1")
    assert result["status"] == "completed"
    assert "hitl_edits" in result["artifacts"]


def test_apply_final_review_edit_no_edits():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_final_review(s, "edit", {}, "t1")
    assert result["status"] == "completed"


def test_apply_final_review_unknown_action():
    s = _state(status="paused", requires_hitl=True)
    result = _apply_final_review(s, "banana", {}, "t1")
    assert result["status"] == "completed"


# ===== resume_from_hitl ======================================================

@patch("src.core.graph.build_full_pipeline_graph")
@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_bid_approve(mock_settings, mock_pg, mock_saver_cls, mock_build):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_bid", requires_hitl=True)

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pool = AsyncMock()
    mock_pg.create_pool = AsyncMock(return_value=mock_pool)

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    final = _state(status="completed")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=final)
    mock_build.return_value = mock_graph

    result = await resume_from_hitl("t1", {"action": "approve"}, hitl_type="bid_approval")
    assert result["status"] == "completed"
    mock_graph.ainvoke.assert_awaited_once()
    mock_pool.close.assert_awaited_once()


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_bid_reject(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_bid")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pool = AsyncMock()
    mock_pg.create_pool = AsyncMock(return_value=mock_pool)

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    result = await resume_from_hitl("t1", {"action": "reject"}, hitl_type="bid_approval")
    assert result["status"] == "failed"
    mock_cp.aput.assert_awaited_once()


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_final_review_approve(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_review")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pool = AsyncMock()
    mock_pg.create_pool = AsyncMock(return_value=mock_pool)

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    result = await resume_from_hitl("t1", {"action": "approve"}, hitl_type="final_review")
    assert result["status"] == "completed"


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_no_checkpoint_raises(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pool = AsyncMock()
    mock_pg.create_pool = AsyncMock(return_value=mock_pool)

    mock_cp = AsyncMock()
    mock_cp.aget_tuple = AsyncMock(return_value=None)
    mock_saver_cls.return_value = mock_cp

    with pytest.raises(ValueError, match="No checkpoint"):
        await resume_from_hitl("t1", {"action": "approve"})


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_auto_detect_bid(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_bid")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pg.create_pool = AsyncMock(return_value=AsyncMock())

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    result = await resume_from_hitl("t1", {"action": "reject"})
    assert result["status"] == "failed"


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_auto_detect_review(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_review")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pg.create_pool = AsyncMock(return_value=AsyncMock())

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    result = await resume_from_hitl("t1", {"action": "approve"})
    assert result["status"] == "completed"


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_auto_detect_fallback(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="some_other")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pg.create_pool = AsyncMock(return_value=AsyncMock())

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    # Falls back to bid_approval
    result = await resume_from_hitl("t1", {"action": "reject"})
    assert result["status"] == "failed"


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_with_provided_pool(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_review")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    ext_pool = AsyncMock()  # externally provided
    valkey = AsyncMock()

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    result = await resume_from_hitl(
        "t1", {"action": "approve"}, hitl_type="final_review",
        valkey=valkey, db_pool=ext_pool,
    )
    assert result["status"] == "completed"
    # Should NOT have created a new pool
    mock_pg.create_pool.assert_not_called()
    # Should NOT close the externally provided pool
    ext_pool.close.assert_not_called()


@patch("src.core.graph.HybridCheckpointSaver")
@patch("src.core.graph.asyncpg")
@patch("src.core.graph.get_settings")
async def test_resume_final_review_edit(mock_settings, mock_pg, mock_saver_cls):
    from src.core.graph import resume_from_hitl

    saved = _state(status="paused", current_agent="hitl_review")

    mock_settings.return_value = MagicMock(DATABASE_URL="postgresql://x")
    mock_pg.create_pool = AsyncMock(return_value=AsyncMock())

    mock_cp = AsyncMock()
    checkpoint_tuple = MagicMock()
    checkpoint_tuple.checkpoint = dict(saved)
    mock_cp.aget_tuple = AsyncMock(return_value=checkpoint_tuple)
    mock_saver_cls.return_value = mock_cp

    result = await resume_from_hitl(
        "t1", {"action": "edit", "edits": {"color": "blue"}}, hitl_type="final_review",
    )
    assert result["status"] == "completed"
    assert "hitl_edits" in result["artifacts"]
