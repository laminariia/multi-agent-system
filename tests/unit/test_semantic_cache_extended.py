"""Extended unit tests for src.core.semantic_cache.SemanticCache.

Covers: dual-layer hit/miss flows, TTL expiry, similarity threshold edge cases,
flush, invalidation, stats tracking, PostgreSQL cold-layer fallback and warming,
concurrent access patterns, and error handling.

All embedding calls and database operations are mocked.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.core.semantic_cache import (
    EMBEDDING_DIM,
    TTL_MAP,
    VALKEY_PREFIX,
    SemanticCache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_embedding(dim: int = EMBEDDING_DIM, seed: int = 42) -> np.ndarray:
    """Return a deterministic unit-norm embedding vector."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    return vec / np.linalg.norm(vec)


def _make_valkey_search_result(
    response: str,
    cosine_distance: float = 0.05,
    query_type: str = "default",
    timestamp: float | None = None,
):
    """Build a mock RediSearch result with a single document."""
    doc = MagicMock()
    doc.score = cosine_distance  # cosine distance, similarity = 1 - distance
    doc.response = response
    doc.query_type = query_type
    doc.timestamp = str(timestamp or time.time())
    result = MagicMock()
    result.docs = [doc]
    return result


@pytest.fixture()
def cache(mock_valkey: AsyncMock, mock_db_pool: AsyncMock) -> SemanticCache:
    """SemanticCache wired to mock infrastructure with patched embeddings."""
    with patch("src.core.semantic_cache.OpenAIEmbeddings"):
        c = SemanticCache(mock_valkey, mock_db_pool, similarity_threshold=0.92)
    c._embeddings = AsyncMock()
    c._embeddings.aembed_query = AsyncMock(return_value=_mock_embedding().tolist())
    return c


# ---------------------------------------------------------------------------
# Valkey hot-layer tests
# ---------------------------------------------------------------------------


async def test_valkey_hit_returns_cached_response(cache: SemanticCache, mock_valkey: AsyncMock):
    """A Valkey search returning a similar doc should be a cache hit."""
    search_result = _make_valkey_search_result("Cached answer", cosine_distance=0.03)
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=search_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await cache.get("What is TypeScript?", query_type="default")
    assert result == "Cached answer"


async def test_valkey_miss_below_threshold(cache: SemanticCache, mock_valkey: AsyncMock):
    """A doc with similarity below threshold should NOT be returned."""
    # cosine_distance = 0.15 → similarity = 0.85 < 0.92 threshold
    search_result = _make_valkey_search_result("Old answer", cosine_distance=0.15)
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=search_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await cache.get("What is TypeScript?", query_type="default")
    assert result is None


async def test_valkey_miss_exact_threshold_boundary(cache: SemanticCache, mock_valkey: AsyncMock):
    """Similarity exactly at threshold should be a hit (>=, not >)."""
    # cosine_distance = 0.08 → similarity = 0.92 = threshold → hit
    search_result = _make_valkey_search_result("Boundary answer", cosine_distance=0.08)
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=search_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await cache.get("Boundary query", query_type="default")
    assert result == "Boundary answer"


async def test_valkey_expired_ttl_returns_none(cache: SemanticCache, mock_valkey: AsyncMock):
    """A doc whose timestamp exceeds the TTL should be treated as expired."""
    # Set timestamp 100_000 seconds ago — exceeds default TTL (21600s)
    old_ts = time.time() - 100_000
    search_result = _make_valkey_search_result("Stale answer", cosine_distance=0.03, timestamp=old_ts)
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=search_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await cache.get("Old query", query_type="default")
    assert result is None


async def test_valkey_search_exception_returns_none(cache: SemanticCache, mock_valkey: AsyncMock):
    """If Valkey search throws, gracefully fall through to PG layer."""
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(side_effect=ConnectionError("Valkey down"))
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    # PG layer also returns None (default mock)
    result = await cache.get("Query during outage", query_type="default")
    assert result is None


async def test_valkey_empty_docs_returns_none(cache: SemanticCache, mock_valkey: AsyncMock):
    """An empty Valkey search result should return None."""
    empty_result = MagicMock()
    empty_result.docs = []
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=empty_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await cache.get("No matches", query_type="default")
    assert result is None


# ---------------------------------------------------------------------------
# PostgreSQL cold-layer tests
# ---------------------------------------------------------------------------


