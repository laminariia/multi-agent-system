# MAS Code Patterns

## Agent Pattern (canonical)
Every agent follows this pattern (see `src/agents/scout.py` as reference):
1. Class inherits `ConstrainedAgent` from `src/agents/base.py`
2. Constructor calls `super().__init__(agent_name=..., allowed_tools=..., llm_client=..., heartbeat=..., loop_detector=...)`
3. Implements `async _execute(self, state: AgentState) -> AgentState`
4. Module-level `async def {name}_node(state: AgentState) -> AgentState` function for LangGraph
5. `_get_valkey_client()` helper at bottom

## State Transition Pattern
- Always use `update_state(state, field=value)` — never mutate state dict directly
- `update_state` auto-bumps `updated_at` and refreshes `mas_checkpoint_id`
- Set `current_agent` to self, `next_agent` to target, `status` to "active"/"paused"

## HITL Pattern (see bid.py, packager.py)
- Set `requires_hitl=True`, `hitl_request_id=str(uuid.uuid4())`, `status="paused"`
- Create `HITLQueue` entry in DB with `action_type`, `payload`, `status="pending"`
- Graph HITL nodes check `requires_hitl` and route to END (waiting for resume)

## LLM Response Parsing Pattern
- Strip markdown code fences: check `startswith("```")`, remove first/last line
- `json.loads()` with try/except
- Validate and normalize fields (clamp scores, default missing fields)
- Return `None` on parse failure, handle in caller

## Graph Routing Pattern
- Each node has a `_route_{name}(state)` function returning next node name or `"__end__"`
- Routing checks: `state.get("status") == "failed"` -> END, `state.get("requires_hitl")` -> HITL, `state.get("next_agent")` -> next node

## Test Pattern (unit)
- Mock `LLMClient`, `HeartbeatMonitor`, `LoopDetector`, `get_db_session`
- Create state via `create_initial_state()` with test project context
- Assert on returned state fields: `next_agent`, `status`, `artifacts`, `requires_hitl`

## Test Pattern (integration)
- Build graph with mock node functions at construction time (before compile)
- Each mock returns state with appropriate routing fields
- Use `graph.ainvoke(initial_state)` and check final state

## SQL Wildcard Injection Prevention
- Use `_escape_like()` helper from `src/api/routes/__init__.py`
- Escapes `%`, `_`, `\` for ILIKE queries
- Used in: jobs.py, pipeline_b.py, agents.py

## WebSocket Publish Guard Pattern
- Wrap all `publish_event()` / `channels.publish()` calls in `try/except (OSError, ConnectionError)`
- Prevents WebSocket disconnects from breaking API responses
- Used in: agents.py, orchestrator.py, hitl.py

## AsyncMock for Async Interfaces
- When mocking `ChannelsPlugin` (or any async interface): use `AsyncMock()`, NOT `MagicMock()`
- `MagicMock` for async → `TypeError` on `await`
- Pattern: `channels = AsyncMock()` in test fixtures

## DB-First Credential Loading
- `load_platform_credentials(platform)` → DB-stored keys priority, fallback to env vars
- Pattern: `creds = await load_platform_credentials("freelancer")` → if None → `get_settings()` fallback
- `LLMClient.update_credentials()` → clears `_chat_model_cache` → next call uses fresh model

## Email Approval Pattern
- `_apply_*_approval`: approve/edit → `status='active'`; reject → `status='failed'`
- NEVER set `status='completed'` for approve when graph needs re-invocation
