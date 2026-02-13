"""Unit tests for src.core.semantic_cache.SemanticCache.

All embedding calls and database operations are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.core.semantic_cache import (
    EMBEDDING_DIM,
    NEVER_CACHE_TYPES,
    SIMILARITY_THRESHOLD,
    TTL_MAP,
    SemanticCache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_embedding(dim: int = EMBEDDING_DIM) -> np.ndarray:
    """Return a deterministic unit-norm embedding vector."""
    vec = np.ones(dim, dtype=np.float32) / np.sqrt(dim)
    return vec


@pytest.fixture()
def cache(mock_valkey: AsyncMock, mock_db_pool: AsyncMock) -> SemanticCache:
    """SemanticCache wired to mock infrastructure with patched embeddings."""
    with patch("src.core.semantic_cache.OpenAIEmbeddings"):
        c = SemanticCache(mock_valkey, mock_db_pool, similarity_threshold=0.92)
    # Replace the internal embeddings model with an async mock that returns a vector.
    c._embeddings = AsyncMock()
    c._embeddings.aembed_query = AsyncMock(return_value=_mock_embedding().tolist())
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_returns_none_on_miss(cache: SemanticCache):
    """A cache miss (no matching entries in either layer) should return None."""
    result = await cache.get("What is React?", query_type="default")
    assert result is None


async def test_set_and_get_roundtrip(cache: SemanticCache, mock_valkey: AsyncMock):
    """After set(), a subsequent get() with the same query should find the cached response.

    We simulate the Valkey search returning a hit after storage.
    """
    await cache.set("What is React?", "React is a JS library.", query_type="default")

    # Verify _valkey_store was attempted (hset + expire).
    mock_valkey.hset.assert_awaited()
    mock_valkey.expire.assert_awaited()

    # Now simulate Valkey returning the cached doc on the next search.
    search_result = MagicMock()
    doc = MagicMock()
    doc.score = 0.05  # cosine distance -- similarity = 1.0 - 0.05 = 0.95 > threshold
    doc.response = "React is a JS library."
    doc.query_type = "default"
    doc.timestamp = str(1e12)  # a recent timestamp
    search_result.docs = [doc]

    ft_mock = MagicMock()
    ft_mock.search = AsyncMock(return_value=search_result)
    ft_mock.create_index = AsyncMock()
    mock_valkey.ft = MagicMock(return_value=ft_mock)

    result = await cache.get("What is React?", query_type="default")
    assert result == "React is a JS library."


async def test_never_cache_types_bypassed(cache: SemanticCache):
    """Queries with a type in NEVER_CACHE_TYPES should always return None and skip storage."""
    for cache_type in NEVER_CACHE_TYPES:
        result = await cache.get("anything", query_type=cache_type)
        assert result is None

    # set() should also be a no-op for never-cache types.
    for cache_type in NEVER_CACHE_TYPES:
        await cache.set("anything", "response", query_type=cache_type)

    # _embed should NOT have been called for never-cache types.
    # (Since get and set both return early before calling _embed.)
    cache._embeddings.aembed_query.assert_not_awaited()


def test_ttl_map_has_expected_keys():
    """TTL_MAP should contain entries for the documented query types."""
    expected_keys = {"proposal", "code", "content", "translation", "default"}
    assert set(TTL_MAP.keys()) == expected_keys

    # All TTL values should be positive integers (seconds).
    for key, ttl in TTL_MAP.items():
        assert isinstance(ttl, int), f"TTL for '{key}' is not int: {type(ttl)}"
        assert ttl > 0, f"TTL for '{key}' is not positive: {ttl}"


async def test_invalidate_by_type(cache: SemanticCache, mock_db_pool: AsyncMock):
    """invalidate_by_type should delete matching entries from PostgreSQL."""
    # Configure mock to return a DELETE result.
    conn = mock_db_pool._test_conn
    conn.execute = AsyncMock(return_value="DELETE 3")

    deleted_count = await cache.invalidate_by_type("proposal")

    assert deleted_count == 3
    conn.execute.assert_awaited()


def test_constants_have_correct_values():
    """Verify that module-level constants match the documented specification."""
    assert SIMILARITY_THRESHOLD == 0.92
    assert EMBEDDING_DIM == 3072
    assert "real_time_data" in NEVER_CACHE_TYPES
    assert "random_generation" in NEVER_CACHE_TYPES
    assert "personalized" in NEVER_CACHE_TYPES
