"""Sisyphus orchestration patterns for autonomous agent lifecycle management.

Adapted from Oh My OpenCode Sisyphus patterns for MAS. Provides four components:

1. **HookRegistry** -- lifecycle hooks fired on agent events (start, stop, error,
   tool pre/post).
2. **TodoContinuationEnforcer** -- prevents the runner from quitting until all
   goals are completed or max retries reached, escalating to HITL on exhaustion.
3. **BackgroundExecutor** -- parallel agent execution with per-provider
   concurrency semaphores and graceful shutdown.
4. **TaskCategoryPresets** -- frozen dataclass presets mapping task types to
   default LLM tier, duration limits, retry policy, and HITL requirements.

Spec: docs/Full_work/specs/orchestrator-spec.md  SS Sisyphus
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select

from src.core.database import get_db_session
from src.core.models import HITLQueue, OrchestratorGoal

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. HookRegistry
# ---------------------------------------------------------------------------

# Canonical event types that the registry supports.
HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "agent_start",
        "agent_stop",
        "agent_error",
        "tool_pre",
        "tool_post",
    }
)


class HookRegistry:
    """Registry for lifecycle hooks that fire on agent events.

    Hooks are async or sync callables registered for a specific event type.
    Errors inside hooks are logged but **never** propagated to the caller,
    ensuring that a misbehaving hook cannot crash the orchestrator.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., Any]]] = {event: [] for event in HOOK_EVENTS}

    # -- registration -------------------------------------------------------

    def register(self, event: str, callback: Callable[..., Any]) -> None:
        """Register a callback for an event type.

        Args:
            event: One of the supported event names (see ``HOOK_EVENTS``).
            callback: Sync or async callable to invoke when the event fires.

        Raises:
            ValueError: If *event* is not a recognised event type.
        """
        if event not in self._hooks:
            raise ValueError(f"Unknown hook event '{event}'. Valid events: {sorted(HOOK_EVENTS)}")
        if callback in self._hooks[event]:
            logger.debug("hook_already_registered", hook_event=event, hook_callback=str(callback))
            return
        self._hooks[event].append(callback)
        logger.debug("hook_registered", hook_event=event, hook_callback=str(callback))

    def unregister(self, event: str, callback: Callable[..., Any]) -> None:
        """Remove a previously registered callback.

        Silently ignores the request if *callback* is not registered.

        Args:
            event: Event type the callback was registered for.
            callback: The callback to remove.

        Raises:
            ValueError: If *event* is not a recognised event type.
        """
        if event not in self._hooks:
            raise ValueError(f"Unknown hook event '{event}'. Valid events: {sorted(HOOK_EVENTS)}")
        try:
            self._hooks[event].remove(callback)
            logger.debug("hook_unregistered", hook_event=event, hook_callback=str(callback))
        except ValueError:
            pass  # not registered -- no-op

    async def fire(self, event: str, **kwargs: Any) -> None:
        """Fire all callbacks for an event.

        Each callback receives ``**kwargs`` forwarded from the caller.  Sync
        callbacks are called directly; async callbacks are awaited.  Any
        exception inside a callback is caught and logged -- it is **never**
        raised to the caller.

        Args:
            event: Event type to fire.
            **kwargs: Arbitrary context forwarded to every callback.

        Raises:
            ValueError: If *event* is not a recognised event type.
        """
        if event not in self._hooks:
            raise ValueError(f"Unknown hook event '{event}'. Valid events: {sorted(HOOK_EVENTS)}")

        for callback in self._hooks[event]:
            try:
                result = callback(**kwargs)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "hook_callback_error",
                    hook_event=event,
                    hook_callback=str(callback),
                )

    def clear(self, event: str | None = None) -> None:
        """Remove hooks.

        Args:
            event: If provided, clear only hooks for that event.
                   If ``None``, clear **all** hooks.

        Raises:
            ValueError: If *event* is provided but not recognised.
        """
        if event is not None:
            if event not in self._hooks:
                raise ValueError(f"Unknown hook event '{event}'. Valid events: {sorted(HOOK_EVENTS)}")
            self._hooks[event].clear()
            logger.debug("hooks_cleared", hook_event=event)
        else:
            for hooks_list in self._hooks.values():
                hooks_list.clear()
            logger.debug("all_hooks_cleared")

    @property
    def registered_count(self) -> dict[str, int]:
        """Return the number of registered callbacks per event."""
        return {event: len(cbs) for event, cbs in self._hooks.items()}


# ---------------------------------------------------------------------------
# 2. TodoContinuationEnforcer
# ---------------------------------------------------------------------------


