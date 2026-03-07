# Testing Quality Code Review

**Date:** 2026-02-23
**Reviewer:** Research Worker (Claude Opus 4.6)
**Scope:** conftest.py, factories.py, property tests, integration tests, golden set, coverage gaps, dashboard

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3     |
| HIGH     | 6     |
| MEDIUM   | 5     |
| LOW      | 3     |

---

## CRITICAL Findings

### C1. Integration Tests Use a Duplicate Graph, Not the Production Graph

**File:** `/Users/awon/programming/projects/MAS/tests/integration/test_full_pipeline.py` (lines 58-183)
**What:** `test_full_pipeline.py` builds its own graph via `_build_pipeline_graph()` with its own routing functions. These routing functions diverge from the production graph in `src/core/graph.py` in critical ways:

1. **Missing `bid_submission_node`** -- The production graph has `hitl_bid -> bid_submission_node -> planner`, but the test graph routes `hitl_bid -> planner` directly, skipping bid submission entirely.
2. **Different `_route_after_hitl_bid`** -- Production routes to `bid_submission_node`; test routes to `planner_node`.
3. **Different `_route_after_hitl_review`** -- Production routes to `dev_node` when `_hitl_type == "plan_review"` and status is active; test always routes to END.
4. **No revision count enforcement** -- Production `_route_after_critic` has `MAX_REVISION_CYCLES` (3) enforcement with HITL escalation; test `route_after_critic` has no cycle limit.

**Why it matters:** The most-run integration test validates a graph topology that does not exist in production. Routing bugs in the production graph (especially around bid submission and revision limits) would pass these tests but fail in production.

**Recommended fix:** This file should either (a) import and test the production `build_full_pipeline_graph()` from `src/core/graph.py` with patched node functions (as `test_production_graph.py` already does), or (b) be clearly marked as "routing-logic-only" tests with a comment explaining the divergence. Ideally, delete this file and consolidate into `test_production_graph.py`.

---

### C2. `_mock_semgrep_gate` autouse Fixture Hides Real Security Gate Failures

**File:** `/Users/awon/programming/projects/MAS/tests/conftest.py` (lines 229-256)
**What:** The `_mock_semgrep_gate` fixture is `autouse=True` and patches:
- `src.agents.critic.SemgrepGate` -- always returns `blocked=False`
- `src.agents.dev.SemgrepGate` -- always returns `blocked=False`
- `src.agents.dev.SandboxManager` -- always returns `exit_code=0`

This runs for EVERY test in the entire test suite (96+ unit tests, 21+ integration tests, golden set tests, load tests).

**Why it matters:**
- If `SemgrepGate` has a real bug (e.g., a parsing error in semgrep output), no test will catch it because the real class is never instantiated outside of `test_semgrep_gate.py`.
- If `SandboxManager.execute_code()` returns an error for malicious code, no test will catch that the Dev agent handles it correctly -- the mock always returns success.
- The Critic agent's "block on critical findings" path is untestable with this fixture active, since `blocked` is hardcoded to `False`.

**Recommended fix:**
1. Remove `autouse=True` from `_mock_semgrep_gate`.
2. Apply it explicitly only to tests that need it (agent unit tests).
3. Create dedicated tests in `test_critic_agent.py` and `test_dev_agent.py` that exercise the SemgrepGate with `blocked=True` and verify the agents handle it correctly.
4. Keep `test_semgrep_gate.py` as-is for unit-testing the gate itself.

---

### C3. Property Tests Directory Is Empty -- hypothesis Is a Dead Dependency

**File:** `/Users/awon/programming/projects/MAS/tests/property/` (contains only `__init__.py`)
**File:** `/Users/awon/programming/projects/MAS/pyproject.toml` (line 80: `"hypothesis>=6.0"`)
**What:** `hypothesis` is listed as a dev dependency but the `tests/property/` directory contains zero test files. No property-based tests exist anywhere in the codebase.

