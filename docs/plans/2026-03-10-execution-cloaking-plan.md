# P1.6: Execution Cloaking (Time-Value Arbitrage)

## Problem
AI completes work in hours; delivering instantly destroys client trust.
Freelancers typically take days — we must simulate that rhythm.

## Solution
Dual estimation + delivery throttling:
1. Planner calculates `real_hours` (actual AI execution time)
2. Bid Agent estimates `human_days` (as a middle developer would)
3. Proposed timeline: `proposed_days` (competitive but believable)
4. Packager enforces `min_delivery_at` — cannot deliver before 70% of proposed time

## New State Fields (`src/core/state.py`)

```python
# Add to AgentState TypedDict:
real_hours: float | None              # AI execution estimate (from Planner)
proposed_days: int | None             # Days proposed to client (from Bid)
min_delivery_at: datetime | None      # Earliest allowed delivery time
scheduled_messages: list[dict[str, Any]]  # Progress messages queue
```

## Changes

### 1. `src/core/state.py` — New fields + defaults
- Add 4 fields to `AgentState`
- Add defaults to `create_initial_state()`

### 2. `src/agents/planner.py` — Store `real_hours`
- After parsing `total_estimated_hours` from LLM response, store in state as `real_hours`
- Location: `_execute()`, after `_generate_plan()` returns
- `real_hours` is the AI's honest estimate of execution time

### 3. `src/agents/bid.py` — Calculate `proposed_days` + `min_delivery_at`
- `delivery_days` from LLM already represents human estimate
- Calculate `min_delivery_at = created_at + timedelta(days=delivery_days * 0.7)`
- Store `proposed_days = delivery_days` in state
- Minimum 1 day — never propose less than 24 hours
- Store `min_delivery_at` in state for Packager to check

### 4. `src/agents/packager.py` — Enforce delivery schedule
- New method `_check_delivery_schedule()`:
  - If `min_delivery_at` exists and `now < min_delivery_at`:
    - Add warning to HITL payload: "Delivery too early — {hours} hours remaining"
    - Set `delivery_hold = True` in HITL entry
  - If no `min_delivery_at`: proceed normally (backwards-compatible)
- Warning is informational — operator can override via HITL approve

### 5. Scheduled Messages (structure only)
- `scheduled_messages` in state: `[{"at": datetime, "text": str, "sent": bool}]`
- Planner generates 2-3 progress messages based on `proposed_days`
- Actual sending deferred to future scheduler integration

## Edge Cases
1. `proposed_days = 0` → force minimum 1 day
2. No `min_delivery_at` → skip throttling (backwards-compatible)
3. `real_hours` parse failure → default to 0.0
4. Operator overrides early delivery via HITL approve → allowed
5. `scheduled_messages` empty → no progress messages (OK)
6. Platform deadline < min_delivery_at → warn in HITL but respect platform deadline

## Files Modified
- `src/core/state.py` — 4 new fields, defaults
- `src/agents/planner.py` — store real_hours from plan
- `src/agents/bid.py` — calculate proposed_days + min_delivery_at
- `src/agents/packager.py` — delivery schedule check

## Tests (~25)
- State: new fields defaults, min_delivery_at calculation
- Planner: real_hours stored from plan, default on parse failure
- Bid: proposed_days stored, min_delivery_at calculated, minimum 1 day
- Packager: early delivery warning, no min_delivery_at passes, operator override
- Integration: full flow with timing fields
