"""Unit tests for Experience Store integration in PackagerAgent.

After successful packaging, the Packager should call save_experience()
to store the completed project in the knowledge base for future RAG retrieval.

Spec: docs/Full_work/pipeline-a-spec.md Phase 6 (RAG section)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from src.agents.packager import PackagerAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults for packager tests."""
    project = {
        "project_id": "proj-exp-001",
        "job_id": "job-exp-001",
        "platform": "freelancer",
        "client": {"name": "Experience Test Client"},
        "requirements": "Build a REST API with authentication",
        "budget": 800.0,
        "deadline": datetime(2026, 4, 1, tzinfo=UTC),
    }
    state = create_initial_state(
        project=project,
        first_agent="packager",
        thread_id="thread-exp-test",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_delivery_json(project_id: str = "proj-exp-001") -> str:
    """Create a valid delivery package JSON for mock LLM responses."""
    delivery = {
        "delivery_id": f"del-{uuid.uuid4().hex[:8]}",
        "project_id": project_id,
        "files_count": 12,
        "includes": ["source_code", "documentation", "tests"],
        "delivery_message": "Your REST API is ready for review.",
        "readme_content": "# REST API\n\n## Setup\npip install -r requirements.txt",
        "missing_artifacts": [],
        "quality_notes": "All code passed Critic review. Tests included.",
        "requires_hitl": True,
    }
    return json.dumps(delivery, default=str)


def _make_agent(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
) -> PackagerAgent:
    """Create a PackagerAgent with mocked dependencies."""
    return PackagerAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )


# ---------------------------------------------------------------------------
# Tests: save_experience is called on successful packaging
# ---------------------------------------------------------------------------


async def test_save_experience_called_on_successful_packaging(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """After successful packaging, _save_project_experience should be called."""
    delivery_json = _make_delivery_json()
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=delivery_json),
            CallMetrics(agent_name="packager", model_id="deepseek-v3.2", provider="deepseek"),
        )
    )

    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    state = _build_state(
        artifacts={
            "dev": ["code-artifact-1", "code-artifact-2"],
            "content": ["content-artifact-1"],
            "critic": ['{"verdict": "APPROVE", "score": 0.91}'],
        }
    )

    save_mock = AsyncMock()

    with (
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-exp-001"),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        patch.object(agent, "_save_project_experience", save_mock),
    ):
        result = await agent._execute(state)

    # Verify save was called exactly once.
    save_mock.assert_awaited_once()

    # Verify the state is still correct (experience save must not break flow).
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"


async def test_save_experience_receives_project_and_delivery_info(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_save_project_experience receives the project dict, delivery_info, and artifacts."""
    delivery_json = _make_delivery_json()
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=delivery_json),
            CallMetrics(agent_name="packager", model_id="deepseek-v3.2", provider="deepseek"),
        )
    )

    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    artifacts = {
        "dev": ["dev-art-1"],
        "content": ["content-art-1"],
        "design": ["design-art-1"],
    }
    state = _build_state(artifacts=artifacts)

    save_mock = AsyncMock()

    with (
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-exp-002"),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        patch.object(agent, "_save_project_experience", save_mock),
    ):
        await agent._execute(state)

    # Check the call arguments.
    call_args = save_mock.call_args
    project_arg = call_args[1]["project"]
    delivery_arg = call_args[1]["delivery_info"]
    artifacts_arg = call_args[1]["collected_artifacts"]

    assert project_arg["project_id"] == "proj-exp-001"
    assert isinstance(delivery_arg, dict)
    assert delivery_arg["requires_hitl"] is True
    assert "dev" in artifacts_arg
    assert "content" in artifacts_arg
    assert "design" in artifacts_arg


async def test_save_experience_not_called_when_no_artifacts(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When there are no execution artifacts, _save_project_experience should NOT be called."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    state = _build_state(
        artifacts={
            "scout": ["job-1"],
            "bid": ["bid-1"],
            "planner": ["plan-1"],
        }
    )

    save_mock = AsyncMock()

    with (
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        patch.object(agent, "_save_project_experience", save_mock),
    ):
        await agent._execute(state)

    # No execution artifacts -> no experience to save.
    save_mock.assert_not_awaited()


async def test_save_experience_not_called_on_delivery_hold(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When delivery is held (execution cloaking), experience should NOT be saved yet."""
    delivery_json = _make_delivery_json()
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=delivery_json),
            CallMetrics(agent_name="packager", model_id="deepseek-v3.2", provider="deepseek"),
        )
    )

    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    # Set min_delivery_at far in the future to trigger delivery hold.
    future = datetime(2030, 1, 1, tzinfo=UTC)
    state = _build_state(
        artifacts={"dev": ["art-1"]},
        min_delivery_at=future,
    )

    save_mock = AsyncMock()

    with (
        patch.object(agent, "_create_delivery_hold_entry", new_callable=AsyncMock, return_value="hitl-hold-001"),
        patch.object(agent, "_save_project_experience", save_mock),
    ):
        await agent._execute(state)

    # Delivery hold -> no experience save.
    save_mock.assert_not_awaited()


