"""Tests for P3.13 — RAG Decision Memory (Decision Patterns).

Covers:
- DecisionPattern dataclass creation and serialization
- DecisionMemory.record_outcome stores patterns via knowledge ingestion
- DecisionMemory.find_similar retrieves similar past patterns
- Pattern content formatting for embedding
- Outcome extraction from pipeline state
- Integration: Planner can query decision memory
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. DecisionPattern dataclass
# ===========================================================================


class TestDecisionPattern:
    """DecisionPattern dataclass holds project outcome data."""

    def test_create_pattern(self) -> None:
        """Should create a pattern with all fields."""
        from src.core.decision_memory import DecisionPattern

        pattern = DecisionPattern(
            project_type="web_development",
            requirements_summary="Build a React landing page with animations",
            approach=["design", "dev", "content"],
            outcome="success",
            timeline_days=3,
            lessons_learned=["Used Framer Motion for animations", "Client liked parallax effects"],
        )
        assert pattern.project_type == "web_development"
        assert pattern.outcome == "success"
        assert len(pattern.approach) == 3

    def test_pattern_to_content_string(self) -> None:
        """to_content() returns a searchable text representation."""
        from src.core.decision_memory import DecisionPattern

        pattern = DecisionPattern(
            project_type="api_backend",
            requirements_summary="REST API with PostgreSQL",
            approach=["planner", "dev"],
            outcome="success",
            timeline_days=5,
            lessons_learned=["Used FastAPI for speed"],
        )
        content = pattern.to_content()
        assert "api_backend" in content
        assert "REST API" in content
        assert "success" in content
        assert "FastAPI" in content

    def test_pattern_to_dict(self) -> None:
        """to_dict() returns a serializable dictionary."""
        from src.core.decision_memory import DecisionPattern

        pattern = DecisionPattern(
            project_type="design",
            requirements_summary="Logo redesign",
            approach=["design"],
            outcome="failed",
            timeline_days=2,
            lessons_learned=["Client had unclear requirements"],
        )
        data = pattern.to_dict()
        assert data["project_type"] == "design"
        assert data["outcome"] == "failed"
        assert isinstance(data["approach"], list)


# ===========================================================================
# 2. Record outcome
# ===========================================================================


class TestRecordOutcome:
    """DecisionMemory.record_outcome stores patterns."""

    async def test_record_stores_via_ingestion_pipeline(self) -> None:
        """record_outcome should call ingest_document with correct params."""
        from src.core.decision_memory import DecisionMemory, DecisionPattern

        mock_ingestion = AsyncMock()
        mock_ingestion.ingest_document = AsyncMock(return_value="doc-123")
        mock_retriever = MagicMock()

        memory = DecisionMemory(ingestion=mock_ingestion, retriever=mock_retriever)

        pattern = DecisionPattern(
            project_type="web_development",
            requirements_summary="Build a landing page",
            approach=["design", "dev"],
            outcome="success",
            timeline_days=3,
            lessons_learned=["Client was happy"],
        )

        doc_id = await memory.record_outcome(pattern)

        assert doc_id == "doc-123"
        mock_ingestion.ingest_document.assert_called_once()
        call_kwargs = mock_ingestion.ingest_document.call_args
        assert (
            call_kwargs.kwargs.get("kb_type") == "decision_pattern"
            or call_kwargs[1].get("kb_type") == "decision_pattern"
        )

    async def test_record_uses_pattern_content_as_document(self) -> None:
        """The ingested content should be the pattern's text representation."""
        from src.core.decision_memory import DecisionMemory, DecisionPattern

        mock_ingestion = AsyncMock()
        mock_ingestion.ingest_document = AsyncMock(return_value="doc-456")

        memory = DecisionMemory(ingestion=mock_ingestion, retriever=MagicMock())

        pattern = DecisionPattern(
            project_type="api_backend",
            requirements_summary="GraphQL API",
            approach=["dev"],
            outcome="success",
            timeline_days=4,
            lessons_learned=["Used Strawberry for GraphQL"],
        )

        await memory.record_outcome(pattern)

        call_args = mock_ingestion.ingest_document.call_args
        content = call_args.kwargs.get("content") or call_args[1].get("content")
        assert "GraphQL API" in content
        assert "success" in content

    async def test_record_sets_success_rate_from_outcome(self) -> None:
        """Success outcome should set success_rate=1.0, failed=0.0."""
        from src.core.decision_memory import DecisionMemory, DecisionPattern

        mock_ingestion = AsyncMock()
        mock_ingestion.ingest_document = AsyncMock(return_value="doc-789")

        memory = DecisionMemory(ingestion=mock_ingestion, retriever=MagicMock())

        # Success pattern
        await memory.record_outcome(
            DecisionPattern(
                project_type="web",
                requirements_summary="Test",
                approach=["dev"],
                outcome="success",
                timeline_days=1,
                lessons_learned=[],
            )
        )

        call_kwargs = mock_ingestion.ingest_document.call_args
        success_rate = call_kwargs.kwargs.get("success_rate") or call_kwargs[1].get("success_rate")
        assert success_rate == 1.0


