# Touch Sequence Manager — Implementation Plan

**Task**: Phase 3, Task #14 from gap-analysis.md
**Spec**: `docs/Full_work/specs/sales-agent-spec.md` §Touch Sequence Manager
**Date**: 2026-03-11

## Problem

No automated follow-up system when leads don't respond to first contact.
Manual follow-ups are inconsistent and often forgotten.

## Design

### File: `src/core/touch_sequence.py`

**TouchState** enum (6 states per spec):
- PENDING, ACTIVE, REPLIED, COMPLETED, STOPPED, PAUSED

**TouchStep** dataclass:
- day, channel, template, description, auto_send, text_template

**TOUCH_SCHEDULE** — 4 steps: Day 1 (best_available), Day 3 (same), Day 5 (alternative), Day 10 (email)

**STOP_RULES** — 4 rules: explicit_no, max_touches (3), bounce, replied

**TouchResult** dataclass:
- lead_id, action, reason, channel

**TouchSequenceManager** class:
- `get_next_step(touch_count) -> TouchStep | None`
- `should_stop(touch_count, last_message) -> StopResult | None`
- `resolve_channel(step, lead) -> str`
- `format_message(step, lead, operator_name) -> str`
- `create_sequence(lead_id) -> TouchSequence`
- `advance(sequence) -> TouchResult`

### Edge Cases

- touch_count >= len(TOUCH_SCHEDULE) → completed
- explicit_no keywords → stop forever
- No available channel → stop with reason "no_contact"
- Paused sequence → skip in advance()

### Files

| File | Change |
|------|--------|
| `src/core/touch_sequence.py` | NEW |
| `tests/unit/test_touch_sequence.py` | NEW |