**Why it matters:** Property-based testing is especially valuable for this project's critical paths:
- **JSON parsing/repair** (`src/core/json_repair.py`) -- testing with arbitrary malformed JSON strings
- **State transitions** (`update_state`) -- verifying state invariants hold for any combination of field updates
- **Score clamping** in agents -- verifying `match_score` stays in [0.0, 1.0] for any LLM output
- **SQL escape** (`_escape_like`) -- verifying no ILIKE injection for arbitrary input strings
- **Routing functions** -- verifying routing determinism for all possible state combinations

Without property tests, edge cases in these areas are found only by luck or in production.

**Recommended fix:** Either write property tests for the above areas, or remove `hypothesis` from `pyproject.toml` to avoid a misleading dependency. The highest-value target is `json_repair.py` -- a single hypothesis test with `st.text()` strategies could catch parsing regressions that handwritten cases miss.

---

## HIGH Findings

### H1. Golden Set Tests Missing for 4 of 10 Agents

**File:** `/Users/awon/programming/projects/MAS/tests/golden_set/conftest.py` (lines 11-17)
**What:** Golden set cases exist for: scout, bid, content, planner, critic, outreach (6 agents).
Missing golden set cases for: **dev**, **design**, **packager**, **geo_scout** (4 agents).

**Why it matters:**
- The Dev agent produces code file artifacts -- structural validation (valid JSON, has `files` array, each file has `path` and `content`) is untested at the golden level.
- The Design agent produces design specs -- no validation that specs have `component`, `colors`, etc.
- The Packager agent produces archive URLs and delivery metadata -- no structural validation.
- GeoScout produces business lists with geocoordinates -- no golden validation.

These agents' LLM output schemas could drift without detection.

**Recommended fix:** Create `dev_agent_golden.py`, `design_agent_golden.py`, `packager_agent_golden.py`, and `geo_scout_agent_golden.py` with 3-5 cases each. Add corresponding Pydantic models to the `_AGENT_MODEL_MAP` in `test_golden_regression.py`.

---

### H2. No Test Coverage for `src/services/telegram_listener.py`

**File:** `/Users/awon/programming/projects/MAS/src/services/telegram_listener.py` (252 lines)
**What:** Zero test files reference `telegram_listener`. The `test_telegram_channels.py` and `test_telegram_parser.py` files test other modules (`src/bot/` and `src/services/telegram_parser.py`), not the listener itself.

**Why it matters:** The Telegram listener is a long-running process that:
- Connects to Telegram via MTProto (Telethon)
- Parses incoming messages via LLM
- Pushes parsed jobs to Valkey queue
- Refreshes channel lists from DB periodically
- Handles signal-based shutdown

Bugs in message parsing, queue pushing, or graceful shutdown could cause silent data loss (jobs not captured) or process hangs.

**Recommended fix:** Create `tests/unit/test_telegram_listener.py` covering:
- Message handler parsing (mock Telethon event, verify Valkey push)
- Channel refresh logic (mock DB query, verify channel list update)
- Graceful shutdown (send signal, verify cleanup)

---

### H3. Dashboard Has Zero Frontend Unit Tests

**File:** `/Users/awon/programming/projects/MAS/dashboard/package.json`
**What:** No test framework (vitest, jest, @testing-library) in `dependencies` or `devDependencies`. No `test` script in `package.json` scripts. The only dashboard testing is Playwright E2E tests in `tests/e2e/dashboard/`.

**Why it matters:** The Remix dashboard contains:
- Zustand state stores (client-side state management)
- API client hooks (@tanstack/react-query)
- Complex UI components (HITL approval forms, WebSocket real-time updates, settings with encryption)

Without unit tests, logic bugs in state management, data transformation, or form validation are caught only by E2E tests (slow, brittle, hard to debug).

**Recommended fix:** Add vitest + @testing-library/react to dashboard devDependencies. Priority test targets:
1. Zustand stores (state logic, computed values)
2. API hooks (error handling, loading states, optimistic updates)
3. HITL approval form (action dispatch, validation)

---

### H4. Factory Counters Leak Between Tests (Class-Level Mutable State)

