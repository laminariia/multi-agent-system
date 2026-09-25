"""CLI for knowledge base management.

Usage::

    python -m src.knowledge.cli ingest --dir knowledge/proposals --type proposal_template
    python -m src.knowledge.cli search --query "React landing page" --type proposal_template
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog

logger = structlog.get_logger(__name__)


async def _run_ingest(args: argparse.Namespace) -> None:
    """Execute the ``ingest`` sub-command."""
    import asyncpg  # noqa: PLC0415

    from src.knowledge.embedding_service import EmbeddingService  # noqa: PLC0415
    from src.knowledge.ingestion import KnowledgeIngestionPipeline  # noqa: PLC0415

    db_url = os.environ.get("DATABASE_URL", "postgresql://mas:mas_password@localhost:5432/mas")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    if pool is None:
        raise RuntimeError("Failed to create database connection pool")

    try:
        embedding_svc = EmbeddingService()
        pipeline = KnowledgeIngestionPipeline(embedding_svc, pool)
        count = await pipeline.ingest_directory(args.dir, kb_type=args.type)
        print(f"Ingested {count} document(s) from '{args.dir}' as type='{args.type}'")  # noqa: T201
    finally:
        await pool.close()


async def _run_search(args: argparse.Namespace) -> None:
    """Execute the ``search`` sub-command."""
    import asyncpg  # noqa: PLC0415

    from src.knowledge.embedding_service import EmbeddingService  # noqa: PLC0415
    from src.knowledge.retrieval import KnowledgeRetriever  # noqa: PLC0415

    db_url = os.environ.get("DATABASE_URL", "postgresql://mas:mas_password@localhost:5432/mas")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    if pool is None:
        raise RuntimeError("Failed to create database connection pool")

    try:
        embedding_svc = EmbeddingService()
        retriever = KnowledgeRetriever(embedding_svc, pool)
        results = await retriever.search(
            args.query,
            kb_type=args.type,
            category=args.category,
            top_k=args.top_k,
        )

        if not results:
            print("No results found.")  # noqa: T201
            return

        for idx, r in enumerate(results, 1):
            print(  # noqa: T201
                f"\n--- Result {idx} (score={r.final_score:.3f}, "
                f"similarity={r.similarity:.3f}, "
                f"success_rate={r.success_rate}) ---"
            )
            print(f"Title:    {r.title}")  # noqa: T201
            print(f"Category: {r.category}")  # noqa: T201
            print(f"Content:  {r.content[:300]}...")  # noqa: T201
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="knowledge-cli",
        description="Knowledge Base management CLI for the Multi-Agent Service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- ingest ---
    ingest_p = sub.add_parser("ingest", help="Ingest documents from a directory")
    ingest_p.add_argument("--dir", required=True, help="Directory to scan for documents")
    ingest_p.add_argument(
        "--type",
        default="proposal_template",
        help="Knowledge base type (default: proposal_template)",
    )

    # -- search ---
    search_p = sub.add_parser("search", help="Search the knowledge base")
    search_p.add_argument("--query", required=True, help="Search query text")
    search_p.add_argument("--type", default=None, help="Filter by knowledge base type")
    search_p.add_argument("--category", default=None, help="Filter by category")
    search_p.add_argument("--top-k", type=int, default=3, dest="top_k", help="Number of results (default: 3)")

    return parser


def main() -> None:
    """Entry point for ``python -m src.knowledge.cli``."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        asyncio.run(_run_ingest(args))
    elif args.command == "search":
        asyncio.run(_run_search(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
