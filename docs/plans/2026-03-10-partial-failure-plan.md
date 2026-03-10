# P1.5: Partial Failure Recovery

## Problem
When an execution agent (dev/content/design) fails, the entire workflow terminates (`status="failed"`, route to END). Completed agents' artifacts are preserved but unreachable — no way to continue.

## Solution
On agent failure: pause workflow, create HITL entry with 3 recovery options:
- **resume** — retry the failed agent (max 3 attempts)
- **skip** — skip this agent, continue sequence
- **manual** — operator provides artifacts, continue sequence

## New State Fields (`src/core/state.py`)

```python
# Add to AgentState TypedDict:
failed_agent: str | None            # "dev" | "content" | "design"
failure_reason: str | None          # Error message for HITL display
recovery_attempted: int             # Retry counter (max 3)
skipped_agents: list[str]           # Agents skipped by operator
```

## Changes

### 1. `src/agents/base.py` — ConstrainedAgent.invoke()
Current: exception → `status="failed"`, `next_agent=None`
New: exception → `status="paused"`, `requires_hitl=True`, `failed_agent=self.agent_name`, `failure_reason=str(e)`
- Create `HITLQueue` entry with type `"agent_failure"`, available_actions `["resume", "skip", "manual"]`
- Preserve any partial artifacts already in state

### 2. `src/core/state.py` — New fields + helpers
- Add 4 fields to `AgentState`
- Add defaults to `create_initial_state()`
- Add `clear_failure(state)` helper — resets `failed_agent`, `failure_reason`

### 3. `src/core/graph.py` — Routing updates
- `_route_next_in_sequence()`: skip agents in `skipped_agents` list
- HITL resume handler: process `agent_failure` type with resume/skip/manual actions

### 4. `src/api/routes/hitl.py` — HITL resolve endpoint
- Support `recovery_action` field: "resume" | "skip" | "manual"
- For "resume": increment `recovery_attempted`, clear failure, re-invoke agent
- For "skip": add to `skipped_agents`, increment `current_sequence_index`, continue
- For "manual": store provided artifacts, increment index, continue

## Edge Cases
1. Resume 3x fails → force HITL escalation (no more auto-resume)
2. Skip middle agent → Critic receives incomplete work, may flag
3. All agents skipped → go directly to Critic with whatever artifacts exist
4. Manual artifacts empty → accept (Critic will flag)
5. Failure during Critic → same pattern (pause + HITL)
6. Resume clears failure state before retry

## Files Modified
- `src/core/state.py` — 4 new fields, defaults, helper
- `src/agents/base.py` — invoke() error handling
- `src/core/graph.py` — routing skip logic, HITL resume handler
- `src/api/routes/hitl.py` — recovery_action support

## Tests (~25)
- State: new fields defaults, clear_failure helper
- Base agent: failure → paused state, HITL entry creation, partial artifact preservation
- Routing: skip agents, resume routing, max retry escalation
- HITL resolve: resume/skip/manual actions, invalid recovery_action
- Integration: full sequence with failure + recovery
