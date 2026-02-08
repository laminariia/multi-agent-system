"""Dev Agent -- generates production-quality code for freelance projects.

The Dev Agent:
1. Receives a task from the Planner (or directly from the pipeline).
2. Builds context from the project requirements and planner's task list.
3. Calls the LLM with DEV_SYSTEM_PROMPT to generate code files.
4. Parses the structured JSON response (files, dependencies, etc.).
5. Stores code artifacts and routes to the Content Agent (next in pipeline).
6. Logs every action to the ``agent_logs`` table.

Role constraints: can WRITE code/tests/docs, CANNOT access external networks,
CANNOT submit proposals.
LLM: Claude Opus 4.6 (fallback Claude Sonnet 4.5).
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
from src.core.llm_client import LLMClient
from src.core.loop_detector import LoopDetector
from src.core.models import AgentLog
from src.core.state import AgentState, update_state
from src.prompts.dev import DEV_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Tools that the Dev Agent is allowed to invoke.
DEV_ALLOWED_TOOLS: list[str] = [
    "generate_code",
    "run_in_sandbox",
    "run_tests",
    "analyze_with_semgrep",
    "deploy_to_preview",
]


class DevAgent(ConstrainedAgent):
    """Generates production-quality code from project requirements and plans.

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
            agent_name="dev",
            allowed_tools=DEV_ALLOWED_TOOLS,
            llm_client=llm_client,
            heartbeat=heartbeat,
            loop_detector=loop_detector,
        )

    # ------------------------------------------------------------------
    # Core execution (called by base.invoke)
    # ------------------------------------------------------------------

    async def _execute(self, state: AgentState) -> AgentState:
        """Generate code for the current task and route to Content Agent.

        Flow:
        1. Extract current task from state (planner artifacts or current_task).
        2. Build context from project requirements and plan.
        3. Call LLM with DEV_SYSTEM_PROMPT.
        4. Parse JSON response with files[], dependencies[].
        5. Store as artifacts["dev"].
        6. Set next_agent="content".
        7. Log action to AgentLog.
        8. Return updated state.
        """
        self._log.info("dev_execute_start", thread_id=state["thread_id"])

        # 1. Get current task / plan context.
        task_context = self._extract_task_context(state)
        if not task_context:
            self._log.warning("no_task_context_found")
            return update_state(state, current_agent="dev", next_agent=None, status="active")

        # 2. Build the LLM prompt with full project context.
        user_content = self._build_user_prompt(state, task_context)

        messages = [
            SystemMessage(content=DEV_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        # 3. Call LLM to generate code.
        response_msg, _metrics = await self._call_llm(messages, temperature=0.3, max_tokens=16000)
        raw_text = str(response_msg.content)

        # 4. Parse the JSON response.
        parsed = self._parse_code_response(raw_text)
        if parsed is None:
            self._log.error("dev_code_parse_failed", raw_preview=raw_text[:300])
            return update_state(
                state,
                current_agent="dev",
                next_agent=None,
                status="active",
                errors=[*state["errors"], "Dev Agent: failed to parse LLM code response"],
            )

        # 5. Serialize and store artifacts.
        artifacts = dict(state.get("artifacts") or {})
        code_artifact_id = str(uuid.uuid4())
        serialized = json.dumps(parsed, default=str, ensure_ascii=False)
        artifacts["dev"] = [code_artifact_id, serialized]

        # 6. Log the action.
        await self._log_code_generated(
            thread_id=state["thread_id"],
            artifact_id=code_artifact_id,
            files_count=len(parsed.get("files", [])),
            dependencies=parsed.get("dependencies", []),
        )

        # 7. Route to content agent (next in pipeline).
        return update_state(
            state,
            current_agent="dev",
            next_agent="content",
            artifacts=artifacts,
            status="active",
        )

    # ------------------------------------------------------------------
    # Task context extraction
    # ------------------------------------------------------------------

    def _extract_task_context(self, state: AgentState) -> dict[str, Any] | None:
        """Extract the current task or first task from the planner's plan.

        Checks in order:
        1. ``state["current_task"]`` (if already set by the graph).
        2. ``state["artifacts"]["planner"]`` (serialised plan from Planner Agent).
        3. Falls back to the project requirements as a single task.
        """
        # Check current_task first.
        current_task = state.get("current_task")
        if current_task:
            return current_task

        # Check planner artifacts for a plan.
        artifacts = state.get("artifacts") or {}
        planner_data = artifacts.get("planner")
        if planner_data:
            plan = self._deserialize_plan(planner_data)
            if plan:
                return plan

        # Fall back to raw project requirements.
        project = state.get("project")
        if project and project.get("requirements"):
            return {
                "description": project["requirements"],
                "assigned_agent": "dev",
                "source": "project_requirements",
            }

        return None

    def _deserialize_plan(self, planner_artifacts: list[str]) -> dict[str, Any] | None:
        """Attempt to deserialise the planner's JSON plan from its artifacts list."""
        for item in planner_artifacts:
            try:
                parsed = json.loads(item)
                if isinstance(parsed, dict):
                    # Return the plan dict itself; extract first task if available.
                    phases = parsed.get("phases", [])
                    if phases:
                        for phase in phases:
                            tasks = phase.get("tasks", [])
                            for task in tasks:
                                if task.get("assigned_to") == "dev" or task.get("assigned_agent") == "dev":
                                    return task
                        # No dev-specific task found; return first task.
                        first_phase_tasks = phases[0].get("tasks", [])
                        if first_phase_tasks:
                            return first_phase_tasks[0]
                    # Plan without phases -- return the plan as context.
                    return parsed
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_user_prompt(self, state: AgentState, task_context: dict[str, Any]) -> str:
        """Build the user-facing prompt with project requirements and task details."""
        project = state.get("project") or {}
        parts: list[str] = []

        # Project overview.
        parts.append("## Project Requirements")
        parts.append(f"Title/Description: {project.get('requirements', 'No requirements provided')}")
        parts.append(f"Budget: ${project.get('budget', 'N/A')}")

        client = project.get("client") or {}
        if client:
            parts.append(f"Client: {client.get('name', 'Unknown')}")

        # Task details.
        parts.append("\n## Current Task")
        task_text = json.dumps(task_context, indent=2, default=str, ensure_ascii=False)
        parts.append(task_text)

        # Include planner plan summary if available.
        artifacts = state.get("artifacts") or {}
        planner_data = artifacts.get("planner")
        if planner_data:
            parts.append("\n## Full Project Plan (from Planner Agent)")
            for item in planner_data:
                try:
                    plan = json.loads(item)
                    parts.append(json.dumps(plan, indent=2, default=str, ensure_ascii=False)[:2000])
                except (json.JSONDecodeError, TypeError):
                    parts.append(str(item)[:500])

        parts.append(
            "\n\nGenerate the code for this task. "
            "Return a single JSON object matching the output format specified in your instructions."
        )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_code_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM's code generation JSON response.

        Returns ``None`` on parse failure so the caller can handle gracefully.
        """
        text = raw.strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log.error("dev_json_parse_error", raw_preview=text[:300], error=str(exc))
            return None

        if not isinstance(parsed, dict):
            self._log.error("dev_unexpected_type", type=type(parsed).__name__)
            return None

        # Validate required field: files.
        files = parsed.get("files")
        if not isinstance(files, list) or len(files) == 0:
            self._log.warning("dev_no_files_in_response")
            return None

        # Validate each file has path and content.
        validated_files: list[dict[str, Any]] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            if not f.get("path") or not f.get("content"):
                continue
            if "language" not in f:
                f["language"] = self._infer_language(f["path"])
            validated_files.append(f)

        if not validated_files:
            self._log.warning("dev_no_valid_files")
            return None

        parsed["files"] = validated_files

        # Ensure optional fields have defaults.
        if not isinstance(parsed.get("dependencies"), list):
            parsed["dependencies"] = []
        if not isinstance(parsed.get("build_commands"), list):
            parsed["build_commands"] = []
        if not isinstance(parsed.get("test_commands"), list):
            parsed["test_commands"] = []
        if not isinstance(parsed.get("deployment_notes"), str):
            parsed["deployment_notes"] = ""

        return parsed

    @staticmethod
    def _infer_language(path: str) -> str:
        """Infer the programming language from a file extension."""
        ext_map: dict[str, str] = {
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".py": "python",
            ".html": "html",
            ".css": "css",
            ".json": "json",
            ".md": "markdown",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".sh": "bash",
            ".sql": "sql",
            ".php": "php",
        }
        for ext, lang in ext_map.items():
            if path.endswith(ext):
                return lang
        return "text"

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _log_code_generated(
        self,
        *,
        thread_id: str,
        artifact_id: str,
        files_count: int,
        dependencies: list[str],
    ) -> None:
        """Write an audit log entry for the generated code."""
        async with get_db_session() as session:
            log_entry = AgentLog(
                id=uuid.uuid4(),
                agent_name="dev",
                event_type="code_generated",
                message=f"Dev Agent generated {files_count} file(s)",
                details={
                    "thread_id": thread_id,
                    "artifact_id": artifact_id,
                    "files_count": files_count,
                    "dependencies": dependencies,
                },
                llm_model="claude-opus-4-6",
            )
            session.add(log_entry)

        self._log.info(
            "code_generated",
            artifact_id=artifact_id,
            files_count=files_count,
            deps_count=len(dependencies),
        )


# ======================================================================
# Module-level node function for LangGraph
# ======================================================================

async def dev_node(state: AgentState) -> AgentState:
    """LangGraph node function that creates and invokes the Dev Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    llm_client = LLMClient()
    heartbeat = HeartbeatMonitor(
        valkey=_get_valkey_client(),
        db_pool=None,  # DB pool injected at app startup in production
    )
    loop_detector = LoopDetector(max_iterations=50, max_identical_steps=3)

    agent = DevAgent(
        llm_client=llm_client,
        heartbeat=heartbeat,
        loop_detector=loop_detector,
    )

    return await agent.invoke(state)


def _get_valkey_client() -> Any:
    """Return the shared Valkey (redis-py) async client."""
    from src.core.database import get_valkey  # noqa: PLC0415
    return get_valkey()