class TodoContinuationEnforcer:
    """Prevents the runner from quitting until goals are done or max retries.

    The core Sisyphus principle: the agent cannot "give up".  On each
    ``enforce()`` call the enforcer checks for pending orchestrator goals.
    If goals remain, it increments a retry counter and returns ``"continue"``.
    Once retries are exhausted it creates a HITL alert and returns
    ``"escalate"``.  If all goals are complete it returns ``"done"``.

    Args:
        max_retries: Maximum continuation attempts before escalation.
        db_session_factory: Optional async context-manager that yields an
            ``AsyncSession``.  Defaults to ``get_db_session``.
    """

    MAX_RETRIES: int = 3

    def __init__(
        self,
        max_retries: int | None = None,
        db_session_factory: Any = None,
    ) -> None:
        if max_retries is not None:
            self.MAX_RETRIES = max_retries
        self._retry_count: int = 0
        self._db_session_factory = db_session_factory or get_db_session

    @property
    def retry_count(self) -> int:
        """Current retry count."""
        return self._retry_count

    def reset(self) -> None:
        """Reset the retry counter (e.g. after a new session starts)."""
        self._retry_count = 0
        logger.debug("continuation_enforcer_reset")

    async def check_completion(self) -> bool:
        """Check if all orchestrator goals are completed.

        Returns:
            ``True`` if zero pending goals remain, ``False`` otherwise.
        """
        try:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "pending"),
                )
                pending = result.scalar_one()
                logger.debug("continuation_check", pending_goals=pending)
                return pending == 0
        except Exception:
            logger.exception("continuation_check_failed")
            # Fail-open: if we cannot reach the DB, assume goals remain
            # so the runner keeps going rather than stopping prematurely.
            return False

    async def enforce(self) -> str:
        """Enforce continuation or escalate.

        Returns:
            ``"done"`` -- all goals complete, runner may stop.
            ``"continue"`` -- goals remain, keep running.
            ``"escalate"`` -- max retries reached, HITL alert created.
        """
        if await self.check_completion():
            logger.info("continuation_enforcer_done", retries_used=self._retry_count)
            return "done"

        self._retry_count += 1
        logger.info(
            "continuation_enforcer_retry",
            retry=self._retry_count,
            max_retries=self.MAX_RETRIES,
        )

        if self._retry_count >= self.MAX_RETRIES:
            await self._create_escalation()
            return "escalate"

        return "continue"

    async def _create_escalation(self) -> str | None:
        """Create a HITL alert for unfinished goals after max retries.

        Returns:
            HITL entry ID as string, or ``None`` on failure.
        """
        try:
            async with self._db_session_factory() as session:
                # Count remaining pending goals for the alert description
                result = await session.execute(
                    select(func.count()).select_from(OrchestratorGoal).where(OrchestratorGoal.status == "pending"),
                )
                pending_count = result.scalar_one()

                hitl_id = uuid.uuid4()
                hitl = HITLQueue(
                    id=hitl_id,
                    type="alert",
                    priority="high",
                    title=(f"Sisyphus enforcer: {pending_count} goals remain after {self._retry_count} retries"),
                    description=(
                        f"The autonomous runner has attempted to complete pending "
                        f"goals {self._retry_count} times without success.  "
                        f"{pending_count} goal(s) still have status 'pending'.  "
                        f"Manual intervention is required to unblock the runner."
                    ),
                    payload={
                        "pending_goals": pending_count,
                        "retry_count": self._retry_count,
                        "max_retries": self.MAX_RETRIES,
                        "detected_at": datetime.now(UTC).isoformat(),
                        "escalation_source": "sisyphus_continuation_enforcer",
                    },
                    available_actions=["acknowledge", "retry", "skip_goals"],
                    status="pending",
                )
                session.add(hitl)

            logger.warning(
                "sisyphus_escalation_created",
                hitl_id=str(hitl_id),
                pending_goals=pending_count,
                retries=self._retry_count,
            )
            return str(hitl_id)

        except Exception:
            logger.exception("sisyphus_escalation_failed")
            return None


# ---------------------------------------------------------------------------
# 3. BackgroundExecutor
# ---------------------------------------------------------------------------

# GC-safe task references (prevent fire-and-forget tasks from being collected)
_background_tasks: set[asyncio.Task[Any]] = set()


