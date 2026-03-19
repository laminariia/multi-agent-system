"""Design Agent -- generates UI/UX design specifications and visual artefacts.

The Design Agent:
1. Reads project requirements and existing artefacts (content copy, etc.) from state.
2. Determines the design type from the project context.
3. Calls the LLM with DESIGN_SYSTEM_PROMPT to produce design specifications.
4. Parses the JSON response into a list of design deliverables.
5. Calls PencilMCPClient to generate .pen files and screenshots (if MCP available).
6. Stores results in ``artifacts["design"]`` and ``artifacts["design_mockup"]``.
7. Logs every decision to the ``agent_logs`` table.

When Pencil.dev MCP is available, the agent generates visual mockups (.pen files)
and exports PNG screenshots for HITL review.  When MCP is unavailable, the agent
falls back to JSON-only design specs (current behavior).

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
        6. Call PencilMCPClient to generate mockup (if available).
        7. Store mockup in artifacts["design_mockup"].
        8. Log the action.
        9. Advance sequence index and return updated state.
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

        # Check for design feedback from HITL revision loop.
        design_feedback = state.get("design_feedback")
        if design_feedback:
            existing_context += f"\n\nDesign revision feedback:\n{design_feedback}"

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

        # 6. Call PencilMCPClient to generate visual mockup.
        await self._generate_pencil_mockup(design_result, design_type, artifacts)

        # 7. Log the action.
        await self._log_design_generated(
            thread_id=state["thread_id"],
            design_type=design_type,
            deliverable_count=len(design_result.get("deliverables", [])),
        )

        # 8. Advance sequence index and let routing decide next agent.
        #    During a revision loop the critic sends us back without changing
        #    sequence position -- only bump the index on a fresh (non-revision)
        #    pass so that routing advances to the next agent correctly.
        is_revision = state.get("revision_severity") is not None
        new_index = state["current_sequence_index"] if is_revision else state.get("current_sequence_index", 0) + 1

        return update_state(
            state,
            current_agent="design",
            current_sequence_index=new_index,
            revision_target=None,
            revision_severity=None,
            design_feedback=None,
            artifacts=artifacts,
            status="active",
        )

    # ------------------------------------------------------------------
    # Pencil.dev MCP integration
    # ------------------------------------------------------------------

    async def _generate_pencil_mockup(
        self,
        design_result: dict[str, Any],
        design_type: str,
        artifacts: dict[str, Any],
    ) -> None:
        """Call PencilMCPClient to generate a visual mockup from the design spec.

        Stores the result in ``artifacts["design_mockup"]``.  On failure
        (MCP unavailable or error), logs a warning and continues without
        a mockup -- the JSON spec is still stored in ``artifacts["design"]``.

        Args:
            design_result: Parsed design specification from the LLM.
            design_type: Inferred design type (e.g. "ui_mockup").
            artifacts: Mutable artifacts dict to update in-place.
        """
        from src.integrations.pencil_mcp import (  # noqa: PLC0415
            build_design_spec,
            get_pencil_client,
        )

        try:
            # Map LLM design type to Pencil.dev project type.
            project_type_map = {
                "ui_mockup": "landing_page",
                "graphic": "graphic",
                "icon_set": "icon_set",
                "design_system": "design_system",
                "wireframe": "wireframe",
            }
            project_type = project_type_map.get(design_type, "landing_page")

            # Extract style from LLM result deliverables.
            deliverables = design_result.get("deliverables", [])
            style: dict[str, Any] = {}
            if deliverables:
                first_spec = deliverables[0].get("specs", {})
                style = {
                    "colors": first_spec.get("colors", []),
                    "typography": ", ".join(first_spec.get("fonts", [])),
                    "theme": "modern_minimal",
                }

            # Build sections from deliverables.
            sections = [
                {"type": d.get("name", "section"), "layout": d.get("specs", {}).get("layout", "")} for d in deliverables
            ]

            pencil_spec = build_design_spec(
                project_type=project_type,
                style=style if style else None,
                sections=sections,
                responsive=True,
                export_format="react",
            )

            client = get_pencil_client()
            result = await client.create_design(pencil_spec)

            # Store mockup result in artifacts.
            mockup_data = result.to_dict()
            artifacts["design_mockup"] = [json.dumps(mockup_data, ensure_ascii=False, default=str)]

            self._log.info(
                "pencil_mockup_generated",
                is_fallback=result.is_fallback,
                pen_file=result.pen_file_path,
            )

        except Exception:
            self._log.warning("pencil_mockup_generation_failed", exc_info=True)
            # Continue without mockup -- design JSON spec is still available.

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
