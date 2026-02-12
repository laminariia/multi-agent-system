"""Unit tests for src/knowledge/ingestion.py.

Tests KnowledgeIngestionPipeline for directory scanning, JSON/text parsing, and database upserts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

if TYPE_CHECKING:
    pass


@pytest.fixture
def mock_embedding_service():
    """Mock EmbeddingService with embed_text returning fake 768-dim vector."""
    mock = MagicMock()
    mock.embed_text = AsyncMock(return_value=[0.1] * 768)
    return mock


@pytest.fixture
def mock_db_pool():
    """Mock asyncpg.Pool with acquire context manager."""
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    mock_pool = MagicMock()
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    return mock_pool


class TestSupportedExtensions:
    """Tests for _SUPPORTED_EXTENSIONS constant."""

    def test_supported_extensions_contents(self):
        """_SUPPORTED_EXTENSIONS contains .md, .json, .txt."""
        from src.knowledge.ingestion import _SUPPORTED_EXTENSIONS

        assert _SUPPORTED_EXTENSIONS == frozenset({".md", ".json", ".txt"})


class TestIngestDocument:
    """Tests for KnowledgeIngestionPipeline.ingest_document."""

    @pytest.mark.asyncio
    async def test_ingest_document_success_with_all_fields(self, mock_embedding_service, mock_db_pool):
        """ingest_document with all fields returns UUID string."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_document(
            title="Test Doc",
            content="Sample content",
            kb_type="proposal_template",
            category="web_development",
            success_rate=0.85,
        )
        assert isinstance(result, str)
        assert len(result) == 36

    @pytest.mark.asyncio
    async def test_ingest_document_without_optional_fields(self, mock_embedding_service, mock_db_pool):
        """ingest_document without optional fields returns UUID."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_document(
            title="Minimal Doc",
            content="Just content",
            kb_type="portfolio",
        )
        assert isinstance(result, str)
        assert len(result) == 36

    @pytest.mark.asyncio
    async def test_ingest_document_calls_embed_text(self, mock_embedding_service, mock_db_pool):
        """ingest_document calls embed_text with content."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        await pipeline.ingest_document(
            title="Test",
            content="Content to embed",
            kb_type="test",
        )
        mock_embedding_service.embed_text.assert_awaited_once_with("Content to embed")

    @pytest.mark.asyncio
    async def test_ingest_document_calls_upsert_row(self, mock_embedding_service, mock_db_pool):
        """ingest_document calls _upsert_row with correct params."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "_upsert_row", new_callable=AsyncMock) as mock_upsert:
            await pipeline.ingest_document(
                title="Doc",
                content="Content",
                kb_type="test",
                category="cat",
                success_rate=0.9,
            )
            mock_upsert.assert_awaited_once()
            call_kwargs = mock_upsert.call_args.kwargs
            assert call_kwargs["title"] == "Doc"
            assert call_kwargs["content"] == "Content"
            assert call_kwargs["kb_type"] == "test"
            assert call_kwargs["category"] == "cat"
            assert call_kwargs["success_rate"] == 0.9
            assert len(call_kwargs["embedding"]) == 768


class TestIngestDirectory:
    """Tests for KnowledgeIngestionPipeline.ingest_directory."""

    @pytest.mark.asyncio
    async def test_ingest_directory_empty_dir_returns_zero(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """ingest_directory with empty dir returns 0."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_directory(str(tmp_path), "test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_directory_non_existent_dir_returns_zero(self, mock_embedding_service, mock_db_pool):
        """ingest_directory with non-existent dir returns 0."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_directory("/non/existent/path", "test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_directory_filters_by_extension(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """ingest_directory filters by supported extensions."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        (tmp_path / "test.md").write_text("Markdown content")
        (tmp_path / "test.txt").write_text("Text content")
        (tmp_path / "test.json").write_text('{"title": "JSON", "content": "JSON content"}')
        (tmp_path / "test.py").write_text("# Python code")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_directory(str(tmp_path), "test")
        assert result == 3

    @pytest.mark.asyncio
    async def test_ingest_directory_skips_unsupported_files(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """ingest_directory skips unsupported file extensions."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        (tmp_path / "doc.docx").write_text("Word doc")
        (tmp_path / "data.csv").write_text("a,b,c")
        (tmp_path / "readme.md").write_text("Supported")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_directory(str(tmp_path), "test")
        assert result == 1

    @pytest.mark.asyncio
    async def test_ingest_directory_handles_file_errors_gracefully(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """ingest_directory continues on file errors."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        (tmp_path / "good.txt").write_text("Good file")
        (tmp_path / "bad.txt").write_text("Bad file")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "_ingest_file", side_effect=[1, Exception("Parse error")]) as mock_ingest:
            result = await pipeline.ingest_directory(str(tmp_path), "test")
            assert result == 1
            assert mock_ingest.call_count == 2

    @pytest.mark.asyncio
    async def test_ingest_directory_recursive_scan(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """ingest_directory scans subdirectories recursively."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        (tmp_path / "root.txt").write_text("Root file")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("Nested file")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline.ingest_directory(str(tmp_path), "test")
        assert result == 2


class TestIngestJsonFile:
    """Tests for KnowledgeIngestionPipeline._ingest_json_file."""

    @pytest.mark.asyncio
    async def test_ingest_json_file_array_of_entries(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_json_file handles array of entries."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        json_path = tmp_path / "data.json"
        json_path.write_text(
            json.dumps(
                [
                    {"title": "Entry 1", "content": "Content 1"},
                    {"title": "Entry 2", "content": "Content 2"},
                ]
            )
        )

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_json_file(json_path, "test")
        assert result == 2

    @pytest.mark.asyncio
    async def test_ingest_json_file_single_dict(self, mock_embedding_service, mock_db_pool, tmp_path: Path):
        """_ingest_json_file handles single dict entry."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        json_path = tmp_path / "single.json"
        json_path.write_text(json.dumps({"title": "Single Entry", "content": "Single Content"}))

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_json_file(json_path, "test")
        assert result == 1

    @pytest.mark.asyncio
    async def test_ingest_json_file_empty_content_skipped(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_json_file skips entries with empty content."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        json_path = tmp_path / "empty.json"
        json_path.write_text(
            json.dumps(
                [
                    {"title": "Entry 1", "content": "Valid content"},
                    {"title": "Entry 2", "content": ""},
                    {"title": "Entry 3"},
                ]
            )
        )

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_json_file(json_path, "test")
        assert result == 1

    @pytest.mark.asyncio
    async def test_ingest_json_file_unexpected_root_type(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_json_file handles unexpected root types."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        json_path = tmp_path / "string.json"
        json_path.write_text(json.dumps("just a string"))

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_json_file(json_path, "test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_json_file_category_and_success_rate(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_json_file extracts category and success_rate."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        json_path = tmp_path / "full.json"
        json_path.write_text(
            json.dumps(
                {
                    "title": "Full Entry",
                    "content": "Full content",
                    "category": "design",
                    "success_rate": 0.95,
                }
            )
        )

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "ingest_document", new_callable=AsyncMock) as mock_ingest:
            await pipeline._ingest_json_file(json_path, "test")
            mock_ingest.assert_awaited_once()
            call_kwargs = mock_ingest.call_args.kwargs
            assert call_kwargs["category"] == "design"
            assert call_kwargs["success_rate"] == 0.95

    @pytest.mark.asyncio
    async def test_ingest_json_file_uses_filename_for_missing_title(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_json_file uses filename stem as fallback title."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        json_path = tmp_path / "fallback_title.json"
        json_path.write_text(json.dumps({"content": "Content without title"}))

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "ingest_document", new_callable=AsyncMock) as mock_ingest:
            await pipeline._ingest_json_file(json_path, "test")
            mock_ingest.assert_awaited_once()
            call_kwargs = mock_ingest.call_args.kwargs
            assert call_kwargs["title"] == "fallback_title"


class TestIngestTextFile:
    """Tests for KnowledgeIngestionPipeline._ingest_text_file."""

    @pytest.mark.asyncio
    async def test_ingest_text_file_normal_file(self, mock_embedding_service, mock_db_pool, tmp_path: Path):
        """_ingest_text_file ingests normal text file."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        text_path = tmp_path / "document.txt"
        text_path.write_text("This is the file content.")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_text_file(text_path, "test")
        assert result == 1

    @pytest.mark.asyncio
    async def test_ingest_text_file_empty_file_skipped(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_text_file skips empty files."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        text_path = tmp_path / "empty.txt"
        text_path.write_text("")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_text_file(text_path, "test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_text_file_whitespace_only_skipped(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_text_file skips whitespace-only files."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        text_path = tmp_path / "whitespace.txt"
        text_path.write_text("   \n\n   ")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        result = await pipeline._ingest_text_file(text_path, "test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_text_file_title_derived_from_filename_underscores(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_text_file converts underscores to spaces and title-cases."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        text_path = tmp_path / "my_test_document.txt"
        text_path.write_text("Content here")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "ingest_document", new_callable=AsyncMock) as mock_ingest:
            await pipeline._ingest_text_file(text_path, "test")
            mock_ingest.assert_awaited_once()
            call_kwargs = mock_ingest.call_args.kwargs
            assert call_kwargs["title"] == "My Test Document"

    @pytest.mark.asyncio
    async def test_ingest_text_file_title_derived_from_filename_hyphens(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_text_file converts hyphens to spaces and title-cases."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        text_path = tmp_path / "another-test-file.md"
        text_path.write_text("Markdown content")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "ingest_document", new_callable=AsyncMock) as mock_ingest:
            await pipeline._ingest_text_file(text_path, "test")
            mock_ingest.assert_awaited_once()
            call_kwargs = mock_ingest.call_args.kwargs
            assert call_kwargs["title"] == "Another Test File"

    @pytest.mark.asyncio
    async def test_ingest_text_file_title_mixed_separators(
        self, mock_embedding_service, mock_db_pool, tmp_path: Path
    ):
        """_ingest_text_file handles mixed underscores and hyphens."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        text_path = tmp_path / "mixed_test-file.txt"
        text_path.write_text("Content")

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        with patch.object(pipeline, "ingest_document", new_callable=AsyncMock) as mock_ingest:
            await pipeline._ingest_text_file(text_path, "test")
            call_kwargs = mock_ingest.call_args.kwargs
            assert call_kwargs["title"] == "Mixed Test File"


class TestUpsertRow:
    """Tests for KnowledgeIngestionPipeline._upsert_row."""

    @pytest.mark.asyncio
    async def test_upsert_row_calls_execute_with_correct_sql(self, mock_embedding_service, mock_db_pool):
        """_upsert_row calls conn.execute with correct SQL params."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        await pipeline._upsert_row(
            doc_id="123e4567-e89b-12d3-a456-426614174000",
            kb_type="proposal_template",
            category="web",
            title="Test Title",
            content="Test Content",
            embedding=[0.1] * 768,
            success_rate=0.8,
        )

        mock_conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        mock_conn.execute.assert_awaited_once()
        call_args = mock_conn.execute.call_args.args
        assert "INSERT INTO knowledge_base" in call_args[0]
        assert "ON CONFLICT (id) DO UPDATE" in call_args[0]
        assert call_args[1] == "123e4567-e89b-12d3-a456-426614174000"
        assert call_args[2] == "proposal_template"
        assert call_args[3] == "web"
        assert call_args[4] == "Test Title"
        assert call_args[5] == "Test Content"

    @pytest.mark.asyncio
    async def test_upsert_row_with_none_category(self, mock_embedding_service, mock_db_pool):
        """_upsert_row handles None category."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        await pipeline._upsert_row(
            doc_id="test-id",
            kb_type="test",
            category=None,
            title="Title",
            content="Content",
            embedding=[0.1] * 768,
            success_rate=None,
        )

        mock_conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        mock_conn.execute.assert_awaited_once()
        call_args = mock_conn.execute.call_args.args
        assert call_args[3] is None
        assert call_args[7] is None

    @pytest.mark.asyncio
    async def test_upsert_row_serializes_embedding_to_json(self, mock_embedding_service, mock_db_pool):
        """_upsert_row serializes embedding list to JSON string."""
        from src.knowledge.ingestion import KnowledgeIngestionPipeline

        pipeline = KnowledgeIngestionPipeline(mock_embedding_service, mock_db_pool)
        embedding = [0.1, 0.2, 0.3]
        await pipeline._upsert_row(
            doc_id="test-id",
            kb_type="test",
            category=None,
            title="Title",
            content="Content",
            embedding=embedding,
            success_rate=None,
        )

        mock_conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.execute.call_args.args
        assert call_args[6] == json.dumps(embedding)
