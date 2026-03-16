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
LLM: Claude Opus 4.6 (Tier 1: complex) / Claude Sonnet 4.6 (Tier 3: standard).
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
from src.prompts.dev import DEV_SYSTEM_PROMPT
from src.sandbox.manager import SandboxManager
from src.security.semgrep_gate import SemgrepGate

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

        # 1b. Check if this is a revision cycle (critic feedback present).
        critic_feedback = self._extract_critic_feedback(state)

        # 2. Build the LLM prompt with full project context.
        if critic_feedback:
            user_content = self._build_revision_prompt(state, task_context, critic_feedback)
        else:
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

        # 4b. Semgrep security scan on generated code.
        gate = SemgrepGate()
        scan_result = await gate.scan_files(parsed.get("files", []))
        if scan_result.blocked:
            self._log.warning(
                "semgrep_blocked_code",
                rules=[f.rule_id for f in scan_result.findings],
            )
            artifacts["_dev_semgrep_warnings"] = [
                json.dumps({"blocked": True, "findings": [f.rule_id for f in scan_result.findings]})
            ]

        # 4c. Sandbox execution (best-effort -- don't block pipeline on sandbox failure).
        if not scan_result.blocked:
            try:
                sandbox = SandboxManager()
                exec_result = await sandbox.execute_code(parsed.get("files", []))
                artifacts["_dev_execution"] = {
                    "stdout": exec_result.stdout[:5000],
                    "stderr": exec_result.stderr[:5000],
                    "exit_code": exec_result.exit_code,
                    "duration_ms": exec_result.duration_ms,
                    "success": exec_result.success,
                }
                if not exec_result.success:
                    self._log.warning(
                        "sandbox_execution_failed",
                        exit_code=exec_result.exit_code,
                        stderr_preview=exec_result.stderr[:200],
                    )
            except Exception as exc:
                self._log.warning("sandbox_unavailable", error=str(exc))
                artifacts["_dev_execution"] = {"error": str(exc), "success": False}
                artifacts["_sandbox_skipped"] = True

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

        # 7. Advance sequence index and let routing decide next agent.
        return update_state(
            state,
            current_agent="dev",
            current_sequence_index=state.get("current_sequence_index", 0) + 1,
            revision_target=None,
            revision_severity=None,
            artifacts=artifacts,
            status="active",
        )

    # ------------------------------------------------------------------
    # Critic revision feedback
    # ------------------------------------------------------------------

    def _extract_critic_feedback(self, state: AgentState) -> dict[str, Any] | None:
        """Extract the most recent critic review from state artifacts.

        Returns the parsed review dict if present, or ``None`` for a fresh run.
        """
        artifacts = state.get("artifacts") or {}
        critic_data = artifacts.get("critic")
        if not critic_data:
            return None

        for item in reversed(critic_data):
            try:
                parsed = json.loads(item)
                if isinstance(parsed, dict) and "verdict" in parsed:
                    return parsed
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _build_revision_prompt(
        self,
        state: AgentState,
        task_context: dict[str, Any],
        critic_feedback: dict[str, Any],
    ) -> str:
        """Build a revision-aware prompt incorporating Critic Agent feedback.

        This prompt instructs the LLM to fix specific issues reported by the
        Critic rather than regenerating from scratch.
        """
        project = state.get("project") or {}
        parts: list[str] = []

        # Project overview.
        parts.append("## Project Requirements")
        parts.append(f"Title/Description: {project.get('requirements', 'No requirements provided')}")
        parts.append(f"Budget: ${project.get('budget', 'N/A')}")

        # Task details.
        parts.append("\n## Current Task")
        parts.append(json.dumps(task_context, indent=2, default=str, ensure_ascii=False))

        # Previous code (if available).
        artifacts = state.get("artifacts") or {}
        dev_data = artifacts.get("dev")
        if dev_data:
            parts.append("\n## Your Previous Code (needs revision)")
            for item in dev_data:
                try:
                    parsed = json.loads(item)
                    if isinstance(parsed, dict) and "files" in parsed:
                        parts.append(json.dumps(parsed, indent=2, default=str, ensure_ascii=False)[:4000])
                except (json.JSONDecodeError, TypeError):
                    continue

        # Critic feedback.
        parts.append("\n## Critic Agent Review Feedback")
        parts.append(f"Verdict: {critic_feedback.get('verdict', 'N/A')}")
        parts.append(f"Score: {critic_feedback.get('score', 'N/A')}")
        parts.append(f"Revision Type: {critic_feedback.get('revision_type', 'N/A')}")

        issues = critic_feedback.get("issues", [])
        if issues:
            parts.append("\n### Issues to Fix:")
            for idx, issue in enumerate(issues, 1):
                desc = issue.get("description", str(issue))
                severity = issue.get("severity", "unknown")
                location = issue.get("location", "")
                suggestion = issue.get("suggestion", "")
                parts.append(f"{idx}. [{severity}] {desc}")
                if location:
                    parts.append(f"   Location: {location}")
                if suggestion:
                    parts.append(f"   Suggestion: {suggestion}")

        revision_instructions = critic_feedback.get("revision_instructions", "")
        if revision_instructions:
            parts.append(f"\n### Revision Instructions:\n{revision_instructions}")

        failed_checks = critic_feedback.get("failed_checks", [])
        if failed_checks:
            parts.append(f"\n### Failed Checks: {', '.join(failed_checks)}")

        # Revision count context.
        revision_count_data = artifacts.get("_critic_revision_count", [])
        if revision_count_data:
            try:
                rev_count = int(revision_count_data[0])
                parts.append(f"\n**This is revision #{rev_count}. Please fix ALL reported issues this time.**")
            except (ValueError, TypeError):
                pass

        parts.append(
            "\n\nFix the issues above in your code. Keep working code unchanged. "
            "Return a single JSON object matching the output format specified in your instructions."
        )

        return "\n".join(parts)

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
        """Parse the LLM's code generation JSON response using extract_json.

        Returns ``None`` on parse failure so the caller can handle gracefully.
        """
        try:
            parsed = extract_json(raw, expected_type=dict)
        except ValueError as exc:
            self._log.error("dev_json_parse_error", raw_preview=raw[:300], error=str(exc))
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


async def dev_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node function that creates and invokes the Dev Agent.

    This is the entry-point wired into the ``StateGraph``.
    """
    from src.core.container import get_container  # noqa: PLC0415

    container = get_container()
    agent = DevAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

    return await agent.invoke(state)
