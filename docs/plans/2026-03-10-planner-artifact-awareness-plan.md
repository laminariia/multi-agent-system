# P1.8 — Planner Artifact Awareness

## Problem
When Planner is re-invoked (after Critic major revision), it generates a plan from scratch
without knowing what agents already produced. This leads to redundant work.

## Solution

### 1. `_build_artifact_context()` method in PlannerAgent
Collects existing execution artifacts (dev, content, design) and Critic feedback
into a summary string for the LLM prompt.

### 2. Enhanced `_generate_plan()`
Accepts optional `artifact_context: str` parameter. When present, appends it
to the user message so the LLM knows what work already exists.

### 3. Re-plan detection in `_execute()`
If `state.artifacts` contains execution agent keys (dev/content/design),
this is a re-plan. Build artifact context and pass to `_generate_plan()`.

### 4. Critic feedback inclusion
If `artifacts["_critic_revision_type"]` or `artifacts["critic"]` exists,
include the revision feedback so the Planner knows WHY re-plan was triggered.

### Files changed
- `src/agents/planner.py` — `_build_artifact_context()`, enhanced `_generate_plan()`
- `tests/unit/test_planner_artifact_awareness.py` — new test file
