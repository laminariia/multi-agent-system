"""Unit tests for RAG wiring: ExperienceStore.retrieve_context + agent integration.

Tests cover:
- ExperienceStore.retrieve_context with mocked DB
- RAG injection into Bid agent prompt
- RAG injection into Dev agent prompt
- RAG injection into Scout agent prompt
- Graceful degradation when store is unavailable
- Semantic cache cleanup (TTL purge, LRU eviction)
- Edge cases (empty results, empty query, exceptions)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from src.agents.bid import BidAgent
from src.agents.dev import DevAgent
from src.agents.scout import ScoutAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state
from src.knowledge.experience_store import ExperienceResult, ExperienceStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-rag-test",
        "job_id": "job-rag-test",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a React landing page with Tailwind CSS",
        "budget": 500.0,
        "deadline": datetime(2026, 4, 1, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="scout", thread_id="thread-rag-test")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _mock_experience_results(count: int = 3) -> list[ExperienceResult]:
    """Create mock ExperienceResult objects."""
    results = []
    for i in range(count):
        results.append(
            ExperienceResult(
                id=f"exp-{i}",
                category="bid",
                title=f"Past project {i}",
                content=f"Sample experience content for project {i}",
                similarity=0.85 - (i * 0.05),
                success_rate=0.9 - (i * 0.1),
                usage_count=10 - i,
            )
        )
    return results


# ---------------------------------------------------------------------------
# ExperienceStore.retrieve_context tests
# ---------------------------------------------------------------------------


class TestRetrieveContext:
    """Tests for ExperienceStore.retrieve_context."""

    async def test_retrieve_context_returns_dicts(self):
        """retrieve_context should return list of plain dicts."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        results = _mock_experience_results(2)
        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.return_value = results
            context = await store.retrieve_context(
                query="React landing page",
                category="bid",
                top_k=3,
            )

        assert len(context) == 2
        assert isinstance(context[0], dict)
        assert "content" in context[0]
        assert "category" in context[0]
        assert "similarity_score" in context[0]
        assert "title" in context[0]
        assert "metadata" in context[0]

    async def test_retrieve_context_maps_fields_correctly(self):
        """Fields should be mapped from ExperienceResult to dict."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        results = [
            ExperienceResult(
                id="exp-42",
                category="code",
                title="React component",
                content="function App() { return <div>Hello</div> }",
                similarity=0.92,
                success_rate=0.85,
                usage_count=5,
            )
        ]
        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.return_value = results
            context = await store.retrieve_context(query="React app", top_k=1)

        assert len(context) == 1
        item = context[0]
        assert item["content"] == "function App() { return <div>Hello</div> }"
        assert item["category"] == "code"
        assert item["similarity_score"] == 0.92
        assert item["title"] == "React component"
        assert item["success_rate"] == 0.85
        assert item["metadata"]["id"] == "exp-42"
        assert item["metadata"]["usage_count"] == 5

    async def test_retrieve_context_empty_query_returns_empty(self):
        """Empty or whitespace-only query should return empty list."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        result_empty = await store.retrieve_context(query="")
        result_ws = await store.retrieve_context(query="   ")

        assert result_empty == []
        assert result_ws == []

    async def test_retrieve_context_no_results(self):
        """When retrieve_similar returns empty, retrieve_context returns empty."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.return_value = []
            context = await store.retrieve_context(query="some query", top_k=5)

        assert context == []

    async def test_retrieve_context_exception_returns_empty(self):
        """On any exception, retrieve_context returns empty list (graceful degradation)."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.side_effect = ConnectionError("DB unavailable")
            context = await store.retrieve_context(query="some query")

        assert context == []

    async def test_retrieve_context_passes_category_filter(self):
        """Category parameter should be forwarded to retrieve_similar."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.return_value = []
            await store.retrieve_context(query="test query", category="estimation", top_k=5)

        mock_rs.assert_called_once_with(
            "test query",
            category="estimation",
            top_k=5,
            min_similarity=0.60,
        )

    async def test_retrieve_context_passes_top_k(self):
        """top_k parameter should be forwarded to retrieve_similar."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.return_value = []
            await store.retrieve_context(query="test query", top_k=10)

        mock_rs.assert_called_once_with(
            "test query",
            category=None,
            top_k=10,
            min_similarity=0.60,
        )

    async def test_retrieve_context_none_similarity_defaults_to_zero(self):
        """When similarity is None, similarity_score should be 0.0."""
        mock_embedding = AsyncMock()
        mock_pool = AsyncMock()

        store = ExperienceStore(
            embedding_service=mock_embedding,
            db_pool=mock_pool,
        )

        results = [
            ExperienceResult(
                id="exp-0",
                category="bid",
                title="Test",
                content="Content",
                similarity=None,
                success_rate=None,
                usage_count=0,
            )
        ]
        with patch.object(store, "retrieve_similar", new_callable=AsyncMock) as mock_rs:
            mock_rs.return_value = results
            context = await store.retrieve_context(query="test")

        assert context[0]["similarity_score"] == 0.0
        assert context[0]["success_rate"] is None


