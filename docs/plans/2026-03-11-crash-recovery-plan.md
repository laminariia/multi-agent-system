# Crash Recovery — Implementation Plan

**Task**: Phase 2, Task #9 from gap-analysis.md
**Spec**: `docs/Full_work/specs/infrastructure-spec.md` §3 (lines 427-763)
**Date**: 2026-03-11

## Problem

Agents can crash mid-execution (LLM timeout, OOM, deploy, DB loss, Valkey restart).
Currently there's no recovery — stale threads just stay "active" forever.

## Design

### Module: `src/core/crash_recovery.py`

**Constants:**
- `MAX_CRASH_RETRIES = 3`
- `STALE_THRESHOLD_MINUTES = 5`

**Functions:**

1. `find_stale_threads(session) -> list[StaleThread]`
   - Query `LanggraphCheckpoint` for latest checkpoint per thread_id
   - WHERE `status = 'active'` AND `created_at < now() - 5 min`
   - Exclude `requires_hitl = True` (HITL pending — do not auto-resume)
   - Returns list of `StaleThread(thread_id, status, current_agent, state_data, created_at)`

2. `resume_from_crash(thread_id, session, checkpointer) -> CrashRecoveryResult`
   - Load latest checkpoint for thread_id
   - If no checkpoint → return `CrashRecoveryResult(action="no_checkpoint")`
   - If `requires_hitl` → return `CrashRecoveryResult(action="wait_for_hitl")`
   - If `status == "failed"` and `_crash_retry_count >= MAX_CRASH_RETRIES` → create HITL alert, return "escalated"
   - If `status == "failed"` → reset status to "active", increment retry count
   - If `status == "active"` → increment retry count
   - Build graph, invoke from `_determine_entry_node(state)`
   - Return final status

3. `_determine_entry_node(state) -> str`
   - If `current_agent` → `"{current_agent}_node"`
   - elif `next_agent` → `"{next_agent}_node"`
   - else → `"scout_node"`

4. `create_crash_hitl(thread_id, state, session) -> None`
   - Create HITLQueue entry with `action_type="crash_recovery"`, priority="urgent"
   - `available_actions=["retry", "skip_agent", "abort_pipeline"]`

5. `startup_crash_recovery(session) -> CrashRecoverySummary`
   - Called from lifespan on startup
   - Calls `find_stale_threads()`
   - For each: logs and calls `resume_from_crash()` in background task
   - Returns summary: `{found, resumed, skipped, escalated}`

### Data Classes

```python
@dataclass
class StaleThread:
    thread_id: str
    status: str
    current_agent: str | None
    state_data: dict[str, Any]
    created_at: datetime

@dataclass
class CrashRecoveryResult:
    action: str  # "resumed", "wait_for_hitl", "escalated", "no_checkpoint"
    thread_id: str
    final_status: str | None = None
    error: str | None = None
```

### Integration

In `src/api/main.py` lifespan, after DB/Valkey verified and before `yield`:
```python
# Crash recovery (best-effort, non-blocking)
try:
    from src.core.crash_recovery import startup_crash_recovery
    summary = await startup_crash_recovery(session)
    logger.info("app.crash_recovery_complete", **summary)
except Exception as exc:
    logger.warning("app.crash_recovery_failed", error=str(exc))
```

### Edge Cases

- No stale threads → summary = {found: 0}, no action
- All stale threads are HITL pending → all skipped
- Checkpoint has no state_data → skip with warning
- Resume raises exception → catch, create HITL alert, continue to next thread
- Concurrent startup (multiple workers) → idempotent via status check before resume

### Files Modified

| File | Change |
|------|--------|
| `src/core/crash_recovery.py` | NEW — main module |
| `tests/unit/test_crash_recovery.py` | NEW — test suite |
| `src/api/main.py` | ADD crash recovery call to lifespan |
