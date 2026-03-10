# P1.7 — HITL Plan Review Edit (Sequence Editing)

## Problem
When operator chooses "edit" in plan_review HITL, the system stores edits in artifacts
but does NOT actually apply them to `agent_sequence`, `delivery_type`, or other plan fields.
The operator cannot remove/add agents or change delivery type.

## Solution

### 1. Enhance `_apply_plan_review` "edit" action in `src/core/graph.py`

When `action == "edit"` and `hitl_response.edits` contains:
- `agent_sequence`: list[str] — validated, replaces state's `agent_sequence`
- `delivery_type`: str — validated via `validate_delivery_type()`
- `tasks`: dict — stored in artifacts for downstream agents (informational)

**Validation:**
- `agent_sequence` entries must be in `_VALID_EXECUTION_AGENTS = {"dev", "content", "design"}`
- Empty sequence is valid (consulting → skip to Packager)
- `delivery_type` validated via existing `validate_delivery_type()`
- Invalid entries logged + removed (not rejected entirely)

**Audit trail:**
- `artifacts["_original_sequence"]` = pre-edit sequence
- `artifacts["_plan_edit_applied"]` = True
- `artifacts["hitl_edits"]` = raw edits dict

**Routing:**
- After edit, `current_sequence_index = 0` (reset since sequence changed)
- `next_agent` set via first element of new sequence (or "packager" if empty)

### 2. Files changed
- `src/core/graph.py` — `_apply_plan_review` edit branch
- `tests/unit/test_hitl_plan_edit.py` — new test file

### 3. NOT in scope
- Dashboard UI for editing (P2)
- API changes (existing resolve endpoint + edits field sufficient)
