"""Design Agent -- generates UI/UX design specifications and visual artefacts.

The Design Agent:
1. Reads project requirements and existing artefacts (content copy, etc.) from state.
2. Determines the design type from the project context.
3. Calls the LLM with DESIGN_SYSTEM_PROMPT to produce design specifications.
4. Parses the JSON response into a list of design deliverables.
5. Stores results in ``artifacts["design"]`` and routes to the Critic Agent.
6. Logs every decision to the ``agent_logs`` table.

In MVP phase, the Design Agent produces detailed JSON specs (colours, fonts,
layout descriptions, responsive notes) rather than actual image files.

Role constraints: can CREATE design artefacts, CANNOT execute code, CANNOT modify code files.
LLM: NanoBanana Pro / Gemini 3 Pro Image (Tier 4: Design).
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
from src.prompts.design import DESIGN_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Design Agent is allowed to invoke.
DESIGN_ALLOWED_TOOLS: list[str] = [
    "generate_image",
    "generate_design_specs",
    "create_color_palette",
    "check_accessibility",
]


class DesignAgent(ConstrainedAgent):
    """Generates design specifications and visual artefact descriptions.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (NanoBanana Pro primary, Tier 4).
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
            agent_name="design",
            allowed_tools=DESIGN_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Generate design specifications for the current project.

        Flow:
        1. Extract project requirements and existing artefacts from state.
        2. Determine design type from project context.
        3. Call LLM with DESIGN_SYSTEM_PROMPT.
        4. Parse JSON response with deliverables[].
        5. Store as artifacts["design"].
        6. Set next_agent="critic".
        7. Log the action.
        8. Return updated state.
        """
        self._log.info("design_execute_start", thread_id=state["thread_id"])

        # 1. Extract project requirements.
        project = state.get("project") or {}
        requirements = project.get("requirements", "")
        if not requirements:
            self._log.warning("no_requirements_in_project")
            return update_state(state, current_agent="design", next_agent=None, status="active")

        # 2. Build context from existing artefacts.
        artifacts = dict(state.get("artifacts") or {})
        existing_context = self._build_existing_context(artifacts)

        # 3. Determine design type from requirements.
        design_type = self._infer_design_type(requirements)

        # 4. Call LLM.
        design_result = await self._generate_design(
            requirements=requirements,
            design_type=design_type,
            existing_context=existing_context,
            project=project,
        )

        if design_result is None:
            self._log.warning("design_generation_failed")
            return update_state(state, current_agent="design", next_agent=None, status="active")

        # 5. Store design deliverables in artefacts.
        serialized = json.dumps(design_result, ensure_ascii=False, default=str)
        artifacts["design"] = [serialized]

        # 6. Log the action.
        await self._log_design_generated(
            thread_id=state["thread_id"],
            design_type=design_type,
            deliverable_count=len(design_result.get("deliverables", [])),
        )

        # 7. Advance sequence index and let routing decide next agent.
        return update_state(
            state,
            current_agent="design",
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

        if "content" in artifacts and artifacts["content"]:
            parts.append(
                "Content artefacts are available from the Content Agent. "
                "Integrate the provided copy into the design specifications."
            )
            for item in artifacts["content"][:3]:
                # Include a preview of the content artefact (truncated for context window).
                parts.append(f"  - Content artefact: {item[:500]}")

        if "dev" in artifacts and artifacts["dev"]:
            parts.append(
                "Code artefacts exist from the Dev Agent. Ensure the design aligns with implemented components."
            )

        if "planner" in artifacts and artifacts["planner"]:
            parts.append("A task plan exists from the Planner Agent.")

        return "\n".join(parts) if parts else ""

    @staticmethod
    def _infer_design_type(requirements: str) -> str:
        """Infer the design type from project requirements text."""
        req_lower = requirements.lower()

        if any(kw in req_lower for kw in ("landing page", "homepage", "web page", "website")):
            return "ui_mockup"
        if any(kw in req_lower for kw in ("dashboard", "admin panel", "analytics")):
            return "ui_mockup"
        if any(kw in req_lower for kw in ("logo", "brand", "branding")):
            return "graphic"
        if any(kw in req_lower for kw in ("icon", "icons", "icon set")):
            return "icon_set"
        if any(kw in req_lower for kw in ("design system", "style guide", "component library")):
            return "design_system"
        if any(kw in req_lower for kw in ("wireframe", "prototype", "user flow")):
            return "wireframe"
        return "ui_mockup"  # sensible default for web projects

    # ------------------------------------------------------------------
    # LLM design generation
    # ------------------------------------------------------------------

    async def _generate_design(
        self,
        *,
        requirements: str,
        design_type: str,
        existing_context: str,
        project: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call the LLM to generate design specifications.

        Returns a parsed design dict or ``None`` on failure.
        """
        context_parts: list[str] = [
            f"Project requirements:\n{requirements}",
            f"\nDesign type requested: {design_type}",
        ]

        if project.get("client"):
            context_parts.append(f"\nClient info: {json.dumps(project['client'], default=str)}")

        if project.get("budget"):
            context_parts.append(f"Budget: ${project['budget']}")

        if existing_context:
            context_parts.append(f"\nExisting artefacts context:\n{existing_context}")

        user_content = "\n".join(context_parts)
        user_content += (
            "\n\nGenerate the design specifications for this project. "
            "Return a single JSON object matching the output format specified in your instructions."
        )

        messages = [
            SystemMessage(content=DESIGN_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.7)
        raw_text = str(response_msg.content)
        return self._parse_design_response(raw_text)

    def _parse_design_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's design JSON response using extract_json."""
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error("design_llm_json_parse_error", raw_preview=raw[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("design_llm_unexpected_type", type=type(parsed).__name__)
            return None

        # Validate required fields.
        if "deliverables" not in parsed or not isinstance(parsed.get("deliverables"), list):
            self._log.warning("design_missing_deliverables")
            return None

        # Ensure design_type is present.
        if "design_type" not in parsed:
            parsed["design_type"] = "ui_mockup"

        return parsed

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_design_generated(
        self,
        *,
        thread_id: str,
        design_type: str,
        deliverable_count: int,
    ) -> None:
        """Write an audit log entry for the generated design."""
        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="design",
                event_type="design_generated",
                message=(f"Design generated: type={design_type}, {deliverable_count} deliverables"),
                details={
                    "thread_id": thread_id,
                    "design_type": design_type,
                    "deliverable_count": deliverable_count,
                },
                llm_model="nanobana-pro",
            )
            session.add(log_entry)

        self._log.info(
            "design_generated",
            design_type=design_type,
            deliverable_count=deliverable_count,
        )


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def design_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node function that creates and invokes the Design Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = DesignAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