async def test_pg_fallback_on_valkey_miss(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """When Valkey misses, PG layer should be checked."""
    # Valkey returns empty
    empty_result = MagicMock()
    empty_result.docs = []
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=empty_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    # PG returns a hit
    conn = mock_db_pool._test_conn
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "response": "PG cached answer", "similarity": 0.95,
    })

    result = await cache.get("Cold layer query", query_type="default")
    assert result == "PG cached answer"


async def test_pg_miss_below_threshold(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """PG result with similarity below threshold should return None."""
    empty_result = MagicMock()
    empty_result.docs = []
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=empty_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    conn = mock_db_pool._test_conn
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "response": "Low similarity", "similarity": 0.80,
    })

    result = await cache.get("Low match", query_type="default")
    assert result is None


async def test_pg_hit_warms_valkey(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """A PG hit should also store the result in Valkey for future fast access."""
    empty_result = MagicMock()
    empty_result.docs = []
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=empty_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    conn = mock_db_pool._test_conn
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "response": "Warmed answer", "similarity": 0.96,
    })

    await cache.get("Warming query", query_type="default")

    # Valkey hset should have been called to warm the hot layer
    mock_valkey.hset.assert_awaited()
    mock_valkey.expire.assert_awaited()


async def test_pg_none_pool_skips_cold_layer(mock_valkey: AsyncMock):
    """If db_pool is None, PG layer is skipped entirely."""
    with patch("src.core.semantic_cache.OpenAIEmbeddings"):
        c = SemanticCache(mock_valkey, None, similarity_threshold=0.92)  # type: ignore[arg-type]
    c._embeddings = AsyncMock()
    c._embeddings.aembed_query = AsyncMock(return_value=_mock_embedding().tolist())

    result = await c.get("No PG query", query_type="default")
    assert result is None


