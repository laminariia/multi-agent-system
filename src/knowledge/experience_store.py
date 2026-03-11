"""Experience Store — save and retrieve project experiences for RAG.

A thin wrapper around the ``knowledge_base`` table with ``type='experience'``.
Per MASTER-VISION §I.15, experience_store is a subset of knowledge_base —
no separate table needed.

Spec: docs/Full_work/specs/rag-memory-spec.md §Experience Store
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import structlog

from src.knowledge.embedding_service import EmbeddingService

logger = structlog.get_logger(__name__)

EXPERIENCE_TYPE = "experience"

EXPERIENCE_CATEGORIES: frozenset[str] = frozenset(
    {
        "bid",
        "code",
        "negotiation",
        "estimation",
        "client_feedback",
    }
)


@dataclass(frozen=True, slots=True)
class ExperienceResult:
    """A single experience search result."""

    id: str
    category: str | None
    title: str
    content: str
    similarity: float | None
    success_rate: float | None
    usage_count: int


class ExperienceStore:
    """Save and retrieve project experiences using knowledge_base + pgvector.

    Parameters
    ----------
    embedding_service:
        Shared :class:`EmbeddingService` for generating embeddings.
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
    # Save
    # ------------------------------------------------------------------

    async def save_experience(
        self,
        category: str,
        title: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        success_score: float | None = None,
    ) -> uuid.UUID:
        """Save a project experience to the knowledge base.

        Returns the UUID of the created entry.
        """
        if not title or not title.strip():
            raise ValueError("title must not be empty")
        if not content or not content.strip():
            raise ValueError("content must not be empty")

        # Clamp success_score to [0, 1]
        clamped_score: float | None = None
        if success_score is not None:
            clamped_score = max(0.0, min(1.0, success_score))

        # Generate embedding
        embedding = await self._embeddings.embed(content)

        async with self._pool.acquire() as conn:
            entry_id = await conn.fetchval(
                """
                INSERT INTO knowledge_base
                    (id, type, category, title, content, embedding, success_rate, usage_count)
                VALUES
                    (gen_random_uuid(), $1, $2, $3, $4, $5, $6, 0)
                RETURNING id
                """,
                EXPERIENCE_TYPE,
                category,
                title.strip(),
                content.strip(),
                str(embedding),
                clamped_score,
            )

        logger.info(
            "experience.saved",
            id=str(entry_id),
            category=category,
            title=title[:50],
        )
        return entry_id

    # ------------------------------------------------------------------
    # Retrieve similar
    # ------------------------------------------------------------------

    async def retrieve_similar(
        self,
        query: str,
        *,
        category: str | None = None,
        top_k: int = 5,
        min_similarity: float = 0.65,
    ) -> list[ExperienceResult]:
        """Find experiences similar to *query* via cosine vector search.

        Results are reranked by weighted score:
        ``0.7 * similarity + 0.3 * success_rate``.
        """
        embedding = await self._embeddings.embed(query)

        if category:
            sql = """
                SELECT id, category, title, content,
                       1 - (embedding <=> $1::vector) AS similarity,
                       success_rate, usage_count
                FROM knowledge_base
                WHERE type = $2 AND category = $3
                  AND 1 - (embedding <=> $1::vector) >= $4
                ORDER BY embedding <=> $1::vector
                LIMIT $5
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    sql,
                    str(embedding),
                    EXPERIENCE_TYPE,
                    category,
                    min_similarity,
                    top_k,
                )
        else:
            sql = """
                SELECT id, category, title, content,
                       1 - (embedding <=> $1::vector) AS similarity,
                       success_rate, usage_count
                FROM knowledge_base
                WHERE type = $2
                  AND 1 - (embedding <=> $1::vector) >= $3
                ORDER BY embedding <=> $1::vector
                LIMIT $4
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    sql,
                    str(embedding),
                    EXPERIENCE_TYPE,
                    min_similarity,
                    top_k,
                )

        return [
            ExperienceResult(
                id=str(row["id"]),
                category=row["category"],
                title=row["title"],
                content=row["content"],
                similarity=float(row["similarity"]) if row["similarity"] else None,
                success_rate=float(row["success_rate"]) if row["success_rate"] else None,
                usage_count=row["usage_count"] or 0,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Record outcome
    # ------------------------------------------------------------------

    async def record_outcome(
        self,
        experience_id: str,
        success: bool,
    ) -> None:
        """Update success_rate based on outcome feedback.

        Uses incremental average:
        ``new_rate = (old_rate * count + score) / (count + 1)``
        """
        score = 1.0 if success else 0.0

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE knowledge_base
                SET success_rate = COALESCE(
                    (COALESCE(success_rate, 0) * usage_count + $1) / (usage_count + 1),
                    $1
                ),
                usage_count = usage_count + 1
                WHERE id = $2 AND type = $3
                """,
                score,
                experience_id,
                EXPERIENCE_TYPE,
            )

        logger.info(
            "experience.outcome_recorded",
            id=experience_id,
            success=success,
        )

    # ------------------------------------------------------------------
    # Get by category
    # ------------------------------------------------------------------

    async def get_by_category(
        self,
        category: str,
        limit: int = 10,
    ) -> list[ExperienceResult]:
        """Fetch experiences by category, ordered by success_rate desc."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, category, title, content, success_rate, usage_count
                FROM knowledge_base
                WHERE type = $1 AND category = $2
                ORDER BY success_rate DESC NULLS LAST
                LIMIT $3
                """,
                EXPERIENCE_TYPE,
                category,
                limit,
            )

        return [
            ExperienceResult(
                id=str(row["id"]),
                category=row["category"],
                title=row["title"],
                content=row["content"],
                similarity=None,
                success_rate=float(row["success_rate"]) if row["success_rate"] else None,
                usage_count=row["usage_count"] or 0,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_experience(self, experience_id: str) -> bool:
        """Delete an experience entry. Returns True if it existed."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM knowledge_base
                WHERE id = $1 AND type = $2
                """,
                experience_id,
                EXPERIENCE_TYPE,
            )

        deleted = result.endswith("1")
        if deleted:
            logger.info("experience.deleted", id=experience_id)
        return deleted
