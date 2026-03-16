"""Constrained agent base class (Feature 6 from architecture v4.2).

Every concrete agent subclass inherits from ``ConstrainedAgent`` and only needs
to implement ``_execute(state) -> AgentState``.  The base class transparently
handles:

* Heartbeat pings (liveness monitoring).
* Loop detection (iteration cap + identical-step detection).
* Role constraint validation (forbidden tools, max iterations).
* LLM delegation via ``_call_llm``.
* Error handling with retry logic.
* State bookkeeping (``current_agent``, ``updated_at``).
"""

from __future__ import annotations

import abc
import asyncio
import os
import time
from typing import Any

import structlog
from langchain_core.messages import BaseMessage

from src.core.database import get_db_session
from src.core.exceptions import (
    AgentException,
    HITLRequiredError,
    LLMException,
    LoopDetectedError,
    MASException,
)
from src.core.heartbeat import HeartbeatMonitor
from src.core.llm_client import CallMetrics, LLMClient
from src.core.loop_detector import LoopDetector
from src.core.state import AgentState, append_error, increment_retry, update_state

# Execution agents that support partial failure recovery (pause for HITL
# instead of terminating the workflow).  Non-execution agents (scout, bid,
# planner, packager) still fail hard on unrecoverable errors.
_RECOVERABLE_AGENTS: frozenset[str] = frozenset({"dev", "content", "design"})

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Role constraint definitions (canonical, from architecture v4.2)
# ---------------------------------------------------------------------------

ROLE_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "scout": {
        "role": "Job Scout",
        "goal": "Find qualified jobs matching profile",
        "constraints": [
            "ONLY search and filter jobs",
            "NEVER submit proposals",
            "NEVER contact clients directly",
            "ALWAYS pass jobs to Bid Agent",
            "STOP after finding 10 qualified jobs per cycle",
        ],
        "forbidden_tools": ["submit_proposal", "send_message", "execute_code"],
        "max_iterations": 10,
    },
    "bid": {
        "role": "Proposal Writer",
        "goal": "Generate high-quality draft proposals",
        "constraints": [
            "ONLY generate draft proposals",
            "NEVER submit without HITL approval",
            "ALWAYS include personalisation tokens",
            "STOP after generating proposal (human submits)",
        ],
        "forbidden_tools": ["execute_code", "delete_file"],
        "max_iterations": 10,
        "requires_hitl_approval": True,
    },
    "planner": {
        "role": "Task Planner",
        "goal": "Decompose projects into actionable tasks",
        "constraints": [
            "ONLY plan and decompose tasks",
            "NEVER execute code directly",
            "ALWAYS produce a structured task list",
            "MAX 3 re-plans per project",
        ],
        "forbidden_tools": ["execute_code", "submit_proposal", "send_email"],
        "max_iterations": 10,
    },
    "dev": {
        "role": "Full-Stack Developer",
        "goal": "Generate production-quality code",
        "constraints": [
            "ONLY write code, tests, documentation",
            "NEVER access external networks (sandbox isolation)",
            "ALWAYS pass code to Critic Agent before delivery",
            "MAX 5 iterations per task",
        ],
        "forbidden_tools": ["submit_proposal", "send_email", "send_message"],
        "max_iterations": 5,
    },
    "content": {
        "role": "Content Writer",
        "goal": "Produce copywriting and documentation",
        "constraints": [
            "ONLY write text content and documentation",
            "NEVER execute code",
            "NEVER submit proposals",
        ],
        "forbidden_tools": ["execute_code", "submit_proposal"],
        "max_iterations": 10,
    },
    "design": {
        "role": "Designer",
        "goal": "Create UI/UX designs and graphics",
        "constraints": [
            "ONLY produce design artefacts",
            "NEVER execute arbitrary code",
            "NEVER modify code files",
        ],
        "forbidden_tools": ["execute_code", "submit_proposal", "send_email"],
        "max_iterations": 10,
    },
    "critic": {
        "role": "Quality Reviewer",
        "goal": "Review code quality and security",
        "constraints": [
            "ONLY review and score artefacts",
            "NEVER modify code directly",
            "ALWAYS run Semgrep before approval",
            "RETURN decision: APPROVE / REVISE / REJECT",
        ],
        "forbidden_tools": ["submit_proposal", "send_email"],
        "max_iterations": 10,
    },
    "packager": {
        "role": "Delivery Packager",
        "goal": "Assemble and deliver final artefacts",
        "constraints": [
            "ONLY assemble approved artefacts",
            "NEVER modify code or content",
            "NEVER submit without HITL approval on first delivery",
        ],
        "forbidden_tools": ["execute_code"],
        "max_iterations": 10,
    },
    "geoscout": {
        "role": "Geo Business Scout",
        "goal": "Discover offline businesses via geo-search",
        "constraints": [
            "ONLY search and filter business leads",
            "NEVER contact leads directly",
            "ALWAYS pass leads to Outreach Agent",
        ],
        "forbidden_tools": ["execute_code", "submit_proposal", "send_email", "send_message"],
        "max_iterations": 10,
    },
    "outreach": {
        "role": "Outreach Specialist",
        "goal": "Enrich leads and generate cold emails",
        "constraints": [
            "ONLY enrich leads and draft emails",
            "NEVER send emails without HITL approval (first batch)",
            "ALWAYS follow email warm-up protocol",
        ],
        "forbidden_tools": ["execute_code", "submit_proposal"],
        "max_iterations": 10,
    },
}

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_NODE_TIMEOUT_SECONDS = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "600"))