async def test_pg_search_exception_returns_none(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """PG search exception should be handled gracefully."""
    empty_result = MagicMock()
    empty_result.docs = []
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=empty_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    conn = mock_db_pool._test_conn
    conn.fetchrow = AsyncMock(side_effect=Exception("PG connection lost"))

    result = await cache.get("PG error query", query_type="default")
    assert result is None


# ---------------------------------------------------------------------------
# set() tests
# ---------------------------------------------------------------------------


async def test_set_stores_in_both_layers(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """set() should store in both Valkey and PostgreSQL."""
    conn = mock_db_pool._test_conn

    await cache.set("New query", "New response", query_type="code")

    mock_valkey.hset.assert_awaited()
    mock_valkey.expire.assert_awaited()
    conn.execute.assert_awaited()


async def test_set_uses_correct_ttl(cache: SemanticCache, mock_valkey: AsyncMock):
    """set() should use the TTL from TTL_MAP for the given query_type."""
    await cache.set("Code query", "Code response", query_type="code")

    # expire should have been called with TTL_MAP["code"] = 3600
    call_args = mock_valkey.expire.call_args
    assert call_args[0][1] == TTL_MAP["code"]


async def test_set_skips_never_cache_types(cache: SemanticCache, mock_valkey: AsyncMock):
    """set() should be a no-op for never-cache types."""
    await cache.set("Real time", "Data", query_type="real_time_data")
    mock_valkey.hset.assert_not_awaited()


async def test_set_valkey_store_failure_logged_not_raised(cache: SemanticCache, mock_valkey: AsyncMock):
    """Valkey store failure should be logged but not raise."""
    mock_valkey.hset = AsyncMock(side_effect=ConnectionError("Valkey write failed"))
    # Should not raise
    await cache.set("Failing set", "Response", query_type="default")


# ---------------------------------------------------------------------------
# invalidate_by_type() tests
# ---------------------------------------------------------------------------


async def test_invalidate_deletes_from_pg(cache: SemanticCache, mock_db_pool: AsyncMock):
    """invalidate_by_type should delete matching PG entries."""
    conn = mock_db_pool._test_conn
    conn.execute = AsyncMock(return_value="DELETE 5")

    count = await cache.invalidate_by_type("code")
    assert count == 5


async def test_invalidate_scans_valkey_keys(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """invalidate_by_type should scan Valkey for matching keys."""
    # Simulate scan_iter yielding keys
    keys = [f"{VALKEY_PREFIX}key1", f"{VALKEY_PREFIX}key2"]

    async def mock_scan(*_args, **_kwargs):
        for k in keys:
            yield k

    mock_valkey.scan_iter = mock_scan
    mock_valkey.hget = AsyncMock(return_value=b"code")

    conn = mock_db_pool._test_conn
    conn.execute = AsyncMock(return_value="DELETE 2")

    count = await cache.invalidate_by_type("code")
    assert count == 2
    # Both keys should have been deleted
    assert mock_valkey.delete.await_count == 2


# ---------------------------------------------------------------------------
# flush() tests
# ---------------------------------------------------------------------------


async def test_flush_clears_both_layers(cache: SemanticCache, mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """flush() should clear Valkey keys and truncate PG table."""
    conn = mock_db_pool._test_conn

    await cache.flush()

    # Stats keys should be deleted
    mock_valkey.delete.assert_awaited()
    conn.execute.assert_awaited()


# ---------------------------------------------------------------------------
# ensure_index() tests
# ---------------------------------------------------------------------------


async def test_ensure_index_creates_once(cache: SemanticCache, mock_valkey: AsyncMock):
    """ensure_index should only create the index once (idempotent)."""
    ft_mock = MagicMock()
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    await cache.ensure_index()
    await cache.ensure_index()

    ft_mock.create_index.assert_awaited_once()


async def test_ensure_index_handles_already_exists(cache: SemanticCache, mock_valkey: AsyncMock):
    """ensure_index should handle 'index already exists' error gracefully."""
    ft_mock = MagicMock()
    ft_mock.create_index = AsyncMock(side_effect=Exception("Index already exists"))
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    # Should not raise
    cache._index_created = False
    await cache.ensure_index()
    assert cache._index_created is True


# ---------------------------------------------------------------------------
# Stats tracking tests
# ---------------------------------------------------------------------------


async def test_bump_stats_hit(cache: SemanticCache, mock_valkey: AsyncMock):
    """_bump_stats(hit=True) should increment cache:hits."""
    await cache._bump_stats(hit=True)
    mock_valkey.incr.assert_awaited_with("cache:hits")


async def test_bump_stats_miss(cache: SemanticCache, mock_valkey: AsyncMock):
    """_bump_stats(hit=False) should increment cache:misses."""
    await cache._bump_stats(hit=False)
    mock_valkey.incr.assert_awaited_with("cache:misses")


async def test_bump_stats_error_swallowed(cache: SemanticCache, mock_valkey: AsyncMock):
    """_bump_stats should swallow Valkey errors."""
    mock_valkey.incr = AsyncMock(side_effect=ConnectionError("Stats fail"))
    # Should not raise
    await cache._bump_stats(hit=True)


# ---------------------------------------------------------------------------
# TTL edge cases
# ---------------------------------------------------------------------------


async def test_ttl_map_proposal_is_24h():
    """Proposal TTL should be 86400 seconds (24 hours)."""
    assert TTL_MAP["proposal"] == 86_400


async def test_ttl_map_translation_is_7d():
    """Translation TTL should be 604800 seconds (7 days)."""
    assert TTL_MAP["translation"] == 604_800


async def test_ttl_map_code_is_1h():
    """Code TTL should be 3600 seconds (1 hour)."""
    assert TTL_MAP["code"] == 3_600


# ---------------------------------------------------------------------------
# Custom similarity threshold
# ---------------------------------------------------------------------------


async def test_custom_threshold(mock_valkey: AsyncMock, mock_db_pool: AsyncMock):
    """Cache with lower threshold should accept lower-similarity results."""
    with patch("src.core.semantic_cache.OpenAIEmbeddings"):
        c = SemanticCache(mock_valkey, mock_db_pool, similarity_threshold=0.80)
    c._embeddings = AsyncMock()
    c._embeddings.aembed_query = AsyncMock(return_value=_mock_embedding().tolist())

    # cosine_distance=0.15 → similarity=0.85 — above 0.80 threshold
    search_result = _make_valkey_search_result("Relaxed match", cosine_distance=0.15)
    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=search_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await c.get("Relaxed query")
    assert result == "Relaxed match"


# ---------------------------------------------------------------------------
# Embedding tests
# ---------------------------------------------------------------------------


async def test_embed_produces_float32_array(cache: SemanticCache):
    """_embed should return a float32 numpy array of correct dimension."""
    embedding = await cache._embed("test text")
    assert isinstance(embedding, np.ndarray)
    assert embedding.dtype == np.float32
    assert embedding.shape == (EMBEDDING_DIM,)