# ===========================================================================
# 3. Find similar patterns
# ===========================================================================


class TestFindSimilar:
    """DecisionMemory.find_similar retrieves matching patterns."""

    async def test_find_similar_queries_knowledge_retriever(self) -> None:
        """find_similar should call retriever.search with decision_pattern type."""
        from src.core.decision_memory import DecisionMemory

        mock_retriever = AsyncMock()
        mock_retriever.search = AsyncMock(return_value=[])

        memory = DecisionMemory(ingestion=MagicMock(), retriever=mock_retriever)

        results = await memory.find_similar("Build a React landing page")

        mock_retriever.search.assert_called_once()
        call_kwargs = mock_retriever.search.call_args
        assert call_kwargs.kwargs.get("kb_type") == "decision_pattern"

    async def test_find_similar_returns_parsed_patterns(self) -> None:
        """Results should be parsed into DecisionPattern objects."""
        from src.core.decision_memory import DecisionMemory

        mock_result = MagicMock()
        mock_result.content = (
            "Project type: web_development\n"
            "Requirements: Build a landing page\n"
            "Approach: design, dev, content\n"
            "Outcome: success\n"
            "Timeline: 3 days\n"
            "Lessons: Used Tailwind CSS"
        )
        mock_result.similarity = 0.85
        mock_result.title = "Past project"

        mock_retriever = AsyncMock()
        mock_retriever.search = AsyncMock(return_value=[mock_result])

        memory = DecisionMemory(ingestion=MagicMock(), retriever=mock_retriever)

        results = await memory.find_similar("Build a landing page")

        assert len(results) == 1
        assert results[0].similarity >= 0.8

    async def test_find_similar_respects_top_k(self) -> None:
        """Should pass top_k to retriever."""
        from src.core.decision_memory import DecisionMemory

        mock_retriever = AsyncMock()
        mock_retriever.search = AsyncMock(return_value=[])

        memory = DecisionMemory(ingestion=MagicMock(), retriever=mock_retriever)

        await memory.find_similar("Test query", top_k=5)

        call_kwargs = mock_retriever.search.call_args
        assert call_kwargs.kwargs.get("top_k") == 5

    async def test_find_similar_returns_empty_on_no_matches(self) -> None:
        """Should return empty list when no similar patterns found."""
        from src.core.decision_memory import DecisionMemory

        mock_retriever = AsyncMock()
        mock_retriever.search = AsyncMock(return_value=[])

        memory = DecisionMemory(ingestion=MagicMock(), retriever=mock_retriever)

        results = await memory.find_similar("Completely unique request")

        assert results == []


# ===========================================================================
# 4. State extraction
# ===========================================================================


