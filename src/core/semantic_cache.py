"""Dual-layer semantic cache (Valkey hot + PostgreSQL cold).

Reduces LLM API costs by returning cached responses for semantically similar
queries.  The similarity threshold is 0.92 (cosine).

**Hot layer (Valkey):** RediSearch vector index (HNSW, FLOAT32, DIM=3072,
COSINE).  Provides sub-millisecond lookup for active queries.

**Cold layer (PostgreSQL):** pgvector with DiskANN index (``<=>`` operator).
Acts as persistent fallback and long-term audit store.

Embeddings are produced by OpenAI ``text-embedding-3-large`` (3072 dimensions)
via ``langchain_openai.OpenAIEmbeddings``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import asyncpg
import numpy as np
import structlog
from langchain_openai import OpenAIEmbeddings
from redis.asyncio import Redis as AsyncRedis
from redis.commands.search.field import NumericField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# TTL by query type (seconds)
# ---------------------------------------------------------------------------

def _build_ttl_map() -> dict[str, int]:
    """Build TTL map from application settings (falls back to defaults)."""
    try:
        from src.core.config import get_settings
        s = get_settings()
        return {
            "proposal": s.SEMANTIC_CACHE_TTL_PROPOSAL,
            "code": s.SEMANTIC_CACHE_TTL_CODE,
            "content": s.SEMANTIC_CACHE_TTL_CONTENT,
            "translation": s.SEMANTIC_CACHE_TTL_TRANSLATION,
            "default": s.SEMANTIC_CACHE_TTL_DEFAULT,
        }
    except Exception:  # noqa: BLE001
        return {
            "proposal": 86_400,
            "code": 3_600,
            "content": 43_200,
            "translation": 604_800,
            "default": 21_600,
        }


def _get_similarity_threshold() -> float:
    """Return similarity threshold from application settings."""
    try:
        from src.core.config import get_settings
        return get_settings().SEMANTIC_CACHE_SIMILARITY_THRESHOLD
    except Exception:  # noqa: BLE001
        return 0.92


TTL_MAP: dict[str, int] = _build_ttl_map()

# Queries that must never be cached
NEVER_CACHE_TYPES: frozenset[str] = frozenset({"real_time_data", "random_generation", "personalized"})

SIMILARITY_THRESHOLD: float = _get_similarity_threshold()
EMBEDDING_DIM: int = 3072
VALKEY_INDEX_NAME: str = "semantic_cache"
VALKEY_PREFIX: str = "sem_cache:"


class SemanticCache:
    """Dual-layer semantic cache backed by Valkey and PostgreSQL.

    Args:
        valkey: Async Valkey (redis-py) client.
        db_pool: ``asyncpg.Pool`` for the PostgreSQL cold store.
        similarity_threshold: Cosine similarity cutoff for cache hits.
    """

    def __init__(
        self,
        valkey: AsyncRedis,
        db_pool: asyncpg.Pool,
        *,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
    ) -> None:
        self.valkey = valkey
        self.db_pool = db_pool
        self.similarity_threshold = similarity_threshold
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for semantic cache embeddings")
        self._embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large",
            openai_api_key=api_key,
        )
        self._index_created = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_index(self) -> None:
        """Create the Valkey RediSearch vector index if it does not exist."""
        if self._index_created:
            return
        try:
            await self.valkey.ft(VALKEY_INDEX_NAME).create_index(
                fields=[
                    VectorField(
                        "embedding",
                        "HNSW",
                        {"TYPE": "FLOAT32", "DIM": EMBEDDING_DIM, "DISTANCE_METRIC": "COSINE"},
                    ),
                    TextField("query"),
                    TextField("response"),
                    TextField("query_type"),
                    NumericField("timestamp"),
                ],
                definition=IndexDefinition(prefix=[VALKEY_PREFIX], index_type=IndexType.HASH),
            )
            logger.info("valkey_semantic_index_created")
        except Exception:
            # Index may already exist (ResponseError)
            logger.debug("valkey_semantic_index_already_exists_or_error", exc_info=True)
        self._index_created = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, query: str, query_type: str = "default") -> str | None:
        """Look up a cached response for a semantically similar query.

        Checks Valkey first, then falls back to PostgreSQL.

        Returns:
            The cached response text, or ``None`` on miss.
        """
        if query_type in NEVER_CACHE_TYPES:
            return None

        query_embedding = await self._embed(query)

        # --- Hot layer: Valkey ---
        result = await self._valkey_search(query_embedding, query_type)
        if result is not None:
            logger.debug("semantic_cache_hit", layer="valkey", query_type=query_type)
            await self._bump_stats(hit=True)
            return result

        # --- Cold layer: PostgreSQL ---
        result = await self._pg_search(query_embedding, query_type)
        if result is not None:
            logger.debug("semantic_cache_hit", layer="postgres", query_type=query_type)
            await self._bump_stats(hit=True)
            # Warm Valkey for future fast hits
            await self._valkey_store(query, result, query_type, query_embedding)
            return result

        await self._bump_stats(hit=False)
        return None

    async def set(self, query: str, response: str, query_type: str = "default") -> None:
        """Store a query-response pair in both cache layers."""
        if query_type in NEVER_CACHE_TYPES:
            return

        query_embedding = await self._embed(query)
        await self._valkey_store(query, response, query_type, query_embedding)
        await self._pg_store(query, response, query_type, query_embedding)
        logger.debug("semantic_cache_set", query_type=query_type)

    async def invalidate_by_type(self, query_type: str) -> int:
        """Remove all cache entries matching *query_type*.

        Returns:
            Number of PostgreSQL rows deleted (Valkey count is best-effort).
        """
        # Valkey: scan and delete matching keys
        deleted_valkey = 0
        async for key in self.valkey.scan_iter(f"{VALKEY_PREFIX}*"):
            entry_type = await self.valkey.hget(key, "query_type")  # type: ignore[arg-type]
            if entry_type is not None:
                decoded = entry_type.decode() if isinstance(entry_type, bytes) else entry_type
                if decoded == query_type:
                    await self.valkey.delete(key)
                    deleted_valkey += 1

        # PostgreSQL
        deleted_pg = 0
        if self.db_pool is not None:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM semantic_cache WHERE query_type = $1", query_type
                )
                deleted_pg = int(result.split()[-1])

        logger.info("semantic_cache_invalidated", query_type=query_type, valkey=deleted_valkey, pg=deleted_pg)
        return deleted_pg

    async def flush(self) -> None:
        """Remove ALL cache entries from both layers."""
        # Valkey
        async for key in self.valkey.scan_iter(f"{VALKEY_PREFIX}*"):
            await self.valkey.delete(key)

        # PostgreSQL
        if self.db_pool is not None:
            async with self.db_pool.acquire() as conn:
                await conn.execute("TRUNCATE semantic_cache")

        # Reset stats
        await self.valkey.delete("cache:hits", "cache:misses")
        logger.info("semantic_cache_flushed")

    # ------------------------------------------------------------------
    # Valkey (hot layer)
    # ------------------------------------------------------------------

    async def _valkey_search(self, query_embedding: np.ndarray, query_type: str) -> str | None:
        await self.ensure_index()

        vec_bytes = query_embedding.astype(np.float32).tobytes()
        q = (
            Query("*=>[KNN 1 @embedding $vec AS score]")
            .return_fields("response", "query_type", "timestamp", "score")
            .dialect(2)
        )
        try:
            results = await self.valkey.ft(VALKEY_INDEX_NAME).search(q, query_params={"vec": vec_bytes})
        except Exception:
            logger.warning("valkey_vector_search_failed", exc_info=True)
            return None

        if not results.docs:
            return None

        doc = results.docs[0]
        similarity = 1.0 - float(doc.score)
        if similarity < self.similarity_threshold:
            return None

        # Check TTL expiry (belt-and-suspenders with Valkey key TTL)
        ttl = TTL_MAP.get(query_type, TTL_MAP["default"])
        doc_ts = float(doc.timestamp) if hasattr(doc, "timestamp") else 0.0
        if doc_ts > 0 and (time.time() - doc_ts) > ttl:
            return None

        return str(doc.response)

    async def _valkey_store(
        self,
        query: str,
        response: str,
        query_type: str,
        embedding: np.ndarray,
    ) -> None:
        await self.ensure_index()

        key = f"{VALKEY_PREFIX}{hashlib.sha256(query.encode()).hexdigest()[:20]}"
        mapping: dict[str, Any] = {
            "query": query,
            "response": response,
            "query_type": query_type,
            "embedding": embedding.astype(np.float32).tobytes(),
            "timestamp": time.time(),
        }
        ttl = TTL_MAP.get(query_type, TTL_MAP["default"])

        try:
            await self.valkey.hset(key, mapping=mapping)  # type: ignore[arg-type]
            await self.valkey.expire(key, ttl)
        except Exception:
            logger.warning("valkey_cache_store_failed", exc_info=True)

    # ------------------------------------------------------------------
    # PostgreSQL (cold layer)
    # ------------------------------------------------------------------

    async def _pg_search(self, query_embedding: np.ndarray, query_type: str) -> str | None:
        if self.db_pool is None:
            return None

        embedding_list = query_embedding.tolist()
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, response, 1 - (embedding <=> $1::vector) AS similarity
                    FROM semantic_cache
                    WHERE expires_at > NOW()
                      AND query_type = $2
                    ORDER BY embedding <=> $1::vector
                    LIMIT 1
                    """,
                    json.dumps(embedding_list),
                    query_type,
                )
        except Exception:
            logger.warning("pg_semantic_search_failed", exc_info=True)
            return None

        if row is None:
            return None

        if float(row["similarity"]) < self.similarity_threshold:
            return None

        # Bump hit counter
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = $1",
                    row["id"],
                )
        except Exception:
            logger.debug("cache_hit_count_update_failed", exc_info=True)

        return str(row["response"])

    async def _pg_store(
        self,
        query: str,
        response: str,
        query_type: str,
        embedding: np.ndarray,
    ) -> None:
        if self.db_pool is None:
            return

        ttl_seconds = TTL_MAP.get(query_type, TTL_MAP["default"])
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:32]
        embedding_list = embedding.tolist()

        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO semantic_cache
                        (query_hash, query, response, query_type, embedding, expires_at)
                    VALUES
                        ($1, $2, $3, $4, $5::vector, NOW() + make_interval(secs => $6))
                    ON CONFLICT (query_hash) DO UPDATE
                        SET response = $3,
                            embedding = $5::vector,
                            expires_at = NOW() + make_interval(secs => $6),
                            hit_count = 0
                    """,
                    query_hash,
                    query,
                    response,
                    query_type,
                    json.dumps(embedding_list),
                    float(ttl_seconds),
                )
        except Exception:
            logger.warning("pg_cache_store_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _embed(self, text: str) -> np.ndarray:
        """Compute the 3072-dim embedding for *text*."""
        raw = await self._embeddings.aembed_query(text)
        return np.array(raw, dtype=np.float32)

    async def _bump_stats(self, *, hit: bool) -> None:
        key = "cache:hits" if hit else "cache:misses"
        try:
            await self.valkey.incr(key)
        except Exception:
            logger.debug("cache_stats_bump_failed", key=key)
