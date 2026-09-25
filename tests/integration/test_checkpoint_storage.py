"""Integration tests for the HybridCheckpointSaver (Valkey hot + PostgreSQL cold).

Verifies round-trip persistence and the Valkey-miss -> PostgreSQL-fallback path.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.checkpoints import HybridCheckpointSaver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    """Build a LangGraph-style config dict."""
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if checkpoint_id is not None:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def _make_checkpoint(checkpoint_id: str | None = None, **extra: Any) -> dict[str, Any]:
    """Build a minimal checkpoint payload."""
    cid = checkpoint_id or uuid.uuid4().hex
    return {
        "id": cid,
        "current_agent": "scout",
        "status": "active",
        "requires_hitl": False,
        "artifacts": {"scout": ["job-1"]},
        **extra,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_checkpoint_round_trip(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """Saving a checkpoint and then retrieving it should return the same data.

    Flow: aput() -> stores in Valkey + PG -> aget_tuple() -> reads from Valkey.
    """
    # Patch the serde to use plain JSON for test simplicity.
    with patch.object(HybridCheckpointSaver, "serde") as mock_serde:
        mock_serde.dumps = MagicMock(side_effect=lambda d: json.dumps(d, default=str).encode())
        mock_serde.loads = MagicMock(side_effect=lambda b: json.loads(b if isinstance(b, str) else b.decode()))

        saver = HybridCheckpointSaver(
            valkey=mock_valkey,
            db_pool=mock_db_pool,
            valkey_ttl=timedelta(hours=1),
        )

        thread_id = "thread-ckpt-001"
        checkpoint_id = uuid.uuid4().hex
        checkpoint = _make_checkpoint(checkpoint_id)
        metadata: dict[str, Any] = {"source": "test"}

        config = _make_config(thread_id)

        # ---- SAVE ----
        # Set up the pipeline mock for _valkey_put
        pipe_mock = AsyncMock()
        pipe_mock.set = MagicMock()
        pipe_mock.execute = AsyncMock()
        mock_valkey.pipeline = MagicMock(return_value=pipe_mock)

        new_config = await saver.aput(config, checkpoint, metadata)

        assert new_config["configurable"]["thread_id"] == thread_id
        assert new_config["configurable"]["checkpoint_id"] == checkpoint_id

        # Verify PG write was called.
        conn = mock_db_pool._test_conn
        conn.execute.assert_awaited()

        # ---- RETRIEVE ----
        # Simulate Valkey returning the saved data.
        stored_blob = {
            **checkpoint,
            "parent_id": None,
            "_metadata": metadata,
        }
        mock_valkey.get = AsyncMock(return_value=json.dumps(stored_blob, default=str).encode())

        retrieve_config = _make_config(thread_id, checkpoint_id)
        result = await saver.aget_tuple(retrieve_config)

        assert result is not None
        assert result.checkpoint["id"] == checkpoint_id
        assert result.checkpoint["current_agent"] == "scout"
        assert result.checkpoint["artifacts"] == {"scout": ["job-1"]}


async def test_checkpoint_valkey_miss_postgres_fallback(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """When Valkey returns None, the saver should fall back to PostgreSQL and warm the cache.

    Flow: aget_tuple() -> Valkey miss -> PG fetch -> warm Valkey -> return checkpoint.
    """
    with patch.object(HybridCheckpointSaver, "serde") as mock_serde:
        mock_serde.dumps = MagicMock(side_effect=lambda d: json.dumps(d, default=str).encode())
        mock_serde.loads = MagicMock(side_effect=lambda b: json.loads(b if isinstance(b, str) else b.decode()))

        saver = HybridCheckpointSaver(
            valkey=mock_valkey,
            db_pool=mock_db_pool,
            valkey_ttl=timedelta(hours=1),
        )

        thread_id = "thread-ckpt-002"
        checkpoint_id = uuid.uuid4().hex

        # ---- Valkey returns None (miss) ----
        mock_valkey.get = AsyncMock(return_value=None)

        # ---- PostgreSQL returns the checkpoint ----
        pg_state_data = json.dumps(
            {
                "id": checkpoint_id,
                "parent_id": None,
                "_metadata": {"source": "pg_test"},
                "current_agent": "bid",
                "status": "paused",
                "requires_hitl": True,
                "artifacts": {"scout": ["job-x"], "bid": ["bid-y"]},
            },
            default=str,
        )

        pg_row = {
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": None,
            "state_data": pg_state_data,
            "created_at": "2026-02-01T00:00:00Z",
        }
        conn = mock_db_pool._test_conn
        conn.fetchrow = AsyncMock(return_value=pg_row)

        # Set up pipeline for cache-warming.
        pipe_mock = AsyncMock()
        pipe_mock.set = MagicMock()
        pipe_mock.execute = AsyncMock()
        mock_valkey.pipeline = MagicMock(return_value=pipe_mock)

        # ---- Retrieve ----
        config = _make_config(thread_id, checkpoint_id)
        result = await saver.aget_tuple(config)

        assert result is not None
        assert result.checkpoint["id"] == checkpoint_id
        assert result.checkpoint["current_agent"] == "bid"
        assert result.checkpoint["requires_hitl"] is True

        # Verify that the saver warmed the Valkey cache after the PG fallback.
        pipe_mock.execute.assert_awaited()


async def test_checkpoint_both_layers_empty_returns_none(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """When both Valkey and PostgreSQL have no data, aget_tuple should return None."""
    with patch.object(HybridCheckpointSaver, "serde") as mock_serde:
        mock_serde.loads = MagicMock()

        saver = HybridCheckpointSaver(
            valkey=mock_valkey,
            db_pool=mock_db_pool,
        )

        mock_valkey.get = AsyncMock(return_value=None)
        conn = mock_db_pool._test_conn
        conn.fetchrow = AsyncMock(return_value=None)

        config = _make_config("thread-nonexistent")
        result = await saver.aget_tuple(config)

        assert result is None