**File:** `/Users/awon/programming/projects/MAS/tests/factories.py` (lines 18, 58, 88, 118, 148, 166)
**What:** All factory classes use `_counter: int = 0` as a class variable that increments on every `create()` call. This counter is never reset between tests.

```python
class JobFactory:
    _counter: int = 0  # shared across ALL tests in a session

    @classmethod
    def create(cls, **overrides):
        cls._counter += 1  # monotonically increasing
```

**Why it matters:**
- Test output is non-deterministic across runs if test ordering changes (e.g., `pytest --randomly`).
- Factory-generated `external_id` values like `ext-00042` depend on how many tests ran before.
- If any test asserts on factory-generated fields (e.g., `assert job["title"] == "Build landing page #1"`), it will break when tests run in a different order.
- The `HITLFactory.create()` internally calls `JobFactory.create()` and `ProposalFactory.create()`, causing hidden counter increments.

**Recommended fix:** Either:
(a) Reset counters in a session-scoped or function-scoped fixture: `JobFactory._counter = 0`
(b) Switch to instance-based factories (not classmethod) with per-test instances
(c) Use UUIDs instead of counters for fields that need uniqueness

---

### H5. `_restore_langgraph_compile` autouse Fixture Masks Sentry Integration Failures

**File:** `/Users/awon/programming/projects/MAS/tests/conftest.py` (lines 22-36)
**What:** This autouse fixture saves the original `StateGraph.compile` before Sentry can patch it, and restores it after every test. This means Sentry's LangGraph integration is never tested with the actual graph compilation.

**Why it matters:** If Sentry's patched `compile` has a bug with `StateGraph(dict)` graphs (as the comment acknowledges), this fixture prevents discovering whether:
- The Sentry fix for this issue has been released
- The production code (which runs with Sentry enabled) will crash on graph compilation
- Sentry tracing correctly captures LangGraph spans

There IS a `test_sentry_integration.py` but if this fixture runs globally, it may bypass the very behavior it needs to test.

**Recommended fix:**
1. Keep the fixture but add a dedicated test that explicitly exercises `StateGraph(dict).compile()` with Sentry's monkey-patch active.
2. Add a comment to the fixture explaining WHEN it can be safely removed (e.g., "Remove after Sentry SDK >= X.Y.Z fixes LangGraph dict graph support").

---

### H6. Evaluator `_extract_text` Has Fragile Text Extraction Logic

**File:** `/Users/awon/programming/projects/MAS/tests/golden_set/evaluator.py` (lines 188-205)
**What:** The `_extract_text` function tries specific keys in order (`proposal_text`, `body`, `reasoning`, `revision_instructions`), then falls back to `deliverables`, then concatenates all string values > 15 chars.

```python
def _extract_text(output: dict[str, Any]) -> str:
    for key in ("proposal_text", "body", "reasoning", "revision_instructions"):
        if key in output and output[key]:
            return str(output[key])  # returns FIRST match only
    # ...
    return " ".join(
        str(v) for v in output.values() if isinstance(v, str) and len(v) > 15
    )
```

**Why it matters:**
- If an agent output has BOTH `reasoning` AND `proposal_text`, only `proposal_text` is checked -- `reasoning` is silently ignored by `must_mention` / `must_not_mention` validators.
- The `len(v) > 15` fallback arbitrarily excludes short text fields that might contain forbidden keywords.
- Nested dict values (e.g., `output["issues"][0]["desc"]`) are never searched.

This means `must_not_mention` rules could pass even when a forbidden keyword exists in a field that `_extract_text` doesn't reach.

**Recommended fix:** Either:
(a) Extract ALL text recursively from the output dict (walk nested dicts/lists)
(b) Allow `must_mention`/`must_not_mention` rules to specify which field(s) to search
(c) Document the extraction priority and add tests for edge cases

---

## MEDIUM Findings

### M1. Integration Test Graph Uses `ainvoke` Instead of `astream`

**File:** `/Users/awon/programming/projects/MAS/tests/integration/test_full_pipeline.py` (lines 224, 259, 336, etc.)
**What:** All tests use `graph.ainvoke()` to get results:
```python
result = await graph.ainvoke(_make_initial_state())
```

