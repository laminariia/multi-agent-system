"""Unit tests for src/knowledge/cli.py."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture


# Mirror the KnowledgeResult dataclass for test data
@dataclass(frozen=True, slots=True)
class KnowledgeResult:
    """Test copy of KnowledgeResult."""

    id: str
    title: str
    content: str
    category: str | None
    similarity: float
    success_rate: float | None
    final_score: float = field(default=0.0)


class TestBuildParser:
    """Test CLI argument parser construction."""

    def test_build_parser_returns_parser(self) -> None:
        """Parser is constructed successfully."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.prog == "knowledge-cli"

    def test_build_parser_has_subcommands(self) -> None:
        """Parser has ingest and search subcommands."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        # Parse with no args should fail because command is required
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_ingest_subcommand_required_args(self) -> None:
        """Ingest subcommand requires --dir."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        # Missing --dir should fail
        with pytest.raises(SystemExit):
            parser.parse_args(["ingest"])

    def test_ingest_subcommand_success(self) -> None:
        """Ingest subcommand parses correctly with required args."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["ingest", "--dir", "/tmp/docs"])
        assert args.command == "ingest"
        assert args.dir == "/tmp/docs"
        assert args.type == "proposal_template"  # default

    def test_ingest_subcommand_custom_type(self) -> None:
        """Ingest subcommand accepts custom --type."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["ingest", "--dir", "/tmp/docs", "--type", "portfolio"])
        assert args.type == "portfolio"

    def test_search_subcommand_required_args(self) -> None:
        """Search subcommand requires --query."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["search"])

    def test_search_subcommand_success(self) -> None:
        """Search subcommand parses correctly with required args."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["search", "--query", "test query"])
        assert args.command == "search"
        assert args.query == "test query"
        assert args.type is None  # default
        assert args.category is None  # default
        assert args.top_k == 3  # default

    def test_search_subcommand_all_filters(self) -> None:
        """Search subcommand accepts all optional filters."""
        from src.knowledge.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "search",
            "--query", "test",
            "--type", "proposal_template",
            "--category", "web_dev",
            "--top-k", "10",
        ])
        assert args.type == "proposal_template"
        assert args.category == "web_dev"
        assert args.top_k == 10