class BackgroundExecutor:
    """Execute multiple agents in parallel with provider concurrency limits.

    Each LLM provider has a concurrency cap enforced via ``asyncio.Semaphore``.
    Tasks are tracked in a set to prevent garbage collection of running
    coroutines.

    Args:
        provider_limits: Optional override mapping provider name to max
            concurrency.  Falls back to class-level ``PROVIDER_LIMITS``.
    """

    PROVIDER_LIMITS: dict[str, int] = {
        "anthropic": 5,
        "google": 3,
        "openrouter": 10,
        "deepseek": 3,
    }

    def __init__(
        self,
        provider_limits: dict[str, int] | None = None,
    ) -> None:
        limits = provider_limits or self.PROVIDER_LIMITS
        self._semaphores: dict[str, asyncio.Semaphore] = {
            provider: asyncio.Semaphore(limit) for provider, limit in limits.items()
        }
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def active_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._tasks)

    @property
    def provider_semaphores(self) -> dict[str, int]:
        """Current semaphore values per provider (available slots)."""
        return {provider: sem._value for provider, sem in self._semaphores.items()}

    async def submit(
        self,
        coro: Any,
        provider: str = "openrouter",
        *,
        task_name: str | None = None,
    ) -> asyncio.Task[Any]:
        """Submit a coroutine for background execution with provider limit.

        Args:
            coro: Awaitable coroutine to execute.
            provider: LLM provider name for concurrency limiting.
            task_name: Optional descriptive name for the asyncio task.

        Returns:
            The created ``asyncio.Task``.
        """
        sem = self._semaphores.get(provider, self._semaphores.get("openrouter"))
        if sem is None:
            # No semaphore configured at all -- create a default one
            sem = asyncio.Semaphore(10)
            self._semaphores[provider] = sem

        async def _wrapped() -> Any:
            async with sem:
                return await coro

        name = task_name or f"bg-{provider}-{len(self._tasks)}"
        task = asyncio.create_task(_wrapped(), name=name)
        self._tasks.add(task)
        _background_tasks.add(task)
        task.add_done_callback(self._task_done)

        logger.debug(
            "background_task_submitted",
            provider=provider,
            task_name=name,
            active_tasks=len(self._tasks),
        )
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        """Callback to clean up completed tasks."""
        self._tasks.discard(task)
        _background_tasks.discard(task)

        if task.cancelled():
            logger.debug("background_task_cancelled", task_name=task.get_name())
        elif task.exception() is not None:
            logger.error(
                "background_task_failed",
                task_name=task.get_name(),
                error=str(task.exception()),
            )
        else:
            logger.debug("background_task_completed", task_name=task.get_name())

    async def shutdown(self, timeout: float = 30.0) -> list[BaseException]:
        """Wait for all tasks to complete or timeout.

        Tasks that do not finish within *timeout* seconds are cancelled.

        Args:
            timeout: Maximum seconds to wait before cancelling stragglers.

        Returns:
            List of exceptions from failed tasks (empty on clean shutdown).
        """
        if not self._tasks:
            return []

        pending = list(self._tasks)
        logger.info("background_executor_shutdown", pending_tasks=len(pending), timeout=timeout)

        done, not_done = await asyncio.wait(pending, timeout=timeout)

        # Cancel tasks that did not finish in time
        errors: list[BaseException] = []
        for task in not_done:
            task.cancel()
            logger.warning("background_task_force_cancelled", task_name=task.get_name())

        # Collect exceptions from completed tasks
        for task in done:
            if task.exception() is not None:
                errors.append(task.exception())  # type: ignore[arg-type]

        # Wait briefly for cancellations to propagate
        if not_done:
            await asyncio.wait(not_done, timeout=2.0)

        self._tasks.clear()
        logger.info(
            "background_executor_shutdown_complete",
            completed=len(done),
            cancelled=len(not_done),
            errors=len(errors),
        )
        return errors


# ---------------------------------------------------------------------------
# 4. TaskCategoryPresets
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskPreset:
    """Predefined configuration for a category of orchestrator tasks.

    Attributes:
        name: Human-readable preset name.
        default_model_tier: LLM tier (1-6) from the 6-tier system.
        max_duration_minutes: Maximum wall-clock time for this task type.
        retry_on_failure: Whether the task should be retried on failure.
        requires_hitl: Whether HITL approval is required before execution.
    """

    name: str
    default_model_tier: int
    max_duration_minutes: int
    retry_on_failure: bool
    requires_hitl: bool


TASK_PRESETS: dict[str, TaskPreset] = {
    "freelance-bid": TaskPreset(
        name="freelance-bid",
        default_model_tier=2,
        max_duration_minutes=5,
        retry_on_failure=True,
        requires_hitl=True,
    ),
    "code-gen": TaskPreset(
        name="code-gen",
        default_model_tier=1,
        max_duration_minutes=30,
        retry_on_failure=True,
        requires_hitl=False,
    ),
    "design-ui": TaskPreset(
        name="design-ui",
        default_model_tier=4,
        max_duration_minutes=15,
        retry_on_failure=False,
        requires_hitl=True,
    ),
    "outreach": TaskPreset(
        name="outreach",
        default_model_tier=2,
        max_duration_minutes=10,
        retry_on_failure=True,
        requires_hitl=True,
    ),
    "analysis": TaskPreset(
        name="analysis",
        default_model_tier=5,
        max_duration_minutes=5,
        retry_on_failure=False,
        requires_hitl=False,
    ),
    "packaging": TaskPreset(
        name="packaging",
        default_model_tier=6,
        max_duration_minutes=10,
        retry_on_failure=False,
        requires_hitl=True,
    ),
}


def get_preset(category: str) -> TaskPreset | None:
    """Look up a task preset by category name.

    Args:
        category: Preset key (e.g. ``"freelance-bid"``, ``"code-gen"``).

    Returns:
        The matching ``TaskPreset`` or ``None`` if not found.
    """
    return TASK_PRESETS.get(category)
