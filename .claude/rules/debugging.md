# LangGraph & Project Debugging Notes

## CRITICAL: LangGraph Reserved Channel Names
- `checkpoint_id` is **reserved** by LangGraph. Using it as a field in your state TypedDict causes:
  `ValueError: Channel name 'checkpoint_id' is reserved`
- **Fix**: Rename to `mas_checkpoint_id` or any non-reserved name
- Other likely reserved names: `checkpoint_ns`, `checkpoint_map`

## CRITICAL: StateGraph Schema
- Use `StateGraph(dict)` instead of `StateGraph(AgentState)` to avoid issues with LangGraph introspecting TypedDict fields as channel names
- Node functions can still use `AgentState` type hints for documentation, but the graph schema itself should be `dict`

## CRITICAL: Routing Function Type Annotations (LangGraph 1.0.8)
- **Bug**: LangGraph 1.0.8 inspects routing function parameter type annotations
- If routing functions have `state: AgentState` (TypedDict), LangGraph reconstructs the state from the TypedDict schema at each routing decision
- This causes accumulated values (like `artifacts`) to be LOST — they reset to the TypedDict defaults (empty dict)
- **Fix**: Use `state: dict[str, Any]` for ALL routing functions and HITL node functions
- Same applies to node functions — use `dict[str, Any]` to prevent state reconstruction
- Confirmed with A/B test: `AgentState` annotation -> artifacts lost, `dict[str, Any]` -> artifacts preserved

## CRITICAL: LangGraph ainvoke vs astream
- `ainvoke()` may return the INITIAL state instead of FINAL state for complex `StateGraph(dict)` graphs in LangGraph 1.0.8
- **Workaround**: Use `astream(stream_mode="values")` and take the last emitted value
- Pattern: `async for chunk in graph.astream(state, config, stream_mode="values"): final = chunk`

## CompiledGraph Import Path
- **Wrong**: `from langgraph.graph.graph import CompiledGraph`
- **Correct**: `from langgraph.graph.state import CompiledStateGraph as CompiledGraph`

## Testing Compiled Graphs
- You **cannot** replace nodes in a compiled graph by assigning `graph.nodes["name"] = func`
- Compiled nodes are `PregelNode` objects, not raw functions
- **Solution**: Pass mock functions at graph construction time (before `graph.compile()`), then compile with mocks already in place
- Pattern: `_build_pipeline_graph(nodes={"scout": mock_scout, ...})` where nodes dict overrides default imports

## Python Environment
- Always use `venv\Scripts\python.exe` (Windows) for running tests/imports, not system Python
- System Python won't have project dependencies (structlog, langgraph, etc.)

## playwright_stealth API
- **Wrong**: `from playwright_stealth import stealth_async` — this function no longer exists
- **Correct**: `from playwright_stealth import Stealth` then `_stealth = Stealth()` and `await _stealth.apply_stealth_async(page)`
- When testing, mock `src.browser.stealth._stealth` (the module-level instance)

## SemgrepGate Fail-Closed
- SemgrepGate returns `blocked=True` when semgrep binary is not installed (fail-closed design)
- This will break critic/dev agent tests unless SemgrepGate is mocked
- **Solution**: autouse fixture in `tests/conftest.py` (`_mock_semgrep_gate`) patches `src.agents.critic.SemgrepGate` and `src.agents.dev.SemgrepGate` with clean ScanResult

## Test Source Inspection
- When testing that source code doesn't contain forbidden patterns (e.g. `connect_over_cdp`), filter out docstrings/comments
- Raw `inspect.getsource()` includes docstrings — use line filtering to check only executable code

## Docker SDK
- `docker.from_env()` creates a new client — must call `client.close()` when done, else file descriptor/socket leak
- Docker SDK is synchronous — wrap all calls in `asyncio.to_thread()`
- Use `shlex.quote()` for shell command quoting, never a custom implementation

## E2B SDK
- Optional dependency — use `_E2B_AVAILABLE` flag with try/except ImportError on module load
- Path sanitization needed for E2B too (same as Docker) — strip `..`, `/`, `\` from file paths

## Prometheus Testing
- Singleton `MASMetrics` registers collectors on the default registry
- Between tests: set `mod._metrics = None` AND unregister collectors from `REGISTRY._names_to_collectors`
- Without unregistering: `ValueError: Duplicated timeseries` on next `MASMetrics()`

## APScheduler v3
- `shutdown(wait=False)` schedules shutdown via `call_soon_threadsafe`, not immediate
- In tests: need `await asyncio.sleep(0)` after `stop()` to let the event loop process the callback
- Check `scheduler.running` property, not `scheduler._scheduler.state`

## asyncio.Event
- Don't create `asyncio.Event()` at module level — it breaks re-entrancy
- Create inside the async function that uses it

## Ruff S608 False Positive
- pgvector SQL with `$1`-style asyncpg params looks like f-string SQL injection to ruff
- Fix: use string concatenation instead of f-strings, add `# noqa: S608` on the line

## Railway Deploy Gotchas

### Rate Limiting 429 on Login/Register
- **Root cause**: Railway reverse proxy collapses ALL client IPs into one internal IP (`100.64.0.3`)
- Litestar `RateLimitConfig` tracks by IP -> all users share the same bucket
- **Fix**: increased to 300/min global, 60/min auth — long-term fix: per-user rate limiting via JWT

### Alembic Migration Race Condition
- On container start, PostgreSQL may not be ready when Alembic runs
- **Fix**: retry loop in CMD — `for i in 1 2 3 4 5; do alembic upgrade head && break || sleep 5; done`

### Two Services to Deploy
- `api` service (Python) and `dashboard` service (Node/Remix) are SEPARATE Railway services
- Frontend changes require deploying BOTH services

### Dashboard Deploy: `--path-as-root` is CRITICAL
- `railway up` from project root ALWAYS uses root `railway.toml` (Python/API config)
- **Correct command**: `railway up --detach --service dashboard --path-as-root dashboard`
- `--path-as-root` tells Railway CLI to use `dashboard/` as the build context root
- Without this flag, dashboard deploys use Python nixpacks — wrong for Node/Remix

### .dockerignore
- Root `.dockerignore` excludes .env, venv, tests, .git — but NOT `dashboard/` (removing it broke dashboard deploys)
- `dashboard/.dockerignore` excludes node_modules, .env separately