class TestRunIngest:
    """Test _run_ingest async function."""

    @pytest.mark.asyncio
    async def test_run_ingest_success(
        self,
        capsys: CaptureFixture[str],
    ) -> None:
        """Ingest succeeds and prints count."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=5)

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.ingestion.KnowledgeIngestionPipeline", return_value=mock_pipeline),
        ):
            from src.knowledge.cli import _run_ingest

            args = argparse.Namespace(dir="/tmp/docs", type="proposal_template")
            await _run_ingest(args)

        mock_pipeline.ingest_directory.assert_awaited_once_with(
            "/tmp/docs",
            kb_type="proposal_template",
        )
        mock_pool.close.assert_awaited_once()
        captured = capsys.readouterr()
        assert "Ingested 5 document(s)" in captured.out
        assert "'/tmp/docs'" in captured.out
        assert "type='proposal_template'" in captured.out

    @pytest.mark.asyncio
    async def test_run_ingest_pool_creation_failure(self) -> None:
        """Ingest raises RuntimeError if pool creation returns None."""
        # Need to import before patching since imports are deferred
        from src.knowledge.cli import _run_ingest

        with patch("asyncpg.create_pool", new=AsyncMock(return_value=None)):
            args = argparse.Namespace(dir="/tmp/docs", type="proposal_template")
            with pytest.raises(RuntimeError, match="Failed to create database connection pool"):
                await _run_ingest(args)

    @pytest.mark.asyncio
    async def test_run_ingest_pool_closed_on_error(self) -> None:
        """Pool is closed even if ingestion fails."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.ingest_directory = AsyncMock(side_effect=ValueError("Test error"))

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.ingestion.KnowledgeIngestionPipeline", return_value=mock_pipeline),
            pytest.raises(ValueError, match="Test error"),
        ):
            from src.knowledge.cli import _run_ingest

            args = argparse.Namespace(dir="/tmp/docs", type="proposal_template")
            await _run_ingest(args)

        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_ingest_custom_env_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: CaptureFixture[str],
    ) -> None:
        """Ingest uses DATABASE_URL from environment."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://custom:pass@host:5432/db")  # noqa: S105
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=2)

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)) as mock_create_pool,
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.ingestion.KnowledgeIngestionPipeline", return_value=mock_pipeline),
        ):
            from src.knowledge.cli import _run_ingest

            args = argparse.Namespace(dir="/data", type="portfolio")
            await _run_ingest(args)

        # Verify custom DB URL was used
        mock_create_pool.assert_awaited_once()
        call_args = mock_create_pool.call_args
        assert call_args[0][0] == "postgresql://custom:pass@host:5432/db"  # noqa: S105

    @pytest.mark.asyncio
    async def test_run_ingest_zero_documents(
        self,
        capsys: CaptureFixture[str],
    ) -> None:
        """Ingest handles zero documents gracefully."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=0)

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.ingestion.KnowledgeIngestionPipeline", return_value=mock_pipeline),
        ):
            from src.knowledge.cli import _run_ingest

            args = argparse.Namespace(dir="/empty", type="proposal_template")
            await _run_ingest(args)

        captured = capsys.readouterr()
        assert "Ingested 0 document(s)" in captured.out

    @pytest.mark.asyncio
    async def test_run_ingest_passes_correct_kb_type(self) -> None:
        """Ingest passes kb_type argument correctly."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.ingest_directory = AsyncMock(return_value=3)

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.ingestion.KnowledgeIngestionPipeline", return_value=mock_pipeline),
        ):
            from src.knowledge.cli import _run_ingest

            args = argparse.Namespace(dir="/docs", type="custom_type")
            await _run_ingest(args)

        mock_pipeline.ingest_directory.assert_awaited_once_with(
            "/docs",
            kb_type="custom_type",
        )


class TestRunSearch:
    """Test _run_search async function."""

    @pytest.mark.asyncio
    async def test_run_search_with_results(
        self,
        capsys: CaptureFixture[str],
    ) -> None:
        """Search prints formatted results."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()

        result = KnowledgeResult(
            id="1",
            title="Test Document",
            content="This is a test document with some content that is longer than 300 characters to test truncation",
            category="web_dev",
            similarity=0.95,
            success_rate=0.85,
            final_score=0.90,
        )
        mock_retriever.search = AsyncMock(return_value=[result])

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="test query",
                type=None,
                category=None,
                top_k=3,
            )
            await _run_search(args)

        mock_retriever.search.assert_awaited_once_with(
            "test query",
            kb_type=None,
            category=None,
            top_k=3,
        )
        mock_pool.close.assert_awaited_once()

        captured = capsys.readouterr()
        assert "Result 1" in captured.out
        assert "score=0.900" in captured.out
        assert "similarity=0.950" in captured.out
        assert "success_rate=0.85" in captured.out
        assert "Test Document" in captured.out
        assert "web_dev" in captured.out

    @pytest.mark.asyncio
    async def test_run_search_no_results(
        self,
        capsys: CaptureFixture[str],
    ) -> None:
        """Search prints 'No results found' when empty."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[])

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="nonexistent",
                type=None,
                category=None,
                top_k=3,
            )
            await _run_search(args)

        captured = capsys.readouterr()
        assert "No results found." in captured.out

    @pytest.mark.asyncio
    async def test_run_search_multiple_results(
        self,
        capsys: CaptureFixture[str],
    ) -> None:
        """Search formats multiple results correctly."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()

        results = [
            KnowledgeResult(
                id="1",
                title="First",
                content="A" * 400,  # Will be truncated
                category="cat1",
                similarity=0.95,
                success_rate=0.9,
                final_score=0.92,
            ),
            KnowledgeResult(
                id="2",
                title="Second",
                content="B" * 100,
                category="cat2",
                similarity=0.88,
                success_rate=None,
                final_score=0.88,
            ),
        ]
        mock_retriever.search = AsyncMock(return_value=results)

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="test",
                type=None,
                category=None,
                top_k=5,
            )
            await _run_search(args)

        captured = capsys.readouterr()
        assert "Result 1" in captured.out
        assert "Result 2" in captured.out
        assert "First" in captured.out
        assert "Second" in captured.out
        assert "cat1" in captured.out
        assert "cat2" in captured.out
        # Check truncation (300 chars + "...")
        assert captured.out.count("...") >= 1

    @pytest.mark.asyncio
    async def test_run_search_pool_creation_failure(self) -> None:
        """Search raises RuntimeError if pool creation returns None."""
        from src.knowledge.cli import _run_search

        with patch("asyncpg.create_pool", new=AsyncMock(return_value=None)):
            args = argparse.Namespace(
                query="test",
                type=None,
                category=None,
                top_k=3,
            )
            with pytest.raises(RuntimeError, match="Failed to create database connection pool"):
                await _run_search(args)

    @pytest.mark.asyncio
    async def test_run_search_pool_closed_on_error(self) -> None:
        """Pool is closed even if search fails."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(side_effect=RuntimeError("Search failed"))

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
            pytest.raises(RuntimeError, match="Search failed"),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="test",
                type=None,
                category=None,
                top_k=3,
            )
            await _run_search(args)

        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_search_custom_filters(self) -> None:
        """Search passes custom filters to retriever."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[])

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="custom",
                type="portfolio",
                category="mobile_dev",
                top_k=10,
            )
            await _run_search(args)

        mock_retriever.search.assert_awaited_once_with(
            "custom",
            kb_type="portfolio",
            category="mobile_dev",
            top_k=10,
        )

    @pytest.mark.asyncio
    async def test_run_search_custom_env_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Search uses DATABASE_URL from environment."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://search:pass@host:5432/db")  # noqa: S105
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[])

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)) as mock_create_pool,
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="test",
                type=None,
                category=None,
                top_k=3,
            )
            await _run_search(args)

        # Verify custom DB URL was used
        mock_create_pool.assert_awaited_once()
        call_args = mock_create_pool.call_args
        assert call_args[0][0] == "postgresql://search:pass@host:5432/db"  # noqa: S105

    @pytest.mark.asyncio
    async def test_run_search_content_truncation(
        self,
        capsys: CaptureFixture[str],
    ) -> None:
        """Search truncates content at 300 characters."""
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()
        mock_embedding_svc = MagicMock()
        mock_retriever = MagicMock()

        long_content = "X" * 500
        result = KnowledgeResult(
            id="1",
            title="Long Doc",
            content=long_content,
            category="test",
            similarity=0.9,
            success_rate=0.8,
            final_score=0.85,
        )
        mock_retriever.search = AsyncMock(return_value=[result])

        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("src.knowledge.embedding_service.EmbeddingService", return_value=mock_embedding_svc),
            patch("src.knowledge.retrieval.KnowledgeRetriever", return_value=mock_retriever),
        ):
            from src.knowledge.cli import _run_search

            args = argparse.Namespace(
                query="test",
                type=None,
                category=None,
                top_k=3,
            )
            await _run_search(args)

        captured = capsys.readouterr()
        # Should show exactly 300 chars + "..."
        assert long_content[:300] + "..." in captured.out
        # Should NOT show the full 500 chars
        assert long_content not in captured.out