class ConstrainedAgent(abc.ABC):
    """Abstract base class for all MAS agents with role enforcement.

    Subclasses implement ``_execute(state)`` to perform agent-specific work.
    Everything else (heartbeat, loop detection, constraint checking, retries,
    state updates) is handled automatically by ``invoke()``.

    Args:
        agent_name: Canonical agent identifier (e.g. ``"scout"``).
        allowed_tools: List of tool names this agent is permitted to invoke.
        llm_client: Shared ``LLMClient`` instance.
        heartbeat: Shared ``HeartbeatMonitor`` instance.
        loop_detector: Shared ``LoopDetector`` instance.
        max_retries: Per-invocation retry cap before the error propagates.
    """

    def __init__(
        self,
        agent_name: str,
        allowed_tools: list[str],
        llm_client: LLMClient,
        heartbeat: HeartbeatMonitor,
        loop_detector: LoopDetector,
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self.agent_name = agent_name
        self.allowed_tools = set(allowed_tools)
        self.llm_client = llm_client
        self.heartbeat = heartbeat
        self.loop_detector = loop_detector
        self.max_retries = max_retries

        self.role_constraints: dict[str, Any] = ROLE_CONSTRAINTS.get(agent_name, {})
        self._forbidden_tools: set[str] = set(self.role_constraints.get("forbidden_tools", []))
        self._agent_max_iterations: int = self.role_constraints.get("max_iterations", 10)

        self._log = logger.bind(agent=agent_name)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def invoke(self, state: AgentState) -> AgentState:
        """Execute this agent's logic with full lifecycle management.

        1. Load user-specific credentials (if user_id in state).
        2. Heartbeat ping.
        3. Loop detection check.
        4. Role constraint validation.
        5. Delegate to ``_execute``.
        6. Error handling + retry.
        7. State update (current_agent, updated_at).
        """
        # Mark the state as belonging to this agent
        state = update_state(
            state,
            current_agent=self.agent_name,
            current_task=state.get("current_task"),
        )

        # Load DB-stored API key into LLMClient when user context is available
        user_id = state.get("user_id")  # type: ignore[typeddict-item]
        if user_id:
            await self._load_user_credentials(user_id)

        for attempt in range(1, self.max_retries + 1):
            try:
                # 1. Heartbeat
                task_description = _describe_task(state.get("current_task"))
                await self.heartbeat.ping(self.agent_name, task_description)

                # 2. Loop detection
                await self.loop_detector.check(
                    thread_id=state["thread_id"],
                    current_step=self.agent_name,
                    state=dict(state),
                )

                # 3. Role constraints
                self._validate_role_constraints(state)

                # 4. Publish pipeline progress (best-effort)
                await self._publish_progress_start(state)

                # 5. Agent-specific logic
                self._log.info(
                    "agent_invoke_start",
                    thread_id=state["thread_id"],
                    attempt=attempt,
                )
                t0 = time.perf_counter()

                with self._sentry_transaction(state["thread_id"]):
                    result_state = await asyncio.wait_for(
                        self._execute(state),
                        timeout=_DEFAULT_NODE_TIMEOUT_SECONDS,
                    )

                elapsed_ms = (time.perf_counter() - t0) * 1000

                # 5. Finalise state
                result_state = update_state(
                    result_state,
                    current_agent=self.agent_name,
                )
                self._log.info(
                    "agent_invoke_success",
                    thread_id=result_state["thread_id"],
                    elapsed_ms=round(elapsed_ms, 2),
                    next_agent=result_state.get("next_agent"),
                )

                # Record Prometheus metrics
                status = "hitl_paused" if result_state.get("requires_hitl") else "success"
                self._record_metric(status, elapsed_ms / 1000)

                # Publish pipeline progress (best-effort)
                await self._publish_progress_complete(result_state)

                return result_state

            except LoopDetectedError:
                self._log.error("loop_detected", thread_id=state["thread_id"])
                self._record_metric("failed", 0)
                return update_state(
                    append_error(state, f"Loop detected in {self.agent_name}"),
                    status="failed",
                    next_agent=None,
                )

            except TimeoutError:
                elapsed = time.perf_counter() - t0
                reason = f"Agent timeout: {self.agent_name} timed out after {_DEFAULT_NODE_TIMEOUT_SECONDS}s"
                self._log.error(
                    "node_timeout",
                    thread_id=state["thread_id"],
                    timeout_seconds=_DEFAULT_NODE_TIMEOUT_SECONDS,
                    elapsed_seconds=round(elapsed, 1),
                )
                self._record_metric("timeout", elapsed)
                state = append_error(state, reason)

                # Create HITL alert for ALL agents on timeout (P3.14)
                import uuid as _uuid  # noqa: PLC0415

                hitl_id = _uuid.uuid4()
                try:
                    from src.core.models import HITLQueue  # noqa: PLC0415

                    async with get_db_session() as session:
                        hitl = HITLQueue(
                            id=hitl_id,
                            type="agent_timeout",
                            priority="urgent",
                            title=f"Agent '{self.agent_name}' timed out",
                            payload={
                                "failed_agent": self.agent_name,
                                "failure_reason": reason[:500],
                                "thread_id": state["thread_id"],
                            },
                            available_actions=["retry", "skip"],
                            status="pending",
                        )
                        session.add(hitl)
                except Exception:  # noqa: BLE001
                    self._log.exception(
                        "timeout_hitl_creation_failed",
                        agent=self.agent_name,
                    )

                if self.agent_name in _RECOVERABLE_AGENTS:
                    return update_state(
                        state,
                        status="paused",
                        requires_hitl=True,
                        hitl_request_id=str(hitl_id),
                        failed_agent=self.agent_name,
                        failure_reason=reason[:500],
                        next_agent=None,
                    )
                return update_state(state, status="failed", next_agent=None)

            except HITLRequiredError as exc:
                self._log.info("hitl_required", thread_id=state["thread_id"], reason=str(exc))
                await self._publish_progress_status(state, "paused")
                return update_state(
                    state,
                    requires_hitl=True,
                    hitl_request_id=exc.hitl_request_id,
                    status="paused",
                    errors=[*state["errors"], f"HITL: {exc}"],
                )

            except LLMException as exc:
                self._log.warning(
                    "llm_error",
                    thread_id=state["thread_id"],
                    attempt=attempt,
                    error=str(exc),
                )
                state = increment_retry(append_error(state, f"LLM error (attempt {attempt}): {exc}"))
                if attempt == self.max_retries:
                    self._record_metric("failed", 0)
                    if self.agent_name in _RECOVERABLE_AGENTS:
                        return await self._pause_for_recovery(state, str(exc))
                    return update_state(state, status="failed", next_agent=None)

            except AgentException as exc:
                self._log.error(
                    "agent_error",
                    thread_id=state["thread_id"],
                    attempt=attempt,
                    error=str(exc),
                )
                state = increment_retry(append_error(state, f"Agent error (attempt {attempt}): {exc}"))
                if attempt == self.max_retries:
                    self._record_metric("failed", 0)
                    if self.agent_name in _RECOVERABLE_AGENTS:
                        return await self._pause_for_recovery(state, str(exc))
                    return update_state(state, status="failed", next_agent=None)

            except MASException as exc:
                self._log.error("mas_error", thread_id=state.get("thread_id", "?"), error=str(exc))
                self._record_metric("failed", 0)
                await self._publish_progress_status(state, "failed")
                state = append_error(state, f"Unrecoverable: {exc}")
                if self.agent_name in _RECOVERABLE_AGENTS:
                    return await self._pause_for_recovery(state, str(exc))
                return update_state(state, status="failed", next_agent=None)

            except Exception as exc:
                self._log.exception("unexpected_error", thread_id=state.get("thread_id", "?"))
                self._record_metric("failed", 0)
                state = append_error(state, f"Unexpected: {type(exc).__name__}: {exc}")
                if self.agent_name in _RECOVERABLE_AGENTS:
                    return await self._pause_for_recovery(state, f"{type(exc).__name__}: {exc}")
                return update_state(state, status="failed", next_agent=None)

        # Exhausted all retries
        if self.agent_name in _RECOVERABLE_AGENTS:
            return await self._pause_for_recovery(state, "Exhausted all retries")
        return update_state(state, status="failed", next_agent=None)

    # ------------------------------------------------------------------
    # Pipeline progress helpers (best-effort, never block execution)
    # ------------------------------------------------------------------

    async def _publish_progress_start(self, state: AgentState) -> None:
        """Publish that this agent has started execution."""
        try:
            from src.core.pipeline_progress import get_progress_tracker  # noqa: PLC0415

            tracker = get_progress_tracker()
            if tracker:
                await tracker.publish_agent_started(self.agent_name, state)
        except Exception:
            self._log.debug("progress_start_publish_failed", exc_info=True)

    async def _publish_progress_complete(self, state: AgentState) -> None:
        """Publish that this agent has completed execution."""
        try:
            from src.core.pipeline_progress import get_progress_tracker  # noqa: PLC0415

            tracker = get_progress_tracker()
            if tracker:
                await tracker.publish_agent_completed(self.agent_name, state)
        except Exception:
            self._log.debug("progress_complete_publish_failed", exc_info=True)

    async def _publish_progress_status(self, state: AgentState, status: str) -> None:
        """Publish a status change (paused, failed)."""
        try:
            from src.core.pipeline_progress import get_progress_tracker  # noqa: PLC0415

            tracker = get_progress_tracker()
            if tracker:
                await tracker.publish_status(state["thread_id"], status, self.agent_name)
        except Exception:
            self._log.debug("progress_status_publish_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Partial failure recovery
    # ------------------------------------------------------------------

    async def _pause_for_recovery(
        self,
        state: AgentState,
        reason: str,
    ) -> AgentState:
        """Pause the workflow for HITL recovery instead of failing.

        Creates an ``HITLQueue`` entry with ``type="agent_failure"`` and
        returns the state with ``status="paused"``, ``requires_hitl=True``,
        and failure metadata for the operator.
        """
        import uuid as _uuid  # noqa: PLC0415

        from src.core.database import get_db_session as _get_db  # noqa: PLC0415
        from src.core.models import HITLQueue  # noqa: PLC0415

        hitl_id = _uuid.uuid4()
        project = state.get("project") or {}
        project_id = project.get("project_id", "unknown")

        self._log.warning(
            "agent_failure_paused_for_recovery",
            agent=self.agent_name,
            thread_id=state["thread_id"],
            reason=reason[:200],
            recovery_attempted=state.get("recovery_attempted", 0),
        )

        try:
            async with _get_db() as session:
                hitl = HITLQueue(
                    id=hitl_id,
                    type="agent_failure",
                    priority="urgent",
                    title=(f"Agent '{self.agent_name}' failed: {reason[:100]}"),
                    description=(
                        f"Execution agent '{self.agent_name}' failed in project "
                        f"'{project_id}'. Recovery options: resume (retry), "
                        f"skip (move to next agent), or manual (provide output)."
                    ),
                    payload={
                        "failed_agent": self.agent_name,
                        "failure_reason": reason[:500],
                        "project_id": project_id,
                        "thread_id": state["thread_id"],
                        "recovery_attempted": state.get("recovery_attempted", 0),
                        "current_sequence_index": state.get("current_sequence_index", 0),
                        "agent_sequence": state.get("agent_sequence", []),
                        "completed_artifacts": list((state.get("artifacts") or {}).keys()),
                    },
                    available_actions=["resume", "skip", "manual"],
                    status="pending",
                )
                session.add(hitl)
        except Exception:
            self._log.exception("hitl_entry_creation_failed", agent=self.agent_name)

        return update_state(
            state,
            status="paused",
            requires_hitl=True,
            hitl_request_id=str(hitl_id),
            failed_agent=self.agent_name,
            failure_reason=reason[:500],
            next_agent=None,
        )

    # ------------------------------------------------------------------
    # Sentry transaction helper
    # ------------------------------------------------------------------

    def _sentry_transaction(self, thread_id: str):
        """Return a Sentry transaction context manager for this agent run.

        If ``sentry_sdk`` is not installed, returns a no-op context manager
        so the caller never needs to guard imports.
        """
        try:
            from src.monitoring.sentry_config import start_agent_transaction  # noqa: PLC0415

            return start_agent_transaction(self.agent_name, thread_id)
        except Exception:  # noqa: BLE001
            from contextlib import nullcontext  # noqa: PLC0415

            return nullcontext()

    # ------------------------------------------------------------------
    # Metrics helper
    # ------------------------------------------------------------------

    def _record_metric(self, status: str, duration_seconds: float) -> None:
        """Record a Prometheus metric, logging on failure instead of crashing."""
        try:
            from src.monitoring.metrics import get_metrics  # noqa: PLC0415

            get_metrics().record_agent_run(
                self.agent_name,
                status=status,
                duration_seconds=duration_seconds,
            )
        except Exception:  # noqa: BLE001
            self._log.debug("metrics_recording_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Abstract -- subclasses implement this
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def _execute(self, state: AgentState) -> AgentState:
        """Perform agent-specific work.

        Must return a (possibly updated) ``AgentState``.  Should set
        ``next_agent`` to indicate which agent should run next, or ``None``
        to terminate.
        """
        ...

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: list[BaseMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> tuple[BaseMessage, CallMetrics]:
        """Delegate an LLM call, routing through the priority queue when available."""
        from src.core.container import get_container  # noqa: PLC0415

        queue = get_container().llm_queue
        if queue is not None:
            from src.core.llm_queue import AGENT_PRIORITY, LLMPriority  # noqa: PLC0415

            priority = AGENT_PRIORITY.get(self.agent_name, LLMPriority.NORMAL)
            return await queue.submit(
                priority=priority,
                agent_name=self.agent_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await self.llm_client.call(
            self.agent_name,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # Constraint validation
    # ------------------------------------------------------------------

    def _validate_role_constraints(self, state: AgentState) -> None:
        """Enforce role boundaries before execution.

        Raises:
            AgentException: When a constraint would be violated.
        """
        # Check that no forbidden tool is in the allowed set
        overlap = self._forbidden_tools & self.allowed_tools
        if overlap:
            raise AgentException(
                f"Agent '{self.agent_name}' has forbidden tools in its allowed set: {overlap}",
                agent_name=self.agent_name,
                thread_id=state.get("thread_id", ""),
            )

        # Check iteration budget
        retry_count = state.get("retry_count", 0)
        if retry_count > self._agent_max_iterations:
            raise AgentException(
                f"Agent '{self.agent_name}' exceeded max iterations ({self._agent_max_iterations})",
                agent_name=self.agent_name,
                thread_id=state.get("thread_id", ""),
            )

    def _build_system_prompt(self) -> str:
        """Construct a system prompt embedding role constraints.

        Subclasses can call this as a prefix and append domain-specific
        instructions.
        """
        rc = self.role_constraints
        role = rc.get("role", self.agent_name)
        goal = rc.get("goal", "")
        constraints = rc.get("constraints", [])
        forbidden = rc.get("forbidden_tools", [])

        lines = [
            f"You are a {role}.",
            "",
            f"GOAL: {goal}",
            "",
            "CRITICAL CONSTRAINTS (NEVER VIOLATE):",
        ]
        for c in constraints:
            lines.append(f"- {c}")
        lines.append("")
        lines.append("FORBIDDEN ACTIONS:")
        if forbidden:
            for f in forbidden:
                lines.append(f"- {f}")
        else:
            lines.append("- None")
        lines.append("")
        lines.append("If you are unsure about an action, STOP and request human clarification.")
        return "\n".join(lines)

    async def _load_user_credentials(self, user_id: str) -> None:
        """Load the user's OpenRouter API key from DB and update the LLM client.

        Called once per ``invoke()`` when the state contains a ``user_id``.
        Falls back gracefully to env vars if no DB key is stored.
        """
        try:
            from src.core.credential_loader import get_api_key  # noqa: PLC0415

            key = await get_api_key("openrouter_api_key", user_id=user_id)
            if key:
                self.llm_client.update_credentials(api_key=key)
        except Exception:
            self._log.debug(
                "credential_load_skipped",
                user_id=user_id,
                exc_info=True,
            )

    async def _get_credential(self, key_name: str, user_id: str | None = None) -> str | None:
        """Retrieve an API key at runtime, checking DB then environment.

        This is a convenience wrapper around
        :func:`src.core.credential_loader.get_api_key` so that any agent
        subclass can easily obtain credentials without importing the loader
        directly.

        Args:
            key_name: Lowercase key identifier, e.g. ``"gemini_api_key"``.
            user_id: Optional user UUID string.  When provided, user-stored
                keys in the database take precedence over environment defaults.

        Returns:
            The credential value, or ``None`` if not configured.
        """
        from src.core.credential_loader import get_api_key  # noqa: PLC0415

        return await get_api_key(key_name, user_id=user_id)

    def _assert_tool_allowed(self, tool_name: str) -> None:
        """Raise if *tool_name* is not in this agent's allowed set or is forbidden."""
        if tool_name in self._forbidden_tools:
            raise AgentException(
                f"Agent '{self.agent_name}' is forbidden from using tool '{tool_name}'",
                agent_name=self.agent_name,
                details={"tool": tool_name, "forbidden_tools": sorted(self._forbidden_tools)},
            )
        if tool_name not in self.allowed_tools:
            raise AgentException(
                f"Agent '{self.agent_name}' is not permitted to use tool '{tool_name}'",
                agent_name=self.agent_name,
                details={"tool": tool_name, "allowed": sorted(self.allowed_tools)},
            )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _describe_task(task: dict[str, Any] | None) -> str | None:
    """Extract a short description from a task dict for heartbeat reporting."""
    if task is None:
        return None
    return task.get("description") or task.get("name") or str(task)[:120]
