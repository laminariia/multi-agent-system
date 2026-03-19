"""Tests for src.core.sisyphus -- Sisyphus orchestration patterns.

Covers all four components:
1. HookRegistry -- register, fire, unregister, error handling, clear
2. TodoContinuationEnforcer -- check completion, enforce continue/done/escalate
3. BackgroundExecutor -- submit, semaphore limits, shutdown, task cleanup
4. TaskCategoryPresets -- preset lookup, frozen dataclass validation
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.sisyphus import (
    HOOK_EVENTS,
    TASK_PRESETS,
    BackgroundExecutor,
    HookRegistry,
    TaskPreset,
    TodoContinuationEnforcer,
    get_preset,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def registry() -> HookRegistry:
    """Fresh HookRegistry instance."""
    return HookRegistry()


@pytest.fixture()
def mock_db_session_factory():
    """Factory that yields a mock AsyncSession with configurable pending count."""

    def _factory(pending_count: int = 0):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = pending_count
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def _session_ctx():
            yield mock_session

        return _session_ctx, mock_session

    return _factory


@pytest.fixture()
def executor() -> BackgroundExecutor:
    """Fresh BackgroundExecutor instance."""
    return BackgroundExecutor()


# ============================================================================
# 1. HookRegistry
# ============================================================================


class TestHookRegistry:
    """Tests for HookRegistry."""

    def test_initial_state_has_all_events(self, registry: HookRegistry) -> None:
        """Registry starts with all canonical event types and zero hooks."""
        counts = registry.registered_count
        assert set(counts.keys()) == HOOK_EVENTS
        for count in counts.values():
            assert count == 0

    def test_register_sync_callback(self, registry: HookRegistry) -> None:
        """Sync callback can be registered."""

        def my_hook(**kwargs: Any) -> None:
            pass

        registry.register("agent_start", my_hook)
        assert registry.registered_count["agent_start"] == 1

    def test_register_async_callback(self, registry: HookRegistry) -> None:
        """Async callback can be registered."""

        async def my_async_hook(**kwargs: Any) -> None:
            pass

        registry.register("tool_pre", my_async_hook)
        assert registry.registered_count["tool_pre"] == 1

    def test_register_invalid_event_raises(self, registry: HookRegistry) -> None:
        """Registering for an unknown event raises ValueError."""
        with pytest.raises(ValueError, match="Unknown hook event"):
            registry.register("nonexistent_event", lambda: None)

    def test_register_duplicate_is_noop(self, registry: HookRegistry) -> None:
        """Registering the same callback twice does not add a duplicate."""

        def hook(**kwargs: Any) -> None:
            pass

        registry.register("agent_stop", hook)
        registry.register("agent_stop", hook)
        assert registry.registered_count["agent_stop"] == 1

    def test_unregister_removes_callback(self, registry: HookRegistry) -> None:
        """Unregistering a callback removes it from the list."""

        def hook(**kwargs: Any) -> None:
            pass

        registry.register("agent_error", hook)
        assert registry.registered_count["agent_error"] == 1
        registry.unregister("agent_error", hook)
        assert registry.registered_count["agent_error"] == 0

    def test_unregister_missing_callback_is_noop(self, registry: HookRegistry) -> None:
        """Unregistering a callback that was never registered is a no-op."""

        def hook(**kwargs: Any) -> None:
            pass

        # Should not raise
        registry.unregister("agent_start", hook)

    def test_unregister_invalid_event_raises(self, registry: HookRegistry) -> None:
        """Unregistering from an unknown event raises ValueError."""
        with pytest.raises(ValueError, match="Unknown hook event"):
            registry.unregister("bad_event", lambda: None)

    @pytest.mark.anyio()
    async def test_fire_sync_callback(self, registry: HookRegistry) -> None:
        """Sync callbacks are called with kwargs when event fires."""
        received: list[dict[str, Any]] = []

        def hook(**kwargs: Any) -> None:
            received.append(kwargs)

        registry.register("agent_start", hook)
        await registry.fire("agent_start", agent_name="scout", task="search")

        assert len(received) == 1
        assert received[0] == {"agent_name": "scout", "task": "search"}

    @pytest.mark.anyio()
    async def test_fire_async_callback(self, registry: HookRegistry) -> None:
        """Async callbacks are awaited when event fires."""
        received: list[dict[str, Any]] = []

        async def async_hook(**kwargs: Any) -> None:
            received.append(kwargs)

        registry.register("tool_post", async_hook)
        await registry.fire("tool_post", tool="search", result="ok")

        assert len(received) == 1
        assert received[0] == {"tool": "search", "result": "ok"}

    @pytest.mark.anyio()
    async def test_fire_multiple_callbacks_in_order(self, registry: HookRegistry) -> None:
        """Multiple callbacks fire in registration order."""
        order: list[str] = []

        def hook_a(**kwargs: Any) -> None:
            order.append("a")

        async def hook_b(**kwargs: Any) -> None:
            order.append("b")

        def hook_c(**kwargs: Any) -> None:
            order.append("c")

        registry.register("agent_stop", hook_a)
        registry.register("agent_stop", hook_b)
        registry.register("agent_stop", hook_c)

        await registry.fire("agent_stop")
        assert order == ["a", "b", "c"]

    @pytest.mark.anyio()
    async def test_fire_error_in_callback_is_swallowed(self, registry: HookRegistry) -> None:
        """Errors in callbacks are logged but do not propagate."""
        called: list[str] = []

        def good_hook(**kwargs: Any) -> None:
            called.append("good")

        def bad_hook(**kwargs: Any) -> None:
            raise RuntimeError("hook failed")

        registry.register("agent_error", bad_hook)
        registry.register("agent_error", good_hook)

        # Should not raise
        await registry.fire("agent_error")
        # The good hook still ran despite the bad hook crashing
        assert "good" in called

    @pytest.mark.anyio()
    async def test_fire_invalid_event_raises(self, registry: HookRegistry) -> None:
        """Firing an unknown event raises ValueError."""
        with pytest.raises(ValueError, match="Unknown hook event"):
            await registry.fire("nonexistent")

    @pytest.mark.anyio()
    async def test_fire_no_callbacks_is_noop(self, registry: HookRegistry) -> None:
        """Firing an event with zero callbacks does nothing (no error)."""
        await registry.fire("tool_pre")  # should not raise

    def test_clear_single_event(self, registry: HookRegistry) -> None:
        """Clearing a specific event removes only its hooks."""

        def hook1(**kwargs: Any) -> None:
            pass

        def hook2(**kwargs: Any) -> None:
            pass

        registry.register("agent_start", hook1)
        registry.register("agent_stop", hook2)

        registry.clear("agent_start")
        assert registry.registered_count["agent_start"] == 0
        assert registry.registered_count["agent_stop"] == 1

    def test_clear_all_events(self, registry: HookRegistry) -> None:
        """Clearing with event=None removes all hooks."""

        def hook(**kwargs: Any) -> None:
            pass

        for event in HOOK_EVENTS:
            registry.register(event, hook)

        registry.clear()
        for count in registry.registered_count.values():
            assert count == 0

    def test_clear_invalid_event_raises(self, registry: HookRegistry) -> None:
        """Clearing an unknown event raises ValueError."""
        with pytest.raises(ValueError, match="Unknown hook event"):
            registry.clear("bad_event")


# ============================================================================
# 2. TodoContinuationEnforcer
# ============================================================================


class TestTodoContinuationEnforcer:
    """Tests for TodoContinuationEnforcer."""

    @pytest.mark.anyio()
    async def test_check_completion_no_pending(self, mock_db_session_factory: Any) -> None:
        """Returns True when zero pending goals exist."""
        factory, _session = mock_db_session_factory(pending_count=0)
        enforcer = TodoContinuationEnforcer(db_session_factory=factory)
        assert await enforcer.check_completion() is True

    @pytest.mark.anyio()
    async def test_check_completion_with_pending(self, mock_db_session_factory: Any) -> None:
        """Returns False when pending goals exist."""
        factory, _session = mock_db_session_factory(pending_count=3)
        enforcer = TodoContinuationEnforcer(db_session_factory=factory)
        assert await enforcer.check_completion() is False

    @pytest.mark.anyio()
    async def test_check_completion_db_failure_returns_false(self) -> None:
        """On DB error, returns False (fail-open: assume goals remain)."""

        @asynccontextmanager
        async def _bad_session():
            raise ConnectionError("DB down")
            yield  # pragma: no cover  # noqa: E501

        enforcer = TodoContinuationEnforcer(db_session_factory=_bad_session)
        assert await enforcer.check_completion() is False

    @pytest.mark.anyio()
    async def test_enforce_returns_done(self, mock_db_session_factory: Any) -> None:
        """Returns 'done' when all goals are complete."""
        factory, _session = mock_db_session_factory(pending_count=0)
        enforcer = TodoContinuationEnforcer(db_session_factory=factory)
        result = await enforcer.enforce()
        assert result == "done"

    @pytest.mark.anyio()
    async def test_enforce_returns_continue(self, mock_db_session_factory: Any) -> None:
        """Returns 'continue' when goals remain and retries not exhausted."""
        factory, _session = mock_db_session_factory(pending_count=2)
        enforcer = TodoContinuationEnforcer(db_session_factory=factory)
        result = await enforcer.enforce()
        assert result == "continue"
        assert enforcer.retry_count == 1

    @pytest.mark.anyio()
    async def test_enforce_increments_retry(self, mock_db_session_factory: Any) -> None:
        """Each enforce() call increments the retry counter."""
        factory, _session = mock_db_session_factory(pending_count=5)
        enforcer = TodoContinuationEnforcer(max_retries=5, db_session_factory=factory)

        for i in range(1, 4):
            await enforcer.enforce()
            assert enforcer.retry_count == i

    @pytest.mark.anyio()
    async def test_enforce_escalates_at_max_retries(self, mock_db_session_factory: Any) -> None:
        """Returns 'escalate' when max retries reached."""
        factory, session = mock_db_session_factory(pending_count=1)
        enforcer = TodoContinuationEnforcer(max_retries=2, db_session_factory=factory)

        # First call: continue
        result1 = await enforcer.enforce()
        assert result1 == "continue"

        # Second call: escalate (retry_count == max_retries)
        result2 = await enforcer.enforce()
        assert result2 == "escalate"
        assert enforcer.retry_count == 2

    @pytest.mark.anyio()
    async def test_escalation_creates_hitl(self, mock_db_session_factory: Any) -> None:
        """Escalation creates a HITL alert entry via session.add()."""
        factory, session = mock_db_session_factory(pending_count=3)
        enforcer = TodoContinuationEnforcer(max_retries=1, db_session_factory=factory)

        result = await enforcer.enforce()
        assert result == "escalate"

        # Verify session.add was called (HITL creation)
        assert session.add.called

    @pytest.mark.anyio()
    async def test_reset_clears_retry_count(self, mock_db_session_factory: Any) -> None:
        """reset() sets the retry counter back to zero."""
        factory, _session = mock_db_session_factory(pending_count=1)
        enforcer = TodoContinuationEnforcer(db_session_factory=factory)

        await enforcer.enforce()
        assert enforcer.retry_count == 1

        enforcer.reset()
        assert enforcer.retry_count == 0

    @pytest.mark.anyio()
    async def test_custom_max_retries(self, mock_db_session_factory: Any) -> None:
        """Custom max_retries value is respected."""
        factory, _session = mock_db_session_factory(pending_count=1)
        enforcer = TodoContinuationEnforcer(max_retries=5, db_session_factory=factory)

        for _ in range(4):
            result = await enforcer.enforce()
            assert result == "continue"

        result = await enforcer.enforce()
        assert result == "escalate"

    @pytest.mark.anyio()
    async def test_escalation_failure_does_not_crash(self) -> None:
        """If HITL creation fails, enforce() still returns 'escalate'."""
        call_count = 0

        @asynccontextmanager
        async def _flaky_session():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                # First call (check_completion) -- return pending=1
                mock_session = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalar_one.return_value = 1
                mock_session.execute = AsyncMock(return_value=mock_result)
                yield mock_session
            else:
                # Second call (_create_escalation) -- raise
                raise ConnectionError("DB down during escalation")
                yield  # pragma: no cover  # noqa: E501

        enforcer = TodoContinuationEnforcer(max_retries=1, db_session_factory=_flaky_session)
        result = await enforcer.enforce()
        assert result == "escalate"


# ============================================================================
# 3. BackgroundExecutor
# ============================================================================


class TestBackgroundExecutor:
    """Tests for BackgroundExecutor."""

    @pytest.mark.anyio()
    async def test_submit_and_await(self, executor: BackgroundExecutor) -> None:
        """A submitted coroutine runs and returns its result."""

        async def work() -> str:
            return "done"

        task = await executor.submit(work())
        result = await task
        assert result == "done"

    @pytest.mark.anyio()
    async def test_active_count(self, executor: BackgroundExecutor) -> None:
        """active_count reflects running tasks."""
        event = asyncio.Event()

        async def wait_for_signal() -> None:
            await event.wait()

        task = await executor.submit(wait_for_signal())
        # Give the task a moment to start
        await asyncio.sleep(0.01)
        assert executor.active_count >= 1

        event.set()
        await task
        # After completion, done callback removes from set
        await asyncio.sleep(0.01)
        assert executor.active_count == 0

    @pytest.mark.anyio()
    async def test_task_name(self, executor: BackgroundExecutor) -> None:
        """Task receives the specified name."""

        async def work() -> None:
            pass

        task = await executor.submit(work(), task_name="my-task")
        assert task.get_name() == "my-task"
        await task

    @pytest.mark.anyio()
    async def test_provider_semaphore_limits(self) -> None:
        """Concurrency is limited per provider."""
        executor = BackgroundExecutor(provider_limits={"test_provider": 2})
        started: list[int] = []
        gate = asyncio.Event()

        async def tracked_work(idx: int) -> int:
            started.append(idx)
            await gate.wait()
            return idx

        # Submit 3 tasks with limit=2
        t1 = await executor.submit(tracked_work(1), provider="test_provider")
        t2 = await executor.submit(tracked_work(2), provider="test_provider")
        t3 = await executor.submit(tracked_work(3), provider="test_provider")

        # Allow tasks to start
        await asyncio.sleep(0.05)

        # Only 2 should have started (semaphore=2)
        assert len(started) == 2

        # Release all
        gate.set()
        await asyncio.gather(t1, t2, t3)
        assert len(started) == 3

    @pytest.mark.anyio()
    async def test_default_provider_fallback(self, executor: BackgroundExecutor) -> None:
        """Unknown provider falls back to openrouter semaphore."""

        async def work() -> str:
            return "ok"

        task = await executor.submit(work(), provider="unknown_provider")
        result = await task
        assert result == "ok"

    @pytest.mark.anyio()
    async def test_task_error_logged_not_raised(self, executor: BackgroundExecutor) -> None:
        """Task exceptions are captured, not propagated to executor."""

        async def failing() -> None:
            raise ValueError("task error")

        task = await executor.submit(failing())
        with pytest.raises(ValueError, match="task error"):
            await task

        # Executor itself is fine
        await asyncio.sleep(0.01)
        assert executor.active_count == 0

    @pytest.mark.anyio()
    async def test_shutdown_waits_for_tasks(self, executor: BackgroundExecutor) -> None:
        """shutdown() waits for running tasks to complete."""
        completed = []

        async def work(idx: int) -> None:
            await asyncio.sleep(0.02)
            completed.append(idx)

        await executor.submit(work(1))
        await executor.submit(work(2))

        errors = await executor.shutdown(timeout=5.0)
        assert len(errors) == 0
        assert sorted(completed) == [1, 2]

    @pytest.mark.anyio()
    async def test_shutdown_cancels_stragglers(self, executor: BackgroundExecutor) -> None:
        """shutdown() cancels tasks that exceed timeout."""

        async def slow_work() -> None:
            await asyncio.sleep(100)

        await executor.submit(slow_work(), task_name="slow")
        await executor.shutdown(timeout=0.05)
        assert executor.active_count == 0

    @pytest.mark.anyio()
    async def test_shutdown_empty_is_noop(self, executor: BackgroundExecutor) -> None:
        """shutdown() with no tasks returns empty list."""
        errors = await executor.shutdown()
        assert errors == []

    @pytest.mark.anyio()
    async def test_shutdown_collects_errors(self, executor: BackgroundExecutor) -> None:
        """shutdown() returns exceptions from failed tasks."""
        gate = asyncio.Event()

        async def fail_after_gate() -> None:
            await gate.wait()
            raise RuntimeError("boom")

        await executor.submit(fail_after_gate())
        # Task is blocked on gate, still in self._tasks
        assert executor.active_count == 1

        # Release the gate so the task runs and fails during shutdown
        gate.set()
        errors = await executor.shutdown(timeout=2.0)
        assert len(errors) == 1
        assert "boom" in str(errors[0])

    @pytest.mark.anyio()
    async def test_provider_semaphores_property(self) -> None:
        """provider_semaphores returns available slots."""
        executor = BackgroundExecutor(provider_limits={"test": 5})
        sems = executor.provider_semaphores
        assert sems["test"] == 5

    @pytest.mark.anyio()
    async def test_custom_provider_limits(self) -> None:
        """Custom provider limits override defaults."""
        custom = {"custom_llm": 7}
        executor = BackgroundExecutor(provider_limits=custom)
        assert "custom_llm" in executor.provider_semaphores
        assert executor.provider_semaphores["custom_llm"] == 7
        # Default providers should not be present
        assert "anthropic" not in executor.provider_semaphores


# ============================================================================
# 4. TaskCategoryPresets
# ============================================================================


class TestTaskCategoryPresets:
    """Tests for TaskPreset and TASK_PRESETS."""

    def test_all_expected_presets_exist(self) -> None:
        """All six expected task presets are defined."""
        expected = {"freelance-bid", "code-gen", "design-ui", "outreach", "analysis", "packaging"}
        assert set(TASK_PRESETS.keys()) == expected

    def test_preset_is_frozen(self) -> None:
        """TaskPreset instances are immutable (frozen dataclass)."""
        preset = TASK_PRESETS["code-gen"]
        with pytest.raises(AttributeError):
            preset.name = "modified"  # type: ignore[misc]

    def test_get_preset_found(self) -> None:
        """get_preset returns the correct preset."""
        preset = get_preset("freelance-bid")
        assert preset is not None
        assert preset.name == "freelance-bid"
        assert preset.default_model_tier == 2
        assert preset.max_duration_minutes == 5
        assert preset.retry_on_failure is True
        assert preset.requires_hitl is True

    def test_get_preset_not_found(self) -> None:
        """get_preset returns None for unknown category."""
        assert get_preset("nonexistent") is None

    def test_code_gen_preset_values(self) -> None:
        """code-gen preset has expected values."""
        p = TASK_PRESETS["code-gen"]
        assert p.default_model_tier == 1
        assert p.max_duration_minutes == 30
        assert p.retry_on_failure is True
        assert p.requires_hitl is False

    def test_design_ui_preset_values(self) -> None:
        """design-ui preset has expected values."""
        p = TASK_PRESETS["design-ui"]
        assert p.default_model_tier == 4
        assert p.max_duration_minutes == 15
        assert p.retry_on_failure is False
        assert p.requires_hitl is True

    def test_analysis_preset_values(self) -> None:
        """analysis preset has expected values."""
        p = TASK_PRESETS["analysis"]
        assert p.default_model_tier == 5
        assert p.max_duration_minutes == 5
        assert p.retry_on_failure is False
        assert p.requires_hitl is False

    def test_outreach_preset_values(self) -> None:
        """outreach preset has expected values."""
        p = TASK_PRESETS["outreach"]
        assert p.default_model_tier == 2
        assert p.max_duration_minutes == 10

    def test_packaging_preset_values(self) -> None:
        """packaging preset has expected values."""
        p = TASK_PRESETS["packaging"]
        assert p.default_model_tier == 6
        assert p.requires_hitl is True

    def test_preset_equality(self) -> None:
        """Two presets with same values are equal (dataclass __eq__)."""
        a = TaskPreset("test", 1, 10, True, False)
        b = TaskPreset("test", 1, 10, True, False)
        assert a == b

    def test_preset_inequality(self) -> None:
        """Two presets with different values are not equal."""
        a = TaskPreset("test", 1, 10, True, False)
        b = TaskPreset("test", 2, 10, True, False)
        assert a != b
