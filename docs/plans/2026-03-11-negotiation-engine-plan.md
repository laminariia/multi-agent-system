# NegotiationEngine — Implementation Plan

**Task**: Phase 2, Task #7 from gap-analysis.md
**Spec**: `docs/Full_work/specs/negotiation-spec.md`
**Date**: 2026-03-11

## Problem

After bid submission, client negotiations happen manually. No automated state
tracking, transition validation, or HITL routing for negotiation events.

## Scope (Phase 2)

**Core state machine only** — the foundation for the full NegotiationEngine.
Message poller, classifier, response generator, follow-up scheduler are
Phase 3+ (require platform adapter integration, LLM calls).

## Design

### Package: `src/negotiations/`

#### `state_machine.py` — Pure state machine logic

**NegotiationState enum** (9 states):
- INITIAL, QUALIFYING, PROPOSING, NEGOTIATING, CLOSING
- ACCEPTED, DECLINED, STALE, OPERATOR_OVERRIDE

**VALID_TRANSITIONS** dict — spec-defined transition matrix (18 transitions).

**NegotiationStateMachine** class:
- `__init__(current_state, history)`
- `can_transition(target) -> bool`
- `transition(target, reason, actor) -> None` — validates + records history
- `is_terminal -> bool` — True for ACCEPTED, DECLINED
- `requires_hitl(target) -> bool` — True for transitions requiring HITL
- `current_state` property
- `history` list of transition records

**InvalidTransitionError** exception.

**HITL_REQUIRED_TRANSITIONS** — set of (from, to) tuples that need HITL approval.

#### `engine.py` — Negotiation lifecycle management

**NegotiationEngine** class:
- `create_negotiation(bid_id, platform, initial_state) -> Negotiation`
- `get_negotiation(negotiation_id) -> Negotiation | None`
- `transition(negotiation_id, target, reason, actor) -> bool`
- `get_active() -> list[Negotiation]`
- `mark_stale(threshold_days=14) -> int`

#### `models.py` — Negotiation data class (not ORM, for now)

**Negotiation** dataclass:
- id, bid_id, platform, state, state_machine, messages, created_at, updated_at

### Files

| File | Change |
|------|--------|
| `src/negotiations/__init__.py` | NEW — package init |
| `src/negotiations/state_machine.py` | NEW — state machine |
| `src/negotiations/engine.py` | NEW — engine |
| `tests/unit/test_negotiation_state_machine.py` | NEW — state machine tests |
| `tests/unit/test_negotiation_engine.py` | NEW — engine tests |

### Edge Cases

- Invalid transition → InvalidTransitionError
- operator_override → any state (unrestricted return)
- Terminal states (accepted/declined) → no further transitions
- Stale → qualifying (client returns after silence)
- Concurrent transitions → optimistic locking (future, DB-backed)
