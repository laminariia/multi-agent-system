"""Planner Agent -- decomposes projects into actionable tasks and timelines.

The Planner Agent:
1. Reads project requirements from the state.
2. Calls the LLM (Claude Opus 4.6) with PLANNER_SYSTEM_PROMPT to produce a
   structured task decomposition.
3. Parses the JSON response into a plan.
4. Stores the plan as artifacts and routes to the first execution agent.
5. Logs all planning decisions to the ``agent_logs`` table.

Role constraints: can PLAN tasks, CANNOT execute code, CANNOT submit proposals.
LLM: Claude Opus 4.6 (fallback Claude Sonnet 4.5).
"""

from __future__ import annotations

import json
import re
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
from src.core.state import AgentState, update_state, validate_delivery_type
from src.prompts.planner import PLANNER_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Planner Agent is allowed to invoke.
PLANNER_ALLOWED_TOOLS: list[str] = [
    "create_project_plan",
    "estimate_task_duration",
    "assign_task_to_agent",
    "update_project_status",
    "detect_blockers",
]

# Maximum re-plans per project before HITL escalation.
_MAX_REPLANS = 3

# Plan complexity threshold: if total_estimated_hours exceeds this,
# require HITL approval before execution begins.
_HITL_PLAN_REVIEW_HOURS_THRESHOLD = 20.0

# ---------------------------------------------------------------------------
# Delivery type inference keyword sets (case-insensitive matching)
# ---------------------------------------------------------------------------

_DEPLOY_KEYWORDS: frozenset[str] = frozenset(
    {
        "deploy",
        "hosting",
        "хостинг",
        "домен",
        "domain",
        "лендинг",
        "web app",
        "веб-приложение",
        "задеплоить",
        "разместить",
        "опубликовать",
        "publish",
    }
)

_CREDENTIALS_KEYWORDS: frozenset[str] = frozenset(
    {
        "бот",
        "bot",
        "telegram",
        "телеграм",
        "api",
        "сервис",
        "service",
        "token",
        "токен",
        "аккаунт",
        "account",
        "доступ",
        "access",
        "credentials",
        "авторизаци",
        "authentication",
    }
)

_INSTRUCTIONS_KEYWORDS: frozenset[str] = frozenset(
    {
        "аудит",
        "audit",
        "консалтинг",
        "consulting",
        "анализ",
        "analysis",
        "рекомендаци",
        "recommendations",
        "план",
        "plan",
        "стратеги",
        "strategy",
        "обзор",
        "review",
        "документаци",
        "documentation",
        "гайд",
        "guide",
        "инструкци",
    }
)


