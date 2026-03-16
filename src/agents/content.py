"""Content Agent -- generates copywriting, documentation, emails, and UI text.

The Content Agent:
1. Reads project requirements and any existing artefacts (dev code, etc.) from state.
2. Determines the content type from the project context.
3. Calls the LLM with CONTENT_SYSTEM_PROMPT to produce structured deliverables.
4. Parses the JSON response into a list of content deliverables.
5. Stores results in ``artifacts["content"]`` and routes to the Design Agent.
6. Logs every decision to the ``agent_logs`` table.

Role constraints: can WRITE text, CANNOT execute code, CANNOT submit proposals.
LLM: Claude Sonnet 4.6 (Tier 3: Content+Review).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base import ConstrainedAgent
from src.core.database import get_db_session
from src.core.heartbeat import HeartbeatMonitor
from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import AgentLog
from src.core.state import AgentState, update_state
from src.prompts.content import CONTENT_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Content Agent is allowed to invoke.
CONTENT_ALLOWED_TOOLS: list[str] = [
    "search_knowledge_base",
    "analyze_competitor_content",
    "check_readability",
    "translate_content",
]


class ContentAgent(ConstrainedAgent):
    """Generates text content (copywriting, docs, emails, UI text).

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Claude Sonnet 4.6 primary, Tier 3).
    heartbeat:
        Shared :class:`HeartbeatMonitor` for liveness pings.
    loop_detector:
        Shared :class:`LoopDetector` for runaway-prevention.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
    ) -> None:
        super().__init__(
            agent_name="content",
            allowed_tools=CONTENT_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Generate content deliverables for the current project.

        Flow:
        1. Extract project requirements and existing artefacts from state.
        2. Determine content type from project context.
        3. Call LLM with CONTENT_SYSTEM_PROMPT.
        4. Parse JSON response with deliverables[].
        5. Store as artifacts["content"].
        6. Set next_agent="design".
        7. Log the action.
        8. Return updated state.
        """
        self._log.info("content_execute_start", thread_id=state["thread_id"])

        # 1. Extract project requirements.
        project = state.get("project") or {}
        requirements = project.get("requirements", "")
        if not requirements:
            self._log.warning("no_requirements_in_project")
            return update_state(state, current_agent="content", next_agent=None, status="active")

        # 2. Build context from existing artefacts.
        artifacts = dict(state.get("artifacts") or {})
        existing_context = self._build_existing_context(artifacts)

        # 3. Determine content type from requirements.
        content_type = self._infer_content_type(requirements)

        # 4. Call LLM.
        content_result = await self._generate_content(
            requirements=requirements,
            content_type=content_type,
            existing_context=existing_context,
            project=project,
        )

        if content_result is None:
            self._log.warning("content_generation_failed")
            return update_state(state, current_agent="content", next_agent=None, status="active")

        # 5. Store content deliverables in artefacts.
        serialized = json.dumps(content_result, ensure_ascii=False, default=str)
        artifacts["content"] = [serialized]

        # 6. Log the action.
        await self._log_content_generated(
            thread_id=state["thread_id"],
            content_type=content_type,
            deliverable_count=len(content_result.get("deliverables", [])),
            word_count=content_result.get("word_count", 0),
        )

        # 7. Advance sequence index and let routing decide next agent.
        return update_state(
            state,
            current_agent="content",
            current_sequence_index=state.get("current_sequence_index", 0) + 1,
            revision_target=None,
            revision_severity=None,
            artifacts=artifacts,
            status="active",
        )

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_existing_context(artifacts: dict[str, list[str]]) -> str:
        """Assemble context from artefacts already produced by other agents."""
        parts: list[str] = []

        if "dev" in artifacts and artifacts["dev"]:
            parts.append(
                "Existing code artefacts are available from the Dev Agent. "
                "Ensure content complements the implemented features."
            )
            for item in artifacts["dev"][:3]:
                parts.append(f"  - Dev artefact: {item[:200]}")

        if "planner" in artifacts and artifacts["planner"]:
            parts.append("A task plan exists from the Planner Agent.")

        return "\n".join(parts) if parts else ""

    @staticmethod
    def _infer_content_type(requirements: str) -> str:
        """Infer the content type from project requirements text."""
        req_lower = requirements.lower()

        if any(kw in req_lower for kw in ("landing page", "hero", "homepage", "above the fold")):
            return "landing_page"
        if any(kw in req_lower for kw in ("email", "newsletter", "outreach", "cold email")):
            return "email"
        if any(kw in req_lower for kw in ("readme", "api doc", "documentation", "guide", "tutorial")):
            return "documentation"
        if any(kw in req_lower for kw in ("button", "error message", "empty state", "ui text", "microcopy")):
            return "ui_text"
        if any(kw in req_lower for kw in ("blog", "article", "post")):
            return "blog_post"
        if any(kw in req_lower for kw in ("product", "description", "listing")):
            return "product_description"
        return "landing_page"  # sensible default for web projects

    # ------------------------------------------------------------------
    # LLM content generation
    # ------------------------------------------------------------------

    async def _generate_content(
        self,
        *,
        requirements: str,
        content_type: str,
        existing_context: str,
        project: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call the LLM to generate content deliverables.

        Returns a parsed content dict or ``None`` on failure.
        """
        context_parts: list[str] = [
            f"Project requirements:\n{requirements}",
            f"\nContent type requested: {content_type}",
        ]

        if project.get("client"):
            context_parts.append(f"\nClient info: {json.dumps(project['client'], default=str)}")

        if project.get("budget"):
            context_parts.append(f"Budget: ${project['budget']}")

        if existing_context:
            context_parts.append(f"\nExisting artefacts context:\n{existing_context}")

        user_content = "\n".join(context_parts)
        user_content += (
            "\n\nGenerate the content deliverables for this project. "
            "Return a single JSON object matching the output format specified in your instructions."
        )

        messages = [
            SystemMessage(content=CONTENT_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.7)
        raw_text = str(response_msg.content)
        return self._parse_content_response(raw_text)

    def _parse_content_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's content JSON response using extract_json."""
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error("content_llm_json_parse_error", raw_preview=raw[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("content_llm_unexpected_type", type=type(parsed).__name__)
            return None

        # Validate required fields.
        if "deliverables" not in parsed or not isinstance(parsed.get("deliverables"), list):
            self._log.warning("content_missing_deliverables")
            return None

        # Ensure content_type is present.
        if "content_type" not in parsed:
            parsed["content_type"] = "landing_page"

        # Ensure numeric fields.
        try:
            parsed["word_count"] = int(parsed.get("word_count", 0))
        except (TypeError, ValueError):
            parsed["word_count"] = 0

        try:
            parsed["reading_time_seconds"] = int(parsed.get("reading_time_seconds", 0))
        except (TypeError, ValueError):
            parsed["reading_time_seconds"] = 0

        return parsed

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_content_generated(
        self,
        *,
        thread_id: str,
        content_type: str,
        deliverable_count: int,
        word_count: int,
    ) -> None:
        """Write an audit log entry for the generated content."""
        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="content",
                event_type="content_generated",
                message=(
                    f"Content generated: type={content_type}, {deliverable_count} deliverables, {word_count} words"
                ),
                details={
                    "thread_id": thread_id,
                    "content_type": content_type,
                    "deliverable_count": deliverable_count,
                    "word_count": word_count,
                },
                llm_model="claude-sonnet-4-6",
            )
            session.add(log_entry)

        self._log.info(
            "content_generated",
            content_type=content_type,
            deliverable_count=deliverable_count,
            word_count=word_count,
        )


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def content_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the Content Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = ContentAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