async def test_save_experience_error_does_not_fail_pipeline(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """If _save_project_experience raises an exception, the pipeline should NOT fail."""
    delivery_json = _make_delivery_json()
    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=delivery_json),
            CallMetrics(agent_name="packager", model_id="deepseek-v3.2", provider="deepseek"),
        )
    )

    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    state = _build_state(artifacts={"dev": ["art-1"]})

    # save_experience raises an error.
    save_mock = AsyncMock(side_effect=Exception("DB connection lost"))

    with (
        patch.object(agent, "_create_hitl_entry", new_callable=AsyncMock, return_value="hitl-err-001"),
        patch.object(agent, "_log_packaging_action", new_callable=AsyncMock),
        patch.object(agent, "_save_project_experience", save_mock),
    ):
        result = await agent._execute(state)

    # Pipeline should still complete normally.
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert result["hitl_request_id"] == "hitl-err-001"


# ---------------------------------------------------------------------------
# Tests: _save_project_experience method internals
# ---------------------------------------------------------------------------


async def test_save_project_experience_calls_experience_store(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """_save_project_experience should call ExperienceStore.save_experience with correct data."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    mock_store = AsyncMock()
    mock_store.save_experience = AsyncMock(return_value=uuid.uuid4())

    project = {
        "project_id": "proj-save-001",
        "job_id": "job-save-001",
        "platform": "freelancer",
        "client": {"name": "Save Client"},
        "requirements": "Build a chatbot",
        "budget": 1200.0,
    }
    delivery_info = {
        "delivery_id": "del-save-001",
        "project_id": "proj-save-001",
        "files_count": 8,
        "includes": ["source_code", "tests"],
        "delivery_type": "files",
        "quality_notes": "Excellent quality.",
    }
    collected_artifacts = {
        "dev": ["code-1", "code-2"],
        "content": ["text-1"],
    }

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=mock_store,
    ):
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )

    # Verify save_experience was called.
    mock_store.save_experience.assert_awaited_once()

    call_kwargs = mock_store.save_experience.call_args
    # Category should be "bid" (completed project experience).
    assert call_kwargs[0][0] == "bid"
    # Title should contain the project_id.
    assert "proj-save-001" in call_kwargs[0][1]
    # Content should be a non-empty string with project details.
    content = call_kwargs[0][2]
    assert len(content) > 0
    assert "proj-save-001" in content
    assert "chatbot" in content.lower() or "Build a chatbot" in content


async def test_save_project_experience_includes_metadata(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Saved experience should include metadata with platform, budget, delivery_type."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    mock_store = AsyncMock()
    mock_store.save_experience = AsyncMock(return_value=uuid.uuid4())

    project = {
        "project_id": "proj-meta-001",
        "platform": "kwork",
        "client": {"name": "Meta Client"},
        "requirements": "Design a logo",
        "budget": 300.0,
    }
    delivery_info = {
        "delivery_type": "files",
        "files_count": 5,
        "includes": ["design_files"],
        "quality_notes": "Clean design.",
    }
    collected_artifacts = {"design": ["logo-1"]}

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=mock_store,
    ):
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )

    call_kwargs = mock_store.save_experience.call_args
    metadata = call_kwargs[1].get("metadata") or call_kwargs.kwargs.get("metadata")
    assert metadata is not None
    assert metadata["platform"] == "kwork"
    assert metadata["budget"] == 300.0
    assert metadata["delivery_type"] == "files"


async def test_save_project_experience_handles_store_error_gracefully(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """If ExperienceStore.save_experience raises, _save_project_experience logs and returns."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    mock_store = AsyncMock()
    mock_store.save_experience = AsyncMock(side_effect=Exception("pgvector unavailable"))

    project = {"project_id": "proj-fail-001", "requirements": "Test"}
    delivery_info = {"delivery_type": "files", "files_count": 1}
    collected_artifacts = {"dev": ["art-1"]}

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=mock_store,
    ):
        # Should NOT raise.
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )

    # Store was called (and failed), but method didn't raise.
    mock_store.save_experience.assert_awaited_once()