class PlannerAgent(ConstrainedAgent):
    """Decomposes projects into actionable tasks and creates timelines.

    Parameters
    ----------
    llm_client:
        Shared :class:`LLMClient` instance (Claude Opus 4.6 primary).
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
            agent_name="planner",
            allowed_tools=PLANNER_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Generate a task decomposition plan and route to execution agents.

        Flow:
        1. Extract project requirements from state.
        2. Call the LLM to produce a structured plan.
        3. Parse and validate the plan JSON.
        4. Resolve delivery_type: LLM plan > inference from requirements > 'files'.
        5. Store plan in artifacts and log the decision.
        6. Route to the first execution agent in the sequence.
        """
        self._log.info("planner_execute_start", thread_id=state["thread_id"])

        # 1. Extract project context.
        project = state.get("project") or {}
        requirements = project.get("requirements", "")
        project_id = project.get("project_id", "unknown")

        if not requirements:
            self._log.warning("empty_requirements", project_id=project_id)
            # Produce a minimal plan flagging the gap.
            minimal_plan = self._build_minimal_plan(project_id)
            artifacts = dict(state.get("artifacts") or {})
            artifacts["planner"] = [json.dumps(minimal_plan, default=str)]

            await self._log_planning_action(
                project_id=project_id,
                plan=minimal_plan,
                thread_id=state["thread_id"],
                note="Empty requirements -- minimal plan generated",
            )

            agent_sequence = self._build_agent_sequence(minimal_plan)
            return update_state(
                state,
                current_agent="planner",
                agent_sequence=agent_sequence,
                current_sequence_index=0,
                delivery_type="files",
                artifacts=artifacts,
                status="active",
            )

        # 2. Check re-plan count (from existing planner artifacts).
        existing_plans = (state.get("artifacts") or {}).get("planner", [])
        replan_count = len(existing_plans)

        if replan_count >= _MAX_REPLANS:
            self._log.warning(
                "max_replans_exceeded",
                project_id=project_id,
                replan_count=replan_count,
            )
            return update_state(
                state,
                current_agent="planner",
                next_agent=None,
                requires_hitl=True,
                hitl_request_id=str(uuid.uuid4()),
                status="paused",
                errors=[*state["errors"], f"Planner exceeded max re-plans ({_MAX_REPLANS})"],
            )

        # 3. Call LLM to generate the plan.
        plan = await self._generate_plan(project)
        if plan is None:
            self._log.error("plan_generation_failed", project_id=project_id)
            return update_state(
                state,
                current_agent="planner",
                next_agent=None,
                status="active",
                errors=[*state["errors"], "Planner LLM returned unparseable response"],
            )

        # Ensure project_id is set in the plan.
        plan["project_id"] = project_id

        # 4. Resolve delivery_type: LLM plan > inference > default.
        delivery_type = self._resolve_delivery_type(plan, requirements)
        plan["delivery_type"] = delivery_type

        # 5. Store plan as artifact.
        artifacts = dict(state.get("artifacts") or {})
        plan_serialized = json.dumps(plan, default=str)
        existing = list(artifacts.get("planner", []))
        existing.append(plan_serialized)
        artifacts["planner"] = existing

        # 6. Determine the first execution agent from the plan.
        first_agent = self._determine_first_agent(plan)

        # 6b. Check if plan requires HITL review (complex plans or re-plans
        # triggered by Critic major revisions).
        needs_hitl = self._should_request_plan_review(state, plan)

        # 7. Log the planning action.
        await self._log_planning_action(
            project_id=project_id,
            plan=plan,
            thread_id=state["thread_id"],
            note="HITL plan review requested" if needs_hitl else "",
        )

        self._log.info(
            "plan_generated",
            project_id=project_id,
            total_hours=plan.get("total_estimated_hours"),
            phases=len(plan.get("phases", [])),
            next_agent=first_agent,
            needs_hitl=needs_hitl,
            delivery_type=delivery_type,
        )

        # Build agent sequence from plan (fallback to default pipeline).
        agent_sequence = self._build_agent_sequence(plan)

        # Extract real_hours for execution cloaking.
        real_hours = float(plan.get("total_estimated_hours", 0) or 0)

        if needs_hitl:
            return update_state(
                state,
                current_agent="planner",
                agent_sequence=agent_sequence,
                current_sequence_index=0,
                delivery_type=delivery_type,
                real_hours=real_hours,
                artifacts=artifacts,
                requires_hitl=True,
                hitl_request_id=str(uuid.uuid4()),
                status="paused",
            )

        return update_state(
            state,
            current_agent="planner",
            agent_sequence=agent_sequence,
            current_sequence_index=0,
            delivery_type=delivery_type,
            real_hours=real_hours,
            artifacts=artifacts,
            status="active",
        )

    # ------------------------------------------------------------------
    # Delivery type resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_delivery_type(plan: dict[str, Any], requirements: str) -> str:
        """Resolve delivery_type from plan or infer from requirements.

        Priority:
        1. If the LLM plan contains a valid ``delivery_type``, use it.
        2. Otherwise, infer from project requirements keywords.
        3. Fall back to ``"files"`` as the ultimate default.
        """
        plan_dt = plan.get("delivery_type", "")
        if isinstance(plan_dt, str) and plan_dt:
            validated = validate_delivery_type(plan_dt)
            if validated == plan_dt:
                return validated
            # LLM returned an invalid value — fall through to inference.

        return PlannerAgent._infer_delivery_type(requirements)

    @staticmethod
    def _infer_delivery_type(requirements: str) -> str:
        """Infer delivery_type from project requirements using keyword analysis.

        Analyses the requirements text for domain-specific keywords in both
        English and Russian. If multiple delivery categories are detected,
        returns ``"mixed"``.

        Returns one of: ``"files"``, ``"credentials"``, ``"deploy"``,
        ``"instructions"``, or ``"mixed"``.
        """
        text = requirements.lower()
        # Tokenise for whole-word matching; also allow substring matching
        # for keywords >= 4 chars to catch Russian declined forms (e.g.
        # "хостинг" in "хостинге") without false positives from short
        # keywords like "бот" matching inside "разработать".
        words = set(re.findall(r"[\w]+", text))

        def _hits(keywords: frozenset[str]) -> int:
            return sum(1 for kw in keywords if kw in words or (len(kw) >= 4 and kw in text))

        deploy_hits = _hits(_DEPLOY_KEYWORDS)
        cred_hits = _hits(_CREDENTIALS_KEYWORDS)
        instr_hits = _hits(_INSTRUCTIONS_KEYWORDS)

        categories_found = sum(1 for h in (deploy_hits, cred_hits, instr_hits) if h > 0)

        # Mixed: two or more distinct delivery categories detected.
        if categories_found >= 2:
            return "mixed"

        # Single dominant category.
        if deploy_hits > 0:
            return "deploy"
        if cred_hits > 0:
            return "credentials"
        if instr_hits > 0:
            return "instructions"

        return "files"

    # ------------------------------------------------------------------
    # LLM plan generation
    # ------------------------------------------------------------------

    async def _generate_plan(self, project: dict[str, Any]) -> dict[str, Any] | None:
        """Call the LLM to generate a structured project plan.

        Returns a parsed plan dict or ``None`` on failure.
        """
        project_text = json.dumps(project, indent=2, default=str, ensure_ascii=False)

        messages = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Decompose the following project into a structured plan.\n\n"
                    f"Project:\n{project_text}\n\n"
                    "Return a single JSON object matching the output format specified "
                    "in your instructions."
                )
            ),
        ]

        response_msg, _metrics = await self._call_llm(messages, temperature=0.3)
        raw_text = str(response_msg.content)
        return self._parse_plan_response(raw_text)

    def _parse_plan_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's plan JSON response using extract_json.

        Returns a validated plan dict or ``None`` on failure.
        """
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error("planner_json_parse_error", raw_preview=raw[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("planner_unexpected_type", type=type(parsed).__name__)
            return None

        # Validate required top-level fields.
        if "phases" not in parsed:
            self._log.warning("planner_missing_phases")
            parsed["phases"] = []

        if not isinstance(parsed.get("phases"), list):
            parsed["phases"] = []

        # Ensure numeric fields.
        try:
            parsed["total_estimated_hours"] = float(parsed.get("total_estimated_hours", 0))
        except (TypeError, ValueError):
            parsed["total_estimated_hours"] = 0.0

        # Ensure list fields.
        if not isinstance(parsed.get("critical_path"), list):
            parsed["critical_path"] = []

        if not isinstance(parsed.get("risks"), list):
            parsed["risks"] = []

        # Validate individual tasks within phases.
        for phase in parsed["phases"]:
            if not isinstance(phase, dict):
                continue
            if not isinstance(phase.get("tasks"), list):
                phase["tasks"] = []
            for task in phase["tasks"]:
                if not isinstance(task, dict):
                    continue
                # Ensure required task fields have defaults.
                task.setdefault("id", f"task_{uuid.uuid4().hex[:8]}")
                task.setdefault("description", "")
                task.setdefault("assigned_to", "dev")
                try:
                    task["estimated_hours"] = float(task.get("estimated_hours", 1.0))
                except (TypeError, ValueError):
                    task["estimated_hours"] = 1.0
                if not isinstance(task.get("dependencies"), list):
                    task["dependencies"] = []
                if not isinstance(task.get("deliverables"), list):
                    task["deliverables"] = []

        return parsed

    # ------------------------------------------------------------------
    # Helper: determine first execution agent from plan
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_first_agent(plan: dict[str, Any]) -> str:
        """Determine which agent should run first based on the plan's first task.

        Defaults to ``"dev"`` if no tasks exist or the assignment is unrecognised.
        """
        valid_agents = {"dev", "content", "design", "critic"}

        for phase in plan.get("phases", []):
            for task in phase.get("tasks", []):
                assigned = task.get("assigned_to", "")
                if assigned in valid_agents:
                    return assigned

        return "dev"

    @staticmethod
    def _build_agent_sequence(plan: dict[str, Any]) -> list[str]:
        """Build ordered execution agent sequence from plan tasks.

        Extracts unique execution agents (dev, content, design) in the order
        they first appear in the plan phases/tasks.

        Falls back to ``["dev", "content", "design"]`` when the plan has no
        recognisable execution agents (backward compatibility).
        """
        valid_execution = {"dev", "content", "design"}
        seen: list[str] = []

        for phase in plan.get("phases", []):
            for task in phase.get("tasks", []):
                assigned = task.get("assigned_to", "")
                if assigned in valid_execution and assigned not in seen:
                    seen.append(assigned)

        return seen if seen else ["dev", "content", "design"]

    # ------------------------------------------------------------------
    # Helper: HITL plan review gate
    # ------------------------------------------------------------------

    def _should_request_plan_review(self, state: AgentState, plan: dict[str, Any]) -> bool:
        """Determine whether this plan should go through HITL review.

        HITL plan review is triggered when:
        1. The plan is complex (estimated hours exceed threshold).
        2. This is a re-plan triggered by a Critic major revision.
        """
        # Complex plan check: high estimated hours.
        total_hours = plan.get("total_estimated_hours", 0.0)
        if total_hours >= _HITL_PLAN_REVIEW_HOURS_THRESHOLD:
            self._log.info(
                "plan_review_triggered_by_complexity",
                total_hours=total_hours,
                threshold=_HITL_PLAN_REVIEW_HOURS_THRESHOLD,
            )
            return True

        # Re-plan from Critic major revision check.
        artifacts = state.get("artifacts") or {}
        revision_type_data = artifacts.get("_critic_revision_type", [])
        if revision_type_data and isinstance(revision_type_data, list) and len(revision_type_data) > 0:
            if revision_type_data[0] == "major":
                self._log.info("plan_review_triggered_by_major_revision")
                return True

        return False

    # ------------------------------------------------------------------
    # Helper: minimal plan for empty requirements
    # ------------------------------------------------------------------

    @staticmethod
    def _build_minimal_plan(project_id: str) -> dict[str, Any]:
        """Build a minimal placeholder plan when requirements are empty."""
        return {
            "project_id": project_id,
            "phases": [
                {
                    "name": "Clarification",
                    "tasks": [
                        {
                            "id": "task_clarify",
                            "description": "Requirements are empty or unclear. "
                            "Clarify with the client before proceeding.",
                            "assigned_to": "dev",
                            "estimated_hours": 0.5,
                            "dependencies": [],
                            "deliverables": ["clarified_requirements"],
                        },
                    ],
                },
            ],
            "total_estimated_hours": 0.5,
            "critical_path": ["task_clarify"],
            "risks": [
                "Requirements are empty -- project cannot proceed without clarification",
            ],
        }

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_planning_action(
        self,
        *,
        project_id: str,
        plan: dict[str, Any],
        thread_id: str,
        note: str = "",
    ) -> None:
        """Write an audit log entry for the generated plan."""
        total_tasks = sum(len(phase.get("tasks", [])) for phase in plan.get("phases", []))

        message = (
            f"Plan generated for project '{project_id}': "
            f"{len(plan.get('phases', []))} phases, {total_tasks} tasks, "
            f"{plan.get('total_estimated_hours', 0)} estimated hours"
        )
        if note:
            message += f" [{note}]"

        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="planner",
                event_type="plan_generated",
                message=message,
                details={
                    "project_id": project_id,
                    "phases_count": len(plan.get("phases", [])),
                    "total_tasks": total_tasks,
                    "total_estimated_hours": plan.get("total_estimated_hours", 0),
                    "delivery_type": plan.get("delivery_type", "files"),
                    "critical_path": plan.get("critical_path", []),
                    "risks_count": len(plan.get("risks", [])),
                    "thread_id": thread_id,
                },
                llm_model="claude-opus-4-6",
            )
            session.add(log_entry)

        self._log.info(
            "planning_logged",
            project_id=project_id,
            total_tasks=total_tasks,
        )


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================


async def planner_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the Planner Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = PlannerAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