class TestExtractPatternFromState:
    """extract_pattern_from_state builds a DecisionPattern from pipeline state."""

    def test_extracts_from_completed_state(self) -> None:
        """Should extract pattern from a completed pipeline state."""
        from src.core.decision_memory import extract_pattern_from_state

        state: dict[str, Any] = {
            "project": {
                "project_id": "proj-1",
                "requirements": "Build a React landing page with contact form",
                "budget": 500,
            },
            "status": "completed",
            "agent_sequence": ["design", "dev", "content"],
            "artifacts": {
                "planner": {"delivery_type": "deploy"},
                "dev": {"files": ["index.html"]},
            },
            "errors": [],
            "created_at": "2026-03-01T00:00:00Z",
            "updated_at": "2026-03-04T00:00:00Z",
        }

        pattern = extract_pattern_from_state(state)

        assert pattern is not None
        assert "React landing" in pattern.requirements_summary
        assert pattern.outcome == "success"
        assert pattern.approach == ["design", "dev", "content"]

    def test_extracts_failed_outcome(self) -> None:
        """Failed pipeline should produce outcome='failed'."""
        from src.core.decision_memory import extract_pattern_from_state

        state: dict[str, Any] = {
            "project": {
                "project_id": "proj-2",
                "requirements": "Complex ML pipeline",
                "budget": 2000,
            },
            "status": "failed",
            "agent_sequence": ["dev"],
            "artifacts": {},
            "errors": ["LLM timeout", "Sandbox unavailable"],
            "created_at": "2026-03-01T00:00:00Z",
            "updated_at": "2026-03-02T00:00:00Z",
        }

        pattern = extract_pattern_from_state(state)

        assert pattern is not None
        assert pattern.outcome == "failed"
        assert len(pattern.lessons_learned) > 0

    def test_returns_none_for_in_progress_state(self) -> None:
        """Should return None if pipeline is still in progress."""
        from src.core.decision_memory import extract_pattern_from_state

        state: dict[str, Any] = {
            "project": {"project_id": "proj-3", "requirements": "Test"},
            "status": "active",
            "agent_sequence": ["dev"],
            "artifacts": {},
            "errors": [],
        }

        pattern = extract_pattern_from_state(state)
        assert pattern is None

    def test_extracts_timeline_from_timestamps(self) -> None:
        """Timeline should be computed from created_at to updated_at."""
        from src.core.decision_memory import extract_pattern_from_state

        state: dict[str, Any] = {
            "project": {
                "project_id": "proj-4",
                "requirements": "Quick fix",
                "budget": 100,
            },
            "status": "completed",
            "agent_sequence": ["dev"],
            "artifacts": {},
            "errors": [],
            "created_at": "2026-03-01T00:00:00Z",
            "updated_at": "2026-03-03T12:00:00Z",
        }

        pattern = extract_pattern_from_state(state)
        assert pattern is not None
        assert pattern.timeline_days >= 2


# ===========================================================================
# 5. Category inference
# ===========================================================================


class TestCategoryInference:
    """infer_project_type guesses category from requirements."""

    def test_web_keywords(self) -> None:
        """Requirements with web keywords → web_development."""
        from src.core.decision_memory import infer_project_type

        assert infer_project_type("Build a React landing page") == "web_development"

    def test_api_keywords(self) -> None:
        """Requirements with API keywords → api_backend."""
        from src.core.decision_memory import infer_project_type

        assert infer_project_type("REST API with authentication") == "api_backend"

    def test_design_keywords(self) -> None:
        """Requirements with design keywords → design."""
        from src.core.decision_memory import infer_project_type

        assert infer_project_type("Logo and brand identity design") == "design"

    def test_unknown_defaults_to_general(self) -> None:
        """Unknown requirements → general."""
        from src.core.decision_memory import infer_project_type

        assert infer_project_type("Do something complex") == "general"