async def test_save_project_experience_handles_store_unavailable(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """If _get_experience_store returns None, _save_project_experience should skip gracefully."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    project = {"project_id": "proj-nostore-001", "requirements": "Test"}
    delivery_info = {"delivery_type": "files", "files_count": 1}
    collected_artifacts = {"dev": ["art-1"]}

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=None,
    ):
        # Should NOT raise even when store is unavailable.
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )


async def test_save_project_experience_content_includes_all_relevant_fields(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """The content saved should include requirements, budget, delivery info, and artifact summary."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    mock_store = AsyncMock()
    mock_store.save_experience = AsyncMock(return_value=uuid.uuid4())

    project = {
        "project_id": "proj-content-001",
        "job_id": "job-content-001",
        "platform": "freelancer",
        "client": {"name": "Content Client", "rating": 4.9},
        "requirements": "Build an e-commerce API with payment integration",
        "budget": 2500.0,
    }
    delivery_info = {
        "delivery_type": "deploy",
        "files_count": 25,
        "includes": ["source_code", "tests", "deployment_config"],
        "quality_notes": "Production-ready with 95% test coverage.",
        "deploy_url": "https://api.example.com",
    }
    collected_artifacts = {
        "dev": ["api-code-1", "api-code-2", "api-code-3"],
        "content": ["api-docs-1"],
        "critic": ['{"verdict": "APPROVE", "score": 0.95}'],
    }

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=mock_store,
    ):
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )

    call_args = mock_store.save_experience.call_args
    content = call_args[0][2]

    # Content must include key project details for RAG retrieval.
    assert "e-commerce" in content.lower() or "e-commerce API" in content
    assert "2500" in content or "2,500" in content
    assert "deploy" in content.lower()
    assert "25" in content  # files_count
    assert "freelancer" in content.lower()


async def test_save_experience_uses_success_score(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """The success_score should be derived from critic verdict when available."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    mock_store = AsyncMock()
    mock_store.save_experience = AsyncMock(return_value=uuid.uuid4())

    project = {"project_id": "proj-score-001", "requirements": "Test"}
    delivery_info = {"delivery_type": "files", "files_count": 5}
    collected_artifacts = {
        "dev": ["code-1"],
        "critic": ['{"verdict": "APPROVE", "score": 0.92}'],
    }

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=mock_store,
    ):
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )

    call_kwargs = mock_store.save_experience.call_args
    success_score = call_kwargs[1].get("success_score") or call_kwargs.kwargs.get("success_score")
    # Critic score 0.92 should be used as success_score.
    assert success_score is not None
    assert 0.9 <= success_score <= 1.0


async def test_save_experience_default_score_without_critic(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """When no critic artifact exists, success_score should default to 0.7."""
    agent = _make_agent(mock_llm_client, mock_heartbeat, mock_loop_detector)

    mock_store = AsyncMock()
    mock_store.save_experience = AsyncMock(return_value=uuid.uuid4())

    project = {"project_id": "proj-noscore-001", "requirements": "Simple task"}
    delivery_info = {"delivery_type": "files", "files_count": 2}
    collected_artifacts = {"dev": ["code-1"]}  # No critic artifacts.

    with patch(
        "src.agents.packager._get_experience_store",
        new_callable=AsyncMock,
        return_value=mock_store,
    ):
        await agent._save_project_experience(
            project=project,
            delivery_info=delivery_info,
            collected_artifacts=collected_artifacts,
        )

    call_kwargs = mock_store.save_experience.call_args
    success_score = call_kwargs[1].get("success_score") or call_kwargs.kwargs.get("success_score")
    assert success_score == 0.7
