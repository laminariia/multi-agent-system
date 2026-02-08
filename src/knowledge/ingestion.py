"""Knowledge ingestion pipeline -- embed and upsert documents to PostgreSQL.

Supports directory scanning (``.md``, ``.json``, ``.txt`` files) as well as
single-document ingestion.  JSON files may contain an array of entries with
``title``, ``content``, ``category``, and ``success_rate`` fields.  Markdown
and plain-text files use the filename (without extension) as the title and the
entire file body as content.

All SQL is parameterized.  Embeddings are produced by :class:`EmbeddingService`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import structlog

from src.knowledge.embedding_service import EmbeddingService

logger = structlog.get_logger(__name__)

# Supported file extensions for directory scanning.
_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".md", ".json", ".txt"})


class KnowledgeIngestionPipeline:
    """Ingest documents into the ``knowledge_base`` table with embeddings.

    Parameters
    ----------
    embedding_service:
        Shared :class:`EmbeddingService` for generating 768-dim vectors.
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

    async def ingest_directory(
        self,
        directory: str,
        kb_type: str = "proposal_template",
    ) -> int:
        """Scan *directory* for ``.md``, ``.json``, and ``.txt`` files, embed
        them, and upsert into the database.

        Parameters
        ----------
        directory:
            Filesystem path to scan (recursively).
        kb_type:
            Value for the ``type`` column in ``knowledge_base``.

        Returns
        -------
        int
            Number of documents successfully ingested.
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            logger.error("ingest_directory_not_found", directory=directory)
            return 0

        ingested = 0
        for file_path in sorted(dir_path.rglob("*")):
            if file_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                continue
            if not file_path.is_file():
                continue

            try:
                count = await self._ingest_file(file_path, kb_type)
                ingested += count
            except Exception:
                logger.error("ingest_file_failed", path=str(file_path), exc_info=True)

        logger.info("ingest_directory_complete", directory=directory, kb_type=kb_type, count=ingested)
        return ingested

    async def ingest_document(
        self,
        title: str,
        content: str,
        kb_type: str,
        category: str | None = None,
        success_rate: float | None = None,
    ) -> str:
        """Embed and upsert a single document.

        Parameters
        ----------
        title:
            Human-readable document title.
        content:
            Full document text to embed.
        kb_type:
            Knowledge base type (``"proposal_template"``, ``"portfolio"``, etc.).
        category:
            Optional category tag (``"web_development"``, ``"design"``, etc.).
        success_rate:
            Optional historical success rate (0.0 -- 1.0).

        Returns
        -------
        str
            UUID of the inserted/updated row.
        """
        embedding = await self._embeddings.embed_text(content)
        doc_id = str(uuid.uuid4())

        await self._upsert_row(
            doc_id=doc_id,
            kb_type=kb_type,
            category=category,
            title=title,
            content=content,
            embedding=embedding,
            success_rate=success_rate,
        )

        logger.info(
            "document_ingested",
            doc_id=doc_id,
            title=title[:80],
            kb_type=kb_type,
            category=category,
        )
        return doc_id

    # ------------------------------------------------------------------
    # File parsing
    # ------------------------------------------------------------------

    async def _ingest_file(self, file_path: Path, kb_type: str) -> int:
        """Parse and ingest a single file.  Returns number of documents ingested."""
        suffix = file_path.suffix.lower()

        if suffix == ".json":
            return await self._ingest_json_file(file_path, kb_type)
        return await self._ingest_text_file(file_path, kb_type)

    async def _ingest_json_file(self, file_path: Path, kb_type: str) -> int:
        """Parse a JSON file (expected: array of entry objects) and ingest each."""
        raw = file_path.read_text(encoding="utf-8")
        data: Any = json.loads(raw)

        # Accept both a list of entries and a single entry dict.
        entries: list[dict[str, Any]]
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = [data]
        else:
            logger.warning("json_unexpected_root_type", path=str(file_path), type=type(data).__name__)
            return 0

        count = 0
        for entry in entries:
            title = entry.get("title", file_path.stem)
            content = entry.get("content", "")
            if not content:
                continue
            category = entry.get("category")
            success_rate = entry.get("success_rate")
            if success_rate is not None:
                success_rate = float(success_rate)

            await self.ingest_document(
                title=title,
                content=content,
                kb_type=kb_type,
                category=category,
                success_rate=success_rate,
            )
            count += 1

        return count

    async def _ingest_text_file(self, file_path: Path, kb_type: str) -> int:
        """Ingest a ``.md`` or ``.txt`` file as a single document."""
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            logger.debug("skipping_empty_file", path=str(file_path))
            return 0

        title = file_path.stem.replace("_", " ").replace("-", " ").title()
        await self.ingest_document(title=title, content=content, kb_type=kb_type)
        return 1

    # ------------------------------------------------------------------
    # Database upsert
    # ------------------------------------------------------------------

    async def _upsert_row(
        self,
        *,
        doc_id: str,
        kb_type: str,
        category: str | None,
        title: str,
        content: str,
        embedding: list[float],
        success_rate: float | None,
    ) -> None:
        """INSERT ... ON CONFLICT DO UPDATE for a knowledge_base row."""
        embedding_json = json.dumps(embedding)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO knowledge_base
                    (id, type, category, title, content, embedding, success_rate)
                VALUES
                    ($1::uuid, $2, $3, $4, $5, $6::vector, $7)
                ON CONFLICT (id) DO UPDATE
                    SET content   = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        category  = EXCLUDED.category,
                        title     = EXCLUDED.title,
                        success_rate = EXCLUDED.success_rate,
                        updated_at = NOW()
                """,
                doc_id,
                kb_type,
                category,
                title,
                content,
                embedding_json,
                success_rate,
            )