# ---------------------------------------------------------------------------
# Bid Agent RAG wiring tests
# ---------------------------------------------------------------------------


class TestBidAgentRag:
    """Tests for RAG wiring in BidAgent."""

    async def test_fetch_rag_context_graceful_on_import_error(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """_fetch_rag_context should return [] when imports fail."""
        agent = BidAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        job = {"title": "Test job", "description": "Build something"}

        with patch(
            "src.agents.bid.get_asyncpg_pool",
            side_effect=ImportError("No module"),
            create=True,
        ):
            result = await agent._fetch_rag_context(job)

        assert result == []

    async def test_fetch_rag_context_graceful_on_pool_none(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """_fetch_rag_context should return [] when pool is None."""
        agent = BidAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        job = {"title": "Test job", "description": "Build something"}

        with patch(
            "src.agents.bid.get_asyncpg_pool",
            new_callable=AsyncMock,
            return_value=None,
            create=True,
        ):
            result = await agent._fetch_rag_context(job)

        assert result == []

    async def test_rag_context_injected_into_bid_prompt(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When RAG context is provided, it should appear in the LLM prompt."""
        agent = BidAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        rag_context = [
            {
                "content": "Winning proposal for React project",
                "category": "bid",
                "similarity_score": 0.88,
                "title": "React landing page bid",
                "success_rate": 0.9,
                "metadata": {"id": "exp-1", "usage_count": 5},
            }
        ]

        proposal_response = {
            "proposal_text": "I can build this for you.",
            "bid_amount": 300.0,
            "delivery_days": 7,
            "confidence_score": 0.8,
            "requires_hitl": True,
            "milestones": [],
        }

        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(proposal_response)),
                CallMetrics(
                    agent_name="bid",
                    model_id="gemini-3-flash",
                    provider="google",
                ),
            )
        )

        job = {
            "id": "00000000-0000-0000-0000-000000000001",
            "title": "Build React page",
            "description": "Landing page with Tailwind",
            "skills_required": ["React", "Tailwind"],
        }

        await agent._generate_proposal(job, [], rag_context)

        # Check that the LLM was called with RAG context in the prompt.
        # llm_client.call is called as call(agent_name, messages, ...)
        call_args = mock_llm_client.call.call_args
        messages = call_args[0][1]  # second positional arg is messages list
        user_msg = messages[1].content

        assert "Historical proposal experiences (RAG)" in user_msg
        assert "Winning proposal for React project" in user_msg
        assert "similarity: 0.88" in user_msg

    async def test_bid_prompt_without_rag_context(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When no RAG context, prompt should not contain RAG section."""
        agent = BidAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        proposal_response = {
            "proposal_text": "Test proposal",
            "bid_amount": 200.0,
            "delivery_days": 5,
            "confidence_score": 0.7,
            "requires_hitl": True,
            "milestones": [],
        }

        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(proposal_response)),
                CallMetrics(
                    agent_name="bid",
                    model_id="gemini-3-flash",
                    provider="google",
                ),
            )
        )

        job = {
            "id": "00000000-0000-0000-0000-000000000002",
            "title": "Test job",
            "description": "Test description",
        }

        await agent._generate_proposal(job, [], None)

        call_args = mock_llm_client.call.call_args
        messages = call_args[0][1]  # second positional arg is messages list
        user_msg = messages[1].content

        assert "Historical proposal experiences (RAG)" not in user_msg