class TestMain:
    """Test main() entry point."""

    def test_main_dispatch_to_ingest(self) -> None:
        """Main dispatches to _run_ingest for ingest command."""
        test_args = ["knowledge-cli", "ingest", "--dir", "/tmp/test"]

        with (
            patch.object(sys, "argv", test_args),
            patch("src.knowledge.cli._run_ingest") as mock_run_ingest,
            patch("asyncio.run") as mock_asyncio_run,
        ):
            from src.knowledge.cli import main

            main()

        mock_asyncio_run.assert_called_once()
        # Verify _run_ingest was called
        assert mock_run_ingest.called

    def test_main_dispatch_to_search(self) -> None:
        """Main dispatches to _run_search for search command."""
        test_args = ["knowledge-cli", "search", "--query", "test"]

        with (
            patch.object(sys, "argv", test_args),
            patch("src.knowledge.cli._run_search") as mock_run_search,
            patch("asyncio.run") as mock_asyncio_run,
        ):
            from src.knowledge.cli import main

            main()

        mock_asyncio_run.assert_called_once()
        assert mock_run_search.called

    def test_main_no_command_exits(self) -> None:
        """Main exits with error when no command provided."""
        test_args = ["knowledge-cli"]

        with (
            patch.object(sys, "argv", test_args),
            pytest.raises(SystemExit) as exc_info,
        ):
            from src.knowledge.cli import main

            main()

        # argparse exits with code 2 for argument errors
        assert exc_info.value.code == 2

    def test_main_ingest_missing_dir_exits(self) -> None:
        """Main exits when ingest command missing required --dir."""
        test_args = ["knowledge-cli", "ingest"]

        with (
            patch.object(sys, "argv", test_args),
            pytest.raises(SystemExit) as exc_info,
        ):
            from src.knowledge.cli import main

            main()

        assert exc_info.value.code == 2

    def test_main_search_missing_query_exits(self) -> None:
        """Main exits when search command missing required --query."""
        test_args = ["knowledge-cli", "search"]

        with (
            patch.object(sys, "argv", test_args),
            pytest.raises(SystemExit) as exc_info,
        ):
            from src.knowledge.cli import main

            main()

        assert exc_info.value.code == 2

    def test_main_invalid_command_exits(self) -> None:
        """Main exits when invalid command provided."""
        test_args = ["knowledge-cli", "invalid"]

        with (
            patch.object(sys, "argv", test_args),
            pytest.raises(SystemExit) as exc_info,
        ):
            from src.knowledge.cli import main

            main()

        assert exc_info.value.code == 2
