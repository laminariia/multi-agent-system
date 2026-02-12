"""Packager Agent -- assembles approved artifacts and prepares client delivery.

CRITICAL: Every delivery MUST require HITL approval before being sent to the client.
The ``requires_hitl`` field is ALWAYS set to ``True`` before this agent returns
control.  No delivery is ever auto-submitted.

The Packager Agent:
1. Collects all artifacts from state (dev, content, design agents).
2. Calls the LLM (Claude Haiku 4.5) with PACKAGER_SYSTEM_PROMPT to generate
   a delivery package description and README.
3. Creates an HITL queue entry for final review.
4. Pauses the workflow until human approval.

Role constraints: can ASSEMBLE artifacts, CANNOT modify code, CANNOT submit
without HITL approval.
LLM: Claude Haiku 4.5 (no fallback).
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
from src.core.models import AgentLog, HITLQueue
from src.core.state import AgentState, update_state
from src.prompts.packager import PACKAGER_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Packager Agent is allowed to invoke.
PACKAGER_ALLOWED_TOOLS: list[str] = [
    "collect_project_artifacts",
    "generate_readme",
    "create_delivery_archive",
    "generate_delivery_message",
]


class PackagerAgent(ConstrainedAgent):
    """Assembles approved artifacts and creates delivery packages for HITL review.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Claude Haiku 4.5).
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
            agent_name="packager",
            allowed_tools=PACKAGER_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Assemble artifacts into a delivery package and pause for HITL approval.

        Flow:
        1. Collect all artifacts from state (dev, content, design agents).
        2. Call the LLM to generate the delivery package description.
        3. Parse and validate the response.
        4. Store delivery info in artifacts.
        5. Create HITL queue entry for final review.
        6. Set requires_hitl=True, status='paused', next_agent=None.
        7. Log the packaging action and return updated state.
        """
        self._log.info("packager_execute_start", thread_id=state["thread_id"])

        project = state.get("project") or {}
        project_id = project.get("project_id", "unknown")

        # 1. Collect artifacts from all execution agents.
        all_artifacts = state.get("artifacts") or {}
        collected = self._collect_execution_artifacts(all_artifacts)

        if not collected:
            self._log.warning("no_artifacts_to_package", project_id=project_id)
            await self._log_packaging_action(
                project_id=project_id,
                delivery_info={"status": "no_artifacts"},
                thread_id=state["thread_id"],
                note="No artifacts found to package",
            )
            return update_state(
                state,
                current_agent="packager",
                next_agent=None,
                status="active",
            )

        # 2. Call LLM to generate the delivery package description.
        delivery_info = await self._generate_delivery_package(project, collected)
        if delivery_info is None:
            self._log.error("delivery_generation_failed", project_id=project_id)
            # Even on LLM failure, create a basic delivery info.
            delivery_info = self._build_fallback_delivery(project_id, collected)

        # CRITICAL: Enforce requires_hitl invariant.
        delivery_info["requires_hitl"] = True

        # 3. Store delivery info as artifact.
        artifacts = dict(all_artifacts)
        delivery_serialized = json.dumps(delivery_info, default=str)
        existing = list(artifacts.get("packager", []))
        existing.append(delivery_serialized)
        artifacts["packager"] = existing

        # 4. Create HITL queue entry for final review.
        hitl_id = await self._create_hitl_entry(project, delivery_info, state["thread_id"])

        # 5. Log the packaging action.
        await self._log_packaging_action(
            project_id=project_id,
            delivery_info=delivery_info,
            thread_id=state["thread_id"],
        )

        self._log.info(
            "delivery_package_ready",
            project_id=project_id,
            files_count=delivery_info.get("files_count", 0),
            hitl_id=hitl_id,
        )

        # 6. CRITICAL: requires_hitl MUST always be True before delivery.
        return update_state(
            state,
            current_agent="packager",
            next_agent=None,  # Graph will interrupt; resumed after HITL.
            artifacts=artifacts,
            requires_hitl=True,
            hitl_request_id=hitl_id,
            status="paused",
        )

    # ------------------------------------------------------------------
    # Artifact collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_execution_artifacts(
        all_artifacts: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """Extract artifacts produced by execution agents (dev, content, design).

        Returns a dict keyed by agent name with their artifact lists.
        Only includes agents that actually produced artifacts.
        """
        execution_agents = ("dev", "content", "design", "critic")
        collected: dict[str, list[str]] = {}

        for agent_name in execution_agents:
            agent_artifacts = all_artifacts.get(agent_name, [])
            if agent_artifacts:
                collected[agent_name] = agent_artifacts

        return collected

    # ------------------------------------------------------------------
    # LLM delivery package generation
    # ------------------------------------------------------------------

    async def _generate_delivery_package(
        self,
        project: dict[str, Any],
        collected_artifacts: dict[str, list[str]],
    ) -> dict[str, Any] | None:
        """Call the LLM to generate a delivery package description.

        Returns a parsed delivery dict or ``None`` on failure.
        """
        project_text = json.dumps(project, indent=2, default=str, ensure_ascii=False)
        artifacts_summary = json.dumps(
            {k: len(v) for k, v in collected_artifacts.items()},
            indent=2,
        )

        messages = [
            SystemMessage(content=PACKAGER_SYSTEM_PROMPT),
            HumanMessage(content=(
                "Prepare a delivery package for the following project.\n\n"
                f"Project:\n{project_text}\n\n"
                f"Artifacts available (agent -> count):\n{artifacts_summary}\n\n"
                "Return a single JSON object matching the output format specified "
                "in your instructions."
            )),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.3)
        raw_text = str(response_msg.content)
        return self._parse_delivery_response(raw_text)

    def _parse_delivery_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's delivery package JSON response using extract_json."""
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error("packager_json_parse_error", raw_preview=raw[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("packager_unexpected_type", type=type(parsed).__name__)
            return None

        # Ensure required fields have defaults.
        parsed.setdefault("delivery_id", str(uuid.uuid4()))
        parsed.setdefault("project_id", "unknown")

        try:
            parsed["files_count"] = int(parsed.get("files_count", 0))
        except (TypeError, ValueError):
            parsed["files_count"] = 0

        if not isinstance(parsed.get("includes"), list):
            parsed["includes"] = []

        parsed.setdefault("delivery_message", "Your project delivery is ready for review.")
        parsed.setdefault("readme_content", "")
        parsed.setdefault("missing_artifacts", [])
        parsed.setdefault("quality_notes", "")

        # CRITICAL INVARIANT: requires_hitl must always be true.
        parsed["requires_hitl"] = True

        return parsed

    # ------------------------------------------------------------------
    # Fallback delivery info
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fallback_delivery(
        project_id: str,
        collected: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Build a basic delivery info when LLM generation fails."""
        total_artifacts = sum(len(v) for v in collected.values())
        includes = [f"{agent}_artifacts" for agent in collected]

        return {
            "delivery_id": str(uuid.uuid4()),
            "project_id": project_id,
            "files_count": total_artifacts,
            "includes": includes,
            "delivery_message": (
                "Your project delivery is ready for review. "
                "Please check all included artifacts."
            ),
            "readme_content": "",
            "missing_artifacts": [],
            "quality_notes": "Delivery assembled from available artifacts.",
            "requires_hitl": True,
        }

    # ------------------------------------------------------------------
    # HITL queue entry
    # ------------------------------------------------------------------

    async def _create_hitl_entry(
        self,
        project: dict[str, Any],
        delivery_info: dict[str, Any],
        thread_id: str = "",
    ) -> str:
        """Create an HITL queue entry for final delivery review.

        Returns the stringified UUID of the HITL request.
        """
        hitl_id = uuid.uuid4()
        project_id = project.get("project_id", "unknown")

        async with get_db_session() as session:
            hitl = HITLQueue(
                id=hitl_id,
                type="final_review",
                priority="high",
                title=f"Final review: project {project_id}",
                description=(
                    f"Delivery ready for project '{project_id}'. "
                    f"Files: {delivery_info.get('files_count', 0)} | "
                    f"Includes: {', '.join(delivery_info.get('includes', []))} | "
                    f"Missing: {len(delivery_info.get('missing_artifacts', []))}"
                ),
                payload={
                    "project": project,
                    "delivery_info": delivery_info,
                    "source_agent": "packager",
                    "thread_id": thread_id,
                },
                available_actions=["approve_delivery", "request_changes", "reject"],
                status="pending",
            )
            session.add(hitl)

        self._log.info(
            "hitl_final_review_created",
            hitl_id=str(hitl_id),
            project_id=project_id,
        )
        return str(hitl_id)

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_packaging_action(
        self,
        *,
        project_id: str,
        delivery_info: dict[str, Any],
        thread_id: str,
        note: str = "",
    ) -> None:
        """Write an audit log entry for the packaging action."""
        message = (
            f"Delivery package prepared for project '{project_id}': "
            f"{delivery_info.get('files_count', 0)} files"
        )
        if note:
            message += f" [{note}]"

        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="packager",
                event_type="delivery_packaged",
                message=message,
                details={
                    "project_id": project_id,
                    "files_count": delivery_info.get("files_count", 0),
                    "includes": delivery_info.get("includes", []),
                    "missing_artifacts": delivery_info.get("missing_artifacts", []),
                    "requires_hitl": True,
                    "thread_id": thread_id,
                },
                llm_model="claude-haiku-4-5",
            )
            session.add(log_entry)


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================

async def packager_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the Packager Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = PackagerAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