# ---------------------------------------------------------------------------
# Dev Agent RAG wiring tests
# ---------------------------------------------------------------------------


class TestDevAgentRag:
    """Tests for RAG wiring in DevAgent."""

    async def test_fetch_rag_context_graceful_on_failure(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """_fetch_rag_context should return [] when store is unavailable."""
        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state()
        task_context = {"description": "Build a React component"}

        with patch(
            "src.agents.dev.get_asyncpg_pool",
            new_callable=AsyncMock,
            return_value=None,
            create=True,
        ):
            result = await agent._fetch_rag_context(state, task_context)

        assert result == []

    async def test_rag_context_injected_into_dev_prompt(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When RAG context is provided, it should appear in the user prompt."""
        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state()
        task_context = {"description": "Build a React component", "assigned_agent": "dev"}

        rag_context = [
            {
                "content": "const Button = ({ onClick }) => <button onClick={onClick}>Click</button>",
                "category": "code",
                "similarity_score": 0.90,
                "title": "React Button Component",
                "success_rate": 0.95,
                "metadata": {"id": "exp-code-1", "usage_count": 3},
            }
        ]

        prompt = agent._build_user_prompt(state, task_context, rag_context)

        assert "Similar Code Experiences (RAG)" in prompt
        assert "React Button Component" not in prompt  # title is not in user prompt, just content
        assert "const Button" in prompt
        assert "similarity: 0.90" in prompt

    async def test_dev_prompt_without_rag_context(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Without RAG context, prompt should not contain RAG section."""
        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state()
        task_context = {"description": "Build something"}

        prompt = agent._build_user_prompt(state, task_context, None)

        assert "Similar Code Experiences (RAG)" not in prompt

    async def test_rag_context_in_revision_prompt(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """RAG context should also be injected into revision prompts."""
        agent = DevAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
        )

        state = _build_state()
        task_context = {"description": "Fix the component"}
        critic_feedback = {
            "verdict": "revision_needed",
            "score": 6,
            "revision_type": "minor",
            "issues": [{"description": "Missing error handling", "severity": "medium"}],
        }
        rag_context = [
            {
                "content": "try { await fetch(url) } catch (e) { handleError(e) }",
                "category": "code",
                "similarity_score": 0.82,
                "title": "Error handling pattern",
                "success_rate": 0.88,
                "metadata": {"id": "exp-code-2", "usage_count": 7},
            }
        ]

        prompt = agent._build_revision_prompt(state, task_context, critic_feedback, rag_context)

        assert "Similar Code Experiences (RAG)" in prompt
        assert "try { await fetch(url) }" in prompt
        assert "similarity: 0.82" in prompt


# ---------------------------------------------------------------------------
# Scout Agent RAG wiring tests
# ---------------------------------------------------------------------------


class TestScoutAgentRag:
    """Tests for RAG wiring in ScoutAgent."""

    async def test_fetch_rag_context_graceful_on_failure(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """_fetch_rag_context should return [] when store is unavailable."""
        agent = ScoutAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
            adapters={},
        )

        jobs = [{"title": "Web dev job", "description": "Build a website"}]

        with patch(
            "src.agents.scout.get_asyncpg_pool",
            new_callable=AsyncMock,
            return_value=None,
            create=True,
        ):
            result = await agent._fetch_rag_context(jobs)

        assert result == []

    async def test_rag_context_injected_into_score_prompt(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """When RAG context is provided, it should appear in the scoring prompt."""
        agent = ScoutAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
            adapters={},
        )

        scored_response = [
            {
                "title": "Build website",
                "match_score": 0.85,
                "recommendation": "bid",
                "reasoning": "Good match",
            }
        ]

        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(scored_response)),
                CallMetrics(
                    agent_name="scout",
                    model_id="gemini-3-flash",
                    provider="google",
                ),
            )
        )

        jobs = [{"title": "Build website", "description": "Full stack web app"}]
        rag_context = [
            {
                "content": "Previous web dev project scored 0.9 and was successful",
                "category": "estimation",
                "similarity_score": 0.87,
                "title": "Web dev estimation",
                "success_rate": 0.92,
                "metadata": {"id": "exp-est-1", "usage_count": 4},
            }
        ]

        await agent._score_jobs(jobs, rag_context=rag_context)

        # llm_client.call is called as call(agent_name, messages, ...)
        call_args = mock_llm_client.call.call_args
        messages = call_args[0][1]  # second positional arg is messages list
        user_msg = messages[1].content

        assert "Historical Context (from past projects)" in user_msg
        assert "Previous web dev project" in user_msg
        assert "success rate: 92%" in user_msg

    async def test_scout_score_without_rag_context(
        self,
        mock_llm_client: AsyncMock,
        mock_heartbeat: Any,
        mock_loop_detector: Any,
    ):
        """Without RAG context, scoring prompt should not contain RAG section."""
        agent = ScoutAgent(
            llm_client=mock_llm_client,
            heartbeat=mock_heartbeat,
            loop_detector=mock_loop_detector,
            adapters={},
        )

        scored_response = [{"title": "Test", "match_score": 0.5, "recommendation": "skip"}]

        mock_llm_client.call = AsyncMock(
            return_value=(
                AIMessage(content=json.dumps(scored_response)),
                CallMetrics(
                    agent_name="scout",
                    model_id="gemini-3-flash",
                    provider="google",
                ),
            )
        )

        jobs = [{"title": "Test job"}]

        await agent._score_jobs(jobs, rag_context=None)

        # llm_client.call is called as call(agent_name, messages, ...)
        call_args = mock_llm_client.call.call_args
        messages = call_args[0][1]  # second positional arg is messages list
        user_msg = messages[1].content

        assert "Historical Context (from past projects)" not in user_msg