Meanwhile, `test_production_graph.py` uses a `_run_graph` helper with `astream(stream_mode="values")` specifically to work around a known LangGraph 1.0.8 bug where `ainvoke` returns initial state instead of final state.

**Why it matters:** If the `ainvoke` bug manifests in `test_full_pipeline.py`, tests could pass by accident (assertions checking initial state values that happen to match). The production graph tests already use the workaround -- this file should too.

**Recommended fix:** Replace `ainvoke` with the `astream` pattern from `test_production_graph.py`.

---

### M2. State Mutation in `_route_after_critic` (Production Code Side Effect)

**File:** `/Users/awon/programming/projects/MAS/src/core/graph.py` (lines 622-628)
**What:** The production `_route_after_critic` routing function mutates state directly:
```python
artifacts["_critic_revision_count"] = revision_count + 1
state["artifacts"] = artifacts
```

Routing functions in LangGraph should be pure predicates that return a string. Mutating state inside a routing function is a side effect that:
- Is not captured by LangGraph's state management (no reducer applied)
- May or may not persist depending on LangGraph internals and checkpointer behavior
- Is tested in `test_critic_revision_boundary.py` which verifies the mutation happens, but this cements a fragile pattern

**Why it matters:** If LangGraph changes how routing function state is handled (e.g., passes a copy), the revision counter would silently stop incrementing, leading to infinite revision loops in production.

**Recommended fix:** Move the revision count increment into the critic node function itself (or a dedicated counter node), not the routing function.

---

### M3. No Negative Golden Set Cases

