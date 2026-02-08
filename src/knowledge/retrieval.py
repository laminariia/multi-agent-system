"""Knowledge retrieval via pgvector cosine similarity search.

Performs vector search against the ``knowledge_base`` table, then reranks
results using a weighted combination of semantic similarity (70 %) and
historical success rate (30 %).

All SQL queries are fully parameterized -- no string interpolation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog

from src.knowledge.embedding_service import EmbeddingService

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class KnowledgeResult:
    """A single knowledge-base search result."""

    id: str
    title: str
    content: str
    category: str | None
    similarity: float
    success_rate: float | None
    final_score: float = field(default=0.0)


class KnowledgeRetriever:
    """Vector-similarity retriever backed by PostgreSQL + pgvector.

    Parameters
    ----------
    embedding_service:
        Shared :class:`EmbeddingService` for query embedding.
    db_pool:
        ``asyncpg.Pool`` connected to the MAS PostgreSQL database.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        db_pool: asyncpg.Pool,
    ) -> None:
        self._embeddings = embedding_service
        self._pool = db_pool

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        kb_type: str | None = None,
        category: str | None = None,
        top_k: int = 3,
        min_similarity: float = 0.7,
    ) -> list[KnowledgeResult]:
        """Search the knowledge base for entries similar to *query*.

        Parameters
        ----------
        query:
            Free-text search query (will be embedded).
        kb_type:
            Filter by ``knowledge_base.type`` (e.g. ``"proposal_template"``).
        category:
            Filter by ``knowledge_base.category`` (e.g. ``"web_development"``).
        top_k:
            Maximum number of results to return.
        min_similarity:
            Minimum cosine similarity threshold (0..1).

        Returns
        -------
        list[KnowledgeResult]
            Results sorted by *final_score* descending (similarity * 0.7 +
            success_rate * 0.3).
        """
        # 1. Embed the query text.
        query_vec = await self._embeddings.embed_text(query)
        vec_json = json.dumps(query_vec)

        # 2. Build the SQL query dynamically (all values parameterized).
        sql, params = self._build_search_query(
            vec_json=vec_json,
            kb_type=kb_type,
            category=category,
            top_k=top_k,
            min_similarity=min_similarity,
        )

        # 3. Execute.
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
        except Exception:
            logger.error("knowledge_search_failed", exc_info=True)
            return []

        # 4. Map rows to dataclass and rerank.
        results = self._rerank(rows)

        logger.debug(
            "knowledge_search_complete",
            query_len=len(query),
            kb_type=kb_type,
            category=category,
            results=len(results),
        )
        return results[:top_k]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_search_query(
        *,
        vec_json: str,
        kb_type: str | None,
        category: str | None,
        top_k: int,
        min_similarity: float,
    ) -> tuple[str, list[Any]]:
        """Construct a parameterized SQL query for pgvector cosine search.

        Returns ``(sql_string, list_of_params)``.
        """
        # Start building WHERE clauses and parameter list.
        conditions: list[str] = []
        params: list[Any] = []
        param_idx = 1  # asyncpg uses $1, $2, ...

        # The embedding vector is always the first parameter.
        params.append(vec_json)
        vec_param = f"${param_idx}"
        param_idx += 1

        # Minimum similarity filter.
        params.append(min_similarity)
        conditions.append(f"1 - (embedding <=> {vec_param}::vector) >= ${param_idx}")
        param_idx += 1

        # Optional type filter.
        if kb_type is not None:
            params.append(kb_type)
            conditions.append(f"type = ${param_idx}")
            param_idx += 1

        # Optional category filter.
        if category is not None:
            params.append(category)
            conditions.append(f"category = ${param_idx}")
            param_idx += 1

        # Limit.
        # Fetch a wider set (3x) so post-reranking still returns top_k.
        fetch_limit = top_k * 3
        params.append(fetch_limit)
        limit_param = f"${param_idx}"

        where_clause = " AND ".join(conditions)

        parts = [
            "SELECT id, title, content, category, success_rate,",
            " 1 - (embedding <=> " + vec_param + "::vector) AS similarity",  # noqa: S608
            " FROM knowledge_base WHERE " + where_clause,
            " ORDER BY embedding <=> " + vec_param + "::vector",
            " LIMIT " + limit_param,
        ]
        sql = "".join(parts)
        return sql, params

    @staticmethod
    def _rerank(rows: list[asyncpg.Record]) -> list[KnowledgeResult]:
        """Rerank results by weighted score: similarity * 0.7 + success_rate * 0.3."""
        results: list[KnowledgeResult] = []

        for row in rows:
            similarity = float(row["similarity"])
            sr = float(row["success_rate"]) if row["success_rate"] is not None else 0.0
            final_score = similarity * 0.7 + sr * 0.3

            results.append(
                KnowledgeResult(
                    id=str(row["id"]),
                    title=row["title"],
                    content=row["content"],
                    category=row["category"],
                    similarity=similarity,
                    success_rate=float(row["success_rate"]) if row["success_rate"] is not None else None,
                    final_score=final_score,
                )
            )

        results.sort(key=lambda r: r.final_score, reverse=True)
        return results