# ---------------------------------------------------------------------------
# Semantic cache cleanup tests
# ---------------------------------------------------------------------------


class TestSemanticCacheCleanup:
    """Tests for SemanticCache.cleanup method."""

    async def test_cleanup_ttl_purge(self):
        """cleanup should delete expired rows from PostgreSQL."""
        mock_valkey = AsyncMock()
        mock_pool = AsyncMock()

        # Mock scan_iter to return no keys
        mock_valkey.scan_iter = MagicMock(return_value=AsyncIterator([]))

        # Mock PG connection
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 5")
        mock_conn.fetchval = AsyncMock(return_value=100)  # under max_entries
        mock_pool.acquire = MagicMock(return_value=AsyncContextManager(mock_conn))

        with patch(
            "src.core.semantic_cache.os.environ.get",
            side_effect=lambda k, d="": "test-key" if k == "OPENROUTER_API_KEY" else d,
        ):
            with patch("src.core.semantic_cache.OpenAIEmbeddings"):
                from src.core.semantic_cache import SemanticCache

                cache = SemanticCache(
                    valkey=mock_valkey,
                    db_pool=mock_pool,
                )

        removed = await cache.cleanup(max_entries=10_000)

        assert removed == 5

    async def test_cleanup_lru_eviction(self):
        """cleanup should evict LRU entries when count exceeds max_entries."""
        mock_valkey = AsyncMock()
        mock_pool = AsyncMock()

        mock_valkey.scan_iter = MagicMock(return_value=AsyncIterator([]))

        mock_conn = AsyncMock()
        # TTL purge deletes 0
        # LRU check: 15000 entries, max is 10000, so 5000 overflow
        call_count = 0

        async def mock_execute(sql, *args):
            nonlocal call_count
            call_count += 1
            if "expires_at <= NOW()" in sql:
                return "DELETE 0"
            return "DELETE 5000"

        mock_conn.execute = mock_execute
        mock_conn.fetchval = AsyncMock(return_value=15000)
        mock_pool.acquire = MagicMock(return_value=AsyncContextManager(mock_conn))

        with patch(
            "src.core.semantic_cache.os.environ.get",
            side_effect=lambda k, d="": "test-key" if k == "OPENROUTER_API_KEY" else d,
        ):
            with patch("src.core.semantic_cache.OpenAIEmbeddings"):
                from src.core.semantic_cache import SemanticCache

                cache = SemanticCache(
                    valkey=mock_valkey,
                    db_pool=mock_pool,
                )

        removed = await cache.cleanup(max_entries=10_000)

        assert removed == 5000

    async def test_cleanup_no_eviction_under_limit(self):
        """No LRU eviction when entry count is under max_entries."""
        mock_valkey = AsyncMock()
        mock_pool = AsyncMock()

        mock_valkey.scan_iter = MagicMock(return_value=AsyncIterator([]))

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 0")
        mock_conn.fetchval = AsyncMock(return_value=500)  # well under limit
        mock_pool.acquire = MagicMock(return_value=AsyncContextManager(mock_conn))

        with patch(
            "src.core.semantic_cache.os.environ.get",
            side_effect=lambda k, d="": "test-key" if k == "OPENROUTER_API_KEY" else d,
        ):
            with patch("src.core.semantic_cache.OpenAIEmbeddings"):
                from src.core.semantic_cache import SemanticCache

                cache = SemanticCache(
                    valkey=mock_valkey,
                    db_pool=mock_pool,
                )

        removed = await cache.cleanup(max_entries=10_000)

        assert removed == 0

    async def test_cleanup_handles_pg_error_gracefully(self):
        """cleanup should not raise on PostgreSQL errors."""
        mock_valkey = AsyncMock()
        mock_pool = AsyncMock()

        mock_valkey.scan_iter = MagicMock(return_value=AsyncIterator([]))

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=ConnectionError("DB down"))
        mock_pool.acquire = MagicMock(return_value=AsyncContextManager(mock_conn))

        with patch(
            "src.core.semantic_cache.os.environ.get",
            side_effect=lambda k, d="": "test-key" if k == "OPENROUTER_API_KEY" else d,
        ):
            with patch("src.core.semantic_cache.OpenAIEmbeddings"):
                from src.core.semantic_cache import SemanticCache

                cache = SemanticCache(
                    valkey=mock_valkey,
                    db_pool=mock_pool,
                )

        # Should not raise
        removed = await cache.cleanup()

        assert removed == 0

    async def test_cleanup_no_pool(self):
        """cleanup should work even with db_pool=None (Valkey only)."""
        mock_valkey = AsyncMock()
        mock_valkey.scan_iter = MagicMock(return_value=AsyncIterator([]))

        with patch(
            "src.core.semantic_cache.os.environ.get",
            side_effect=lambda k, d="": "test-key" if k == "OPENROUTER_API_KEY" else d,
        ):
            with patch("src.core.semantic_cache.OpenAIEmbeddings"):
                from src.core.semantic_cache import SemanticCache

                cache = SemanticCache(
                    valkey=mock_valkey,
                    db_pool=MagicMock(),  # type: ignore[arg-type]
                )
                cache.db_pool = None  # type: ignore[assignment]

        removed = await cache.cleanup()
        assert removed == 0


# ---------------------------------------------------------------------------
# schedule_cleanup tests
# ---------------------------------------------------------------------------


class TestScheduleCleanup:
    """Tests for schedule_cleanup function."""

    def test_schedule_cleanup_registers_job(self):
        """schedule_cleanup should call scheduler.add_job with correct params."""
        from src.core.semantic_cache import schedule_cleanup

        mock_scheduler = MagicMock()
        mock_cache = MagicMock()

        schedule_cleanup(mock_scheduler, mock_cache)

        mock_scheduler.add_job.assert_called_once()
        call_kwargs = mock_scheduler.add_job.call_args
        assert call_kwargs[1]["id"] == "semantic_cache_cleanup"
        assert call_kwargs[1]["hours"] == 6
        assert call_kwargs[1]["replace_existing"] is True


# ---------------------------------------------------------------------------
# Async helper utilities for tests
# ---------------------------------------------------------------------------


class AsyncIterator:
    """Async iterator helper for mocking async for loops."""

    def __init__(self, items: list[Any]):
        self._items = items
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class AsyncContextManager:
    """Async context manager helper for mocking pool.acquire()."""

    def __init__(self, value: Any):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args: Any):
        pass