**File:** `/Users/awon/programming/projects/MAS/tests/golden_set/` (all `*_golden.py` files)
**What:** All golden cases test the "happy path" -- well-formed LLM outputs that should parse correctly. There are no cases testing:
- Malformed JSON (missing closing brace)
- JSON with extra markdown fences (`````json\n{...}\n````)
- Empty response strings
- Responses with hallucinated fields
- Responses with wrong types (score as string "0.85" instead of float)

**Why it matters:** The golden set's purpose is regression testing of LLM output parsing. If `json_repair.extract_json()` changes behavior on edge cases, no golden test will catch it. The most common LLM failure mode is malformed JSON -- exactly what should be tested.

**Recommended fix:** Add 3-5 "negative" golden cases per agent that test the parsing pipeline's robustness: malformed JSON, extra markdown, truncated output.

---

### M4. `mock_db_pool` Fixture Returns Empty Results for Everything

**File:** `/Users/awon/programming/projects/MAS/tests/conftest.py` (lines 76-97)
**What:** `conn.fetch` returns `[]` and `conn.fetchrow` returns `None` by default. Tests that need non-empty DB results must override these, but many tests don't.

**Why it matters:** Tests that exercise code paths like "load credentials from DB" or "check if job exists in DB" will always take the "not found" path. The "found" path may have bugs that never surface because the mock defaults to empty.

**Recommended fix:** This is a known trade-off. Document it clearly in the fixture docstring: "Default returns empty results. Tests verifying DB-found paths MUST override `conn.fetchrow.return_value`."

---

### M5. `test_golden_case_validates` Does Not Enforce Minimum Validator Count

**File:** `/Users/awon/programming/projects/MAS/tests/golden_set/test_golden_regression.py` (lines 63-67)
**What:** If a golden case has an empty `validators` list, `test_golden_case_validates` passes trivially:
```python
def test_golden_case_validates(golden_case: GoldenCase) -> None:
    parsed = extract_json(golden_case.expected_output)
    eval_result = evaluate_case(golden_case, parsed)  # no validators = always passes
    assert eval_result.passed
```

**Why it matters:** A golden case with no validators provides false confidence. It would be easy to add a new agent's golden cases and forget to add validators.

**Recommended fix:** Add `assert len(golden_case.validators) >= 1, f"Golden case '{golden_case.name}' has no validators"` at the start of the test.

---

## LOW Findings

### L1. No `conftest.py` in `tests/integration/` for Shared Integration Fixtures

**File:** `/Users/awon/programming/projects/MAS/tests/integration/` (no conftest.py)
**What:** Each integration test file re-creates helper functions like `_make_project()`, `_make_initial_state()`, mock agent functions (`_scout_finds_jobs`, `_bid_paused`, etc.). These are duplicated across `test_full_pipeline.py`, `test_production_graph.py`, `test_pipeline_a_full.py`, etc.

**Why it matters:** Code duplication means divergent test data. If `ProjectContext` schema changes, multiple files need updating.

**Recommended fix:** Create `tests/integration/conftest.py` with shared fixtures and helper functions.

---

### L2. `sample_project` Fixture Uses a Single Hardcoded Platform

**File:** `/Users/awon/programming/projects/MAS/tests/conftest.py` (line 162: `platform="freelancer"`)
**What:** The default `sample_project` fixture only tests with `platform="freelancer"`. Platform-specific branching (FL.ru, Kwork, Upwork) is not exercised by default.

**Why it matters:** If the bid submission node behaves differently per platform (and it does -- see `bid_submission_node` in `graph.py`), tests that use `sample_project` only test the Freelancer path.

**Recommended fix:** Use `@pytest.fixture(params=["freelancer", "fl_ru", "kwork", "upwork"])` for a parametrized variant, or ensure platform-specific tests explicitly create project contexts for each platform.

---

### L3. Load Tests Exist but Are Not Part of CI

**File:** `/Users/awon/programming/projects/MAS/tests/load/` (locustfile.py, validate_results.py)
**What:** Load tests exist using Locust, but `pyproject.toml` [tool.pytest.ini_options] `testpaths = ["tests"]` would pick them up as regular tests. There's no pytest marker to exclude them from regular runs.

**Why it matters:** Running `pytest` could accidentally trigger Locust tests, which need a running server.

**Recommended fix:** Add a `load` marker and exclude it from default pytest runs, or move `tests/load/` outside the `tests/` directory.

---

## Coverage Gap Summary

| Source Module | Test File(s) | Coverage Assessment |
|---|---|---|
| `src/browser/stealth.py` | `test_stealth_browser.py` | Covered (mocked Playwright) |
| `src/browser/session.py` | `test_session_manager.py` | Covered (mocked Valkey) |
| `src/browser/pool.py` | `test_browser_pool.py` | Covered |
| `src/enrichment/waterfall.py` | `test_enrichment_modules.py` | Covered |
| `src/enrichment/osint.py` | `test_enrichment_modules.py` | Covered |
| `src/enrichment/hunter.py` | `test_enrichment_modules.py` | Covered |
| `src/enrichment/apollo.py` | `test_enrichment_modules.py` | Covered |
| `src/enrichment/email_sender.py` | `test_email_sender.py` + `test_email_sender_extended.py` | Well covered |
| `src/services/telegram_listener.py` | **NONE** | **ZERO coverage** (HIGH gap) |
| `src/services/telegram_parser.py` | `test_telegram_parser.py` | Covered |
| `dashboard/` (frontend) | E2E only (Playwright) | **No unit tests** (HIGH gap) |

---

## Prioritized Action Items

1. **[CRITICAL]** Consolidate `test_full_pipeline.py` into `test_production_graph.py` or re-sync the test graph topology with production
2. **[CRITICAL]** Remove `autouse=True` from `_mock_semgrep_gate` and apply explicitly
3. **[CRITICAL]** Either write hypothesis property tests or remove the dead dependency
4. **[HIGH]** Add golden set cases for dev, design, packager, geo_scout agents
5. **[HIGH]** Write unit tests for `telegram_listener.py`
6. **[HIGH]** Add vitest + frontend unit tests to dashboard
7. **[HIGH]** Fix factory counter leakage between tests
8. **[MEDIUM]** Move revision count mutation out of routing function
9. **[MEDIUM]** Add negative golden set test cases
10. **[MEDIUM]** Switch `test_full_pipeline.py` from `ainvoke` to `astream`
