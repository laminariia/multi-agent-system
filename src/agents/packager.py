"""Packager Agent -- assembles approved artifacts and prepares client delivery.

CRITICAL: Every delivery MUST require HITL approval before being sent to the client.
The ``requires_hitl`` field is ALWAYS set to ``True`` before this agent returns
control.  No delivery is ever auto-submitted.

The Packager Agent:
1. Collects all artifacts from state (dev, content, design agents).
2. Reads ``delivery_type`` from state to determine packaging strategy.
3. Calls the LLM (DeepSeek V3.2, Tier 6) with PACKAGER_SYSTEM_PROMPT to generate
   a delivery package description and README.
4. Validates completeness based on delivery_type requirements.
5. Creates an HITL queue entry for final review.
6. Pauses the workflow until human approval.

Role constraints: can ASSEMBLE artifacts, CANNOT modify code, CANNOT submit
without HITL approval.
LLM: DeepSeek V3.2 (Tier 6: Simple).
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
from src.core.state import AgentState, update_state, validate_delivery_type
from src.prompts.packager import PACKAGER_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Packager Agent is allowed to invoke.
PACKAGER_ALLOWED_TOOLS: list[str] = [
    "collect_project_artifacts",
    "generate_readme",
    "create_delivery_archive",
    "generate_delivery_message",
]

# Completeness requirements per delivery_type.
# Maps delivery_type -> list of required fields in the delivery info.
_COMPLETENESS_REQUIREMENTS: dict[str, list[str]] = {
    "files": [],  # No extra fields required beyond base.
    "credentials": ["credentials"],
    "deploy": ["deploy_url"],
    "instructions": ["setup_instructions"],
    "mixed": ["credentials", "deploy_url", "setup_instructions"],
}


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
        1. Read delivery_type from state (validated).
        2. Collect all artifacts from state (dev, content, design agents).
        3. Call the LLM to generate the delivery package description.
        4. Parse and validate the response.
        5. Validate completeness based on delivery_type.
        6. Store delivery info in artifacts.
        7. Create HITL queue entry for final review.
        8. Set requires_hitl=True, status='paused', next_agent=None.
        9. Log the packaging action and return updated state.
        """
        self._log.info("packager_execute_start", thread_id=state["thread_id"])

        project = state.get("project") or {}
        project_id = project.get("project_id", "unknown")

        # 1. Read and validate delivery_type from state.
        delivery_type = validate_delivery_type(state.get("delivery_type", "files"))

        self._log.info(
            "packager_delivery_type",
            project_id=project_id,
            delivery_type=delivery_type,
        )

        # 2. Collect artifacts from all execution agents.
        all_artifacts = state.get("artifacts") or {}
        collected = self._collect_execution_artifacts(all_artifacts)

        if not collected:
            self._log.warning("no_artifacts_to_package", project_id=project_id)
            await self._log_packaging_action(
                project_id=project_id,
                delivery_info={"status": "no_artifacts", "delivery_type": delivery_type},
                thread_id=state["thread_id"],
                note="No artifacts found to package",
            )
            return update_state(
                state,
                current_agent="packager",
                next_agent=None,
                status="active",
            )

        # 3. Call LLM to generate the delivery package description.
        delivery_info = await self._generate_delivery_package(
            project,
            collected,
            delivery_type,
        )
        if delivery_info is None:
            self._log.error("delivery_generation_failed", project_id=project_id)
            # Even on LLM failure, create a basic delivery info.
            delivery_info = self._build_fallback_delivery(
                project_id,
                collected,
                delivery_type,
            )

        # CRITICAL: Enforce requires_hitl invariant.
        delivery_info["requires_hitl"] = True

        # Ensure delivery_type is set in the delivery info.
        delivery_info["delivery_type"] = delivery_type

        # 4. Validate completeness based on delivery_type.
        missing = self._validate_delivery_completeness(delivery_type, delivery_info)
        if missing:
            existing_missing = delivery_info.get("missing_artifacts", [])
            if not isinstance(existing_missing, list):
                existing_missing = []
            delivery_info["missing_artifacts"] = existing_missing + missing

        # 4b. Execution cloaking: check delivery schedule.
        self._check_delivery_schedule(state, delivery_info)

        # 4c. Delivery guard: hold if delivery is too early.
        if delivery_info.get("delivery_hold"):
            artifacts = dict(all_artifacts)
            delivery_serialized = json.dumps(delivery_info, default=str)
            existing = list(artifacts.get("packager", []))
            existing.append(delivery_serialized)
            artifacts["packager"] = existing

            hitl_id = await self._create_delivery_hold_entry(
                project,
                delivery_info,
                state["thread_id"],
            )
            self._log.info(
                "delivery_held",
                project_id=project_id,
                reason=delivery_info.get("delivery_hold_reason"),
            )
            return update_state(
                state,
                current_agent="packager",
                next_agent=None,
                artifacts=artifacts,
                requires_hitl=True,
                hitl_request_id=hitl_id,
                status="paused",
            )

        # 5. Store delivery info as artifact.
        artifacts = dict(all_artifacts)
        delivery_serialized = json.dumps(delivery_info, default=str)
        existing = list(artifacts.get("packager", []))
        existing.append(delivery_serialized)
        artifacts["packager"] = existing

        # 6. Create HITL queue entry for final review.
        hitl_id = await self._create_hitl_entry(
            project,
            delivery_info,
            state["thread_id"],
        )

        # 7. Log the packaging action.
        await self._log_packaging_action(
            project_id=project_id,
            delivery_info=delivery_info,
            thread_id=state["thread_id"],
        )

        self._log.info(
            "delivery_package_ready",
            project_id=project_id,
            delivery_type=delivery_type,
            files_count=delivery_info.get("files_count", 0),
            hitl_id=hitl_id,
            missing_count=len(delivery_info.get("missing_artifacts", [])),
        )

        # 8. CRITICAL: requires_hitl MUST always be True before delivery.
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
        delivery_type: str = "files",
    ) -> dict[str, Any] | None:
        """Call the LLM to generate a delivery package description.

        Returns a parsed delivery dict or ``None`` on failure.
        """
        project_text = json.dumps(project, indent=2, default=str, ensure_ascii=False)
        artifacts_summary = json.dumps(
            {k: len(v) for k, v in collected_artifacts.items()},
            indent=2,
        )

        delivery_context = self._build_delivery_context(delivery_type)

        messages = [
            SystemMessage(content=PACKAGER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Prepare a delivery package for the following project.\n\n"
                    f"Project:\n{project_text}\n\n"
                    f"Artifacts available (agent -> count):\n{artifacts_summary}\n\n"
                    f"Delivery type: {delivery_type}\n"
                    f"{delivery_context}\n\n"
                    "Return a single JSON object matching the output format specified "
                    "in your instructions."
                )
            ),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.3)
        raw_text = str(response_msg.content)
        return self._parse_delivery_response(raw_text)

    @staticmethod
    def _build_delivery_context(delivery_type: str) -> str:
        """Build additional context string for the LLM based on delivery_type."""
        contexts = {
            "files": (
                "Package as a standard file delivery (ZIP/repo). "
                "Focus on clean folder structure, README, and installation steps."
            ),
            "credentials": (
                "This is a service/bot delivery. Include credentials object with "
                "masked sensitive values (***masked***) and detailed setup_instructions "
                "for the client to configure and run the service."
            ),
            "deploy": (
                "This project is deployed to hosting. Include the deploy_url "
                "and setup_instructions covering DNS, hosting configuration, "
                "and maintenance procedures."
            ),
            "instructions": (
                "This is a consulting/audit delivery. Focus on setup_instructions "
                "with a detailed report, recommendations, action items, and "
                "implementation roadmap. No code delivery expected."
            ),
            "mixed": (
                "This is a comprehensive delivery combining multiple types. "
                "Include credentials, deploy_url, AND setup_instructions. "
                "Organise each section clearly."
            ),
        }
        return contexts.get(delivery_type, contexts["files"])

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

        # Ensure required base fields have defaults.
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
    # Delivery completeness validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_delivery_completeness(
        delivery_type: str,
        delivery_info: dict[str, Any],
    ) -> list[str]:
        """Check whether the delivery info has all fields required by its type.

        Returns a list of missing field descriptions. Empty list = complete.
        """
        required = _COMPLETENESS_REQUIREMENTS.get(delivery_type, [])
        missing: list[str] = []

        for field in required:
            value = delivery_info.get(field)
            if not value:
                label = field.replace("_", " ").title()
                missing.append(f"Missing {label}: required for {delivery_type} delivery")

        return missing

    # ------------------------------------------------------------------
    # Delivery schedule check (Execution Cloaking)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_delivery_schedule(
        state: dict[str, Any],
        delivery_info: dict[str, Any],
    ) -> None:
        """Flag early delivery if current time is before ``min_delivery_at``.

        Adds ``delivery_hold`` and ``delivery_hold_reason`` to *delivery_info*
        when the package is ready too early.  The HITL operator can still
        approve — this is informational only.
        """
        from datetime import UTC, datetime  # noqa: PLC0415

        min_at = state.get("min_delivery_at")
        if min_at is None:
            return

        # Ensure timezone-aware comparison (checkpoint deserialization may strip tzinfo).
        if hasattr(min_at, "tzinfo") and min_at.tzinfo is None:
            min_at = min_at.replace(tzinfo=UTC)

        now = datetime.now(tz=UTC)
        if now < min_at:
            remaining = min_at - now
            hours_left = remaining.total_seconds() / 3600
            delivery_info["delivery_hold"] = True
            delivery_info["delivery_hold_reason"] = (
                f"Delivery too early — {hours_left:.0f} hours remaining before minimum delivery time"
            )

    # ------------------------------------------------------------------
    # Fallback delivery info
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fallback_delivery(
        project_id: str,
        collected: dict[str, list[str]],
        delivery_type: str = "files",
    ) -> dict[str, Any]:
        """Build a basic delivery info when LLM generation fails."""
        total_artifacts = sum(len(v) for v in collected.values())
        includes = [f"{agent}_artifacts" for agent in collected]

        return {
            "delivery_id": str(uuid.uuid4()),
            "project_id": project_id,
            "delivery_type": delivery_type,
            "files_count": total_artifacts,
            "includes": includes,
            "delivery_message": ("Your project delivery is ready for review. Please check all included artifacts."),
            "readme_content": "",
            "missing_artifacts": [],
            "quality_notes": "Delivery assembled from available artifacts.",
            "requires_hitl": True,
        }

    # ------------------------------------------------------------------
    # Delivery hold HITL entry (Execution Cloaking)
    # ------------------------------------------------------------------

    async def _create_delivery_hold_entry(
        self,
        project: dict[str, Any],
        delivery_info: dict[str, Any],
        thread_id: str = "",
    ) -> str:
        """Create HITL entry for delivery hold (execution cloaking).

        When the package is ready before ``min_delivery_at``, this entry
        gives the operator a choice to deliver now or wait.
        """
        hitl_id = uuid.uuid4()
        project_id = project.get("project_id", "unknown")

        async with get_db_session() as session:
            hitl = HITLQueue(
                id=hitl_id,
                type="delivery_hold",
                priority="medium",
                title=f"Delivery hold: project {project_id}",
                description=delivery_info.get(
                    "delivery_hold_reason",
                    "Delivery ready too early — waiting for minimum delivery time",
                ),
                payload={
                    "project": project,
                    "delivery_info": delivery_info,
                    "source_agent": "packager",
                    "thread_id": thread_id,
                },
                available_actions=["deliver_now", "wait"],
                status="pending",
            )
            session.add(hitl)

        self._log.info(
            "delivery_hold_created",
            hitl_id=str(hitl_id),
            project_id=project_id,
        )
        return str(hitl_id)

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
        delivery_type = delivery_info.get("delivery_type", "files")

        async with get_db_session() as session:
            hitl = HITLQueue(
                id=hitl_id,
                type="final_review",
                priority="high",
                title=f"Final review [{delivery_type}]: project {project_id}",
                description=(
                    f"Delivery ready for project '{project_id}' "
                    f"(type: {delivery_type}). "
                    f"Files: {delivery_info.get('files_count', 0)} | "
                    f"Includes: {', '.join(delivery_info.get('includes', []))} | "
                    f"Missing: {len(delivery_info.get('missing_artifacts', []))}"
                ),
                payload={
                    "project": project,
                    "delivery_info": delivery_info,
                    "delivery_type": delivery_type,
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
            delivery_type=delivery_type,
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
        delivery_type = delivery_info.get("delivery_type", "files")
        message = (
            f"Delivery package prepared for project '{project_id}' "
            f"[{delivery_type}]: {delivery_info.get('files_count', 0)} files"
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
                    "delivery_type": delivery_type,
                    "files_count": delivery_info.get("files_count", 0),
                    "includes": delivery_info.get("includes", []),
                    "missing_artifacts": delivery_info.get("missing_artifacts", []),
                    "requires_hitl": True,
                    "thread_id": thread_id,
                },
                llm_model="deepseek-v3.2",
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
