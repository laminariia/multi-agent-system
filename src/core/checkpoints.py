"""Hybrid checkpoint saver (Valkey hot + PostgreSQL cold).

Extends LangGraph's ``BaseCheckpointSaver`` to provide dual-layer persistence:

* **Valkey** -- hot cache with a 1-hour TTL for active workflows.
* **PostgreSQL** -- permanent storage for recovery, audit, and history.

Serialisation uses LangGraph's ``JsonPlusSerializer`` to handle datetime,
bytes, and other types that standard JSON cannot encode.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import asyncpg
import structlog
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from redis.asyncio import Redis as AsyncRedis

logger = structlog.get_logger(__name__)


class HybridCheckpointSaver(BaseCheckpointSaver):
    """Dual-layer checkpoint saver for LangGraph.

    Args:
        valkey: Async Valkey (redis-py) client.
        db_pool: ``asyncpg.Pool`` for PostgreSQL.
        valkey_ttl: Time-to-live for hot cache entries.
    """

    serde = JsonPlusSerializer()

    def __init__(
        self,
        valkey: AsyncRedis,
        db_pool: asyncpg.Pool,
        *,
        valkey_ttl: timedelta = timedelta(hours=1),
    ) -> None:
        super().__init__(serde=self.serde)
        self.valkey = valkey
        self.db_pool = db_pool
        self.valkey_ttl = valkey_ttl
        self._ttl_seconds = int(valkey_ttl.total_seconds())

    # ------------------------------------------------------------------
    # Required overrides
    # ------------------------------------------------------------------

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        """Retrieve the latest (or specific) checkpoint for a thread.

        Tries Valkey first; on miss, falls back to PostgreSQL and warms the
        Valkey cache.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")

        # --- Valkey fast path ---
        cache_key = self._cache_key(thread_id, checkpoint_id)
        raw = await self.valkey.get(cache_key)
        if raw is not None:
            try:
                data = json.loads(raw)  # type: ignore[arg-type]
                return self._data_to_tuple(data, config)
            except Exception:
                logger.warning("checkpoint_valkey_deserialize_failed", thread_id=thread_id, exc_info=True)

        # --- PostgreSQL fallback ---
        row = await self._pg_get(thread_id, checkpoint_id)
        if row is None:
            return None

        raw = row["state_data"]
        state_data: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else raw
        # Warm Valkey
        await self._valkey_put(thread_id, state_data.get("id", checkpoint_id or "latest"), state_data)

        return self._data_to_tuple(state_data, config)

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any = None,
    ) -> dict[str, Any]:
        """Save a checkpoint to both Valkey and PostgreSQL."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint.get("id") or uuid.uuid4().hex  # type: ignore[union-attr]
        parent_id = config["configurable"].get("checkpoint_id")

        # Merge metadata into the stored blob
        stored: dict[str, Any] = {
            **(checkpoint if isinstance(checkpoint, dict) else {}),
            "id": checkpoint_id,
            "parent_id": parent_id,
            "_metadata": metadata if isinstance(metadata, dict) else {},
        }

        # --- Valkey ---
        await self._valkey_put(thread_id, checkpoint_id, stored)

        # --- PostgreSQL ---
        await self._pg_put(thread_id, checkpoint_id, parent_id, stored)

        new_config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }
        logger.debug("checkpoint_saved", thread_id=thread_id, checkpoint_id=checkpoint_id)
        return new_config

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints for a thread, newest first."""
        if config is None:
            return

        thread_id = config["configurable"]["thread_id"]

        query = """
            SELECT thread_id, checkpoint_id, parent_checkpoint_id, state_data, created_at
            FROM langgraph_checkpoints
            WHERE thread_id = $1
            ORDER BY created_at DESC
            LIMIT $2
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, thread_id, limit)

        for row in rows:
            state_data = json.loads(row["state_data"]) if isinstance(row["state_data"], str) else row["state_data"]
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_id": row["checkpoint_id"],
                    },
                },
                checkpoint=state_data,
                metadata=state_data.get("_metadata", {}),
                parent_config={
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_id": row["parent_checkpoint_id"],
                    },
                } if row["parent_checkpoint_id"] else None,
            )

    # Synchronous variants -- not used but required by the ABC in some versions
    def get_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        raise NotImplementedError("Use the async variant aget_tuple")

    def put(
        self,
        config: dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Use the async variant aput")

    def list(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> Any:
        raise NotImplementedError("Use the async variant alist")

    # ------------------------------------------------------------------
    # Valkey helpers
    # ------------------------------------------------------------------

    def _cache_key(self, thread_id: str, checkpoint_id: str | None = None) -> str:
        suffix = checkpoint_id or "latest"
        return f"checkpoint:{thread_id}:{suffix}"

    async def _valkey_put(self, thread_id: str, checkpoint_id: str, data: dict[str, Any]) -> None:
        serialized = json.dumps(data, default=str)

        specific_key = self._cache_key(thread_id, checkpoint_id)
        latest_key = self._cache_key(thread_id, None)

        pipe = self.valkey.pipeline(transaction=False)
        pipe.set(specific_key, serialized, ex=self._ttl_seconds)
        pipe.set(latest_key, serialized, ex=self._ttl_seconds)
        try:
            await pipe.execute()
        except Exception:
            logger.warning("checkpoint_valkey_put_failed", thread_id=thread_id, exc_info=True)

    # ------------------------------------------------------------------
    # PostgreSQL helpers
    # ------------------------------------------------------------------

    async def _pg_get(self, thread_id: str, checkpoint_id: str | None) -> asyncpg.Record | None:
        async with self.db_pool.acquire() as conn:
            if checkpoint_id:
                return await conn.fetchrow(
                    """
                    SELECT thread_id, checkpoint_id, parent_checkpoint_id, state_data, created_at
                    FROM langgraph_checkpoints
                    WHERE thread_id = $1 AND checkpoint_id = $2
                    """,
                    thread_id,
                    checkpoint_id,
                )
            return await conn.fetchrow(
                """
                SELECT thread_id, checkpoint_id, parent_checkpoint_id, state_data, created_at
                FROM langgraph_checkpoints
                WHERE thread_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                thread_id,
            )

    async def _pg_put(
        self,
        thread_id: str,
        checkpoint_id: str,
        parent_id: str | None,
        data: dict[str, Any],
    ) -> None:
        json_data = json.dumps(data, default=str)

        current_agent = data.get("current_agent", "")
        status = data.get("status", "active")
        requires_hitl = data.get("requires_hitl", False)

        async with self.db_pool.acquire() as conn:
            # Upsert into main checkpoints table
            await conn.execute(
                """
                INSERT INTO langgraph_checkpoints
                    (thread_id, checkpoint_id, parent_checkpoint_id, state_data,
                     current_agent, status, requires_hitl)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
                ON CONFLICT (thread_id, checkpoint_id)
                DO UPDATE SET
                    state_data = $4::jsonb,
                    current_agent = $5,
                    status = $6,
                    requires_hitl = $7
                """,
                thread_id,
                checkpoint_id,
                parent_id,
                json_data,
                current_agent,
                status,
                requires_hitl,
            )

            # Append to history for rollback support
            await conn.execute(
                """
                INSERT INTO langgraph_checkpoint_history
                    (thread_id, checkpoint_id, state_data)
                VALUES ($1, $2, $3::jsonb)
                """,
                thread_id,
                checkpoint_id,
                json_data,
            )

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _data_to_tuple(data: dict[str, Any], config: dict[str, Any]) -> CheckpointTuple:
        parent_id = data.get("parent_id")
        thread_id = config["configurable"]["thread_id"]
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": data.get("id", ""),
                },
            },
            checkpoint=data,
            metadata=data.get("_metadata", {}),
            parent_config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": parent_id,
                },
            } if parent_id else None,
        )
