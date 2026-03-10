# Architecture Code Review: Core Modules

**Date:** 2026-02-23
**Reviewer:** Research Worker (automated)
**Scope:** `src/core/graph.py`, `src/core/database.py`, `src/core/container.py`, `src/core/state.py`, `src/core/checkpoints.py`, `src/agents/base.py`, `src/api/dependencies.py`

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH     | 5 |
| MEDIUM   | 6 |
| LOW      | 3 |
| **Total** | **17** |

---

## CRITICAL Findings

### C-1. `graph.py:627-628` -- Direct state mutation in routing function

```python
def _route_after_critic(state: dict[str, Any]) -> str:
    # ...
    if state.get("next_agent") == "dev":
        artifacts = state.get("artifacts") or {}
        revision_count = artifacts.get("_critic_revision_count", 0)
        if revision_count < MAX_REVISION_CYCLES:
            artifacts["_critic_revision_count"] = revision_count + 1
            state["artifacts"] = artifacts  # <-- DIRECT MUTATION
```

**Issue:** The routing function directly mutates the LangGraph state dict. Routing functions in LangGraph are supposed to be pure -- they inspect the state and return a string for the next node. Mutating state in a router bypasses LangGraph's state management and checkpoint serialization.

**Production risk:** The revision counter increment may not be persisted to the checkpoint, causing infinite revision loops that exceed `MAX_REVISION_CYCLES`. If checkpointing writes the state *before* the routing function runs, the mutation is lost entirely. This is a data-loss race condition.

**Recommended fix:** Move the revision counter increment into the Critic agent's `_execute()` method, or into a dedicated "increment_revision" node that runs before the routing decision. The router should only read `_critic_revision_count` and decide.

---

### C-2. `database.py:26-36` -- Module-level eager engine initialization

```python
_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.async_database_url,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
)
```

**Issue:** The database engine and connection pool are created at **import time**. Any `import` of `src.core.database` (even transitive) triggers `get_settings()` which reads `.env` and validates, then immediately creates a SQLAlchemy engine with a connection pool. This happens before the application lifecycle starts.

**Production risks:**
1. **Test pollution:** Every test that imports any module touching `database.py` creates a real engine pointed at whatever `DATABASE_URL` is configured. Mocking requires patching at the module level before import.
2. **Import failure cascade:** If `DATABASE_URL` is missing or invalid (e.g., in a CI lint job that only needs to check syntax), the import itself fails with a pydantic `ValidationError`, making the entire `src/` tree unimportable.
3. **No lifecycle control:** The pool starts before Litestar's `on_startup` hook, so health checks, migrations, and readiness probes cannot gate against a healthy DB.

**Recommended fix:** Replace module-level creation with a lazy factory:
```python
_engine: AsyncEngine | None = None

def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().async_database_url, ...)
    return _engine
```
Or integrate with Litestar's `on_startup`/`on_shutdown` lifecycle to create and dispose the engine.

---

### C-3. `database.py:69-73` -- Module-level Valkey pool with same eager initialization

```python
_valkey_pool: aioredis.ConnectionPool = aioredis.ConnectionPool.from_url(
    _settings.valkey_redis_url,
    max_connections=20,
    decode_responses=True,
)
```

**Issue:** Same as C-2 but for Valkey. `ConnectionPool.from_url()` is synchronous and does no I/O, but it hard-couples the connection URL to import time. If the URL is wrong, every test and CLI tool that transitively imports this module will try to connect to a non-existent Valkey.

**Additional risk:** `decode_responses=True` converts all Valkey responses to `str`. The checkpoint saver (`checkpoints.py`) stores JSON blobs via `json.dumps()` and reads them back with `json.loads()`. This works, but if any binary data (e.g., serialized BaseMessage objects) is stored, it will be silently corrupted by the decode.

**Recommended fix:** Same lazy factory pattern. Move pool creation into `get_valkey()` with a module-level `None` sentinel.

---

## HIGH Findings

### H-1. `graph.py:1148,1193,1248` -- Using `ainvoke()` instead of `astream()` for complex graphs

```python
result: AgentState = await graph.ainvoke(initial_state, config=config or None)
```

**Issue:** The project's own `debugging.md` (line: "ainvoke vs astream") documents that `ainvoke()` may return the **initial** state instead of the **final** state for complex `StateGraph(dict)` graphs in LangGraph 1.0.8. Despite this known issue, all three convenience runners (`run_full_pipeline`, `run_scout_bid_pipeline`, `run_pipeline_b`) use `ainvoke()`.

**Production risk:** A completed pipeline may return the initial state to the caller, making the system believe no work was done. HITL decisions, artifacts, and status changes would all appear to be missing.

**Recommended fix:** Use the documented workaround:
```python
final = initial_state
async for chunk in graph.astream(state, config, stream_mode="values"):
    final = chunk
return final
```

---

### H-2. `checkpoints.py:260-307` -- Two SQL inserts without a transaction in `_pg_put`

```python
async with self.db_pool.acquire() as conn:
    # Upsert into main checkpoints table
    await conn.execute("""INSERT INTO langgraph_checkpoints ...""", ...)
    # Append to history
    await conn.execute("""INSERT INTO langgraph_checkpoint_history ...""", ...)
```

**Issue:** The two `INSERT` statements (main checkpoint + history) run on the same connection but without an explicit transaction. By default, `asyncpg` connections operate in auto-commit mode -- each `execute()` is its own implicit transaction. If the first INSERT succeeds but the second fails (e.g., constraint violation, connection drop), the checkpoint exists without a corresponding history entry.

**Production risk:** Corrupted checkpoint history. Rollback support (which relies on the history table) breaks silently. The main checkpoint and history table drift apart.

**Recommended fix:**
```python
async with self.db_pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute(...)  # checkpoints
        await conn.execute(...)  # history
```

---

### H-3. `checkpoints.py:205,220,267` -- `json.dumps(data, default=str)` silently corrupts non-serializable types

```python
serialized = json.dumps(data, default=str)
```

**Issue:** Using `default=str` as a serialization fallback means any type that `json.dumps` cannot handle is converted to its `str()` representation. This includes `datetime`, `UUID`, `bytes`, and complex objects like `BaseMessage`. The `str()` representation is **not reversible** -- `json.loads()` will return it as a plain string, losing the original type.

**Production risk:** After a checkpoint restore, `datetime` fields become strings like `"2026-02-23 14:30:00+00:00"`, `UUID` fields become `"a1b2c3..."` strings, and `BaseMessage` objects become `"content='...' ..."` strings. The graph state becomes subtly corrupted after any pause/resume cycle. The class defines `serde = JsonPlusSerializer()` at line 43 but then bypasses it entirely by using raw `json.dumps` in all storage methods.

**Recommended fix:** Use the `JsonPlusSerializer` that is already defined on the class:
```python
serialized = self.serde.dumps(data)
# and for deserialization:
data = self.serde.loads(raw)
```

---

### H-4. `container.py:104-105` -- Private attribute access across class boundary

```python
@semantic_cache.setter
def semantic_cache(self, value: Any) -> None:
    self._semantic_cache = value
    if self._llm_client is not None:
        self._llm_client._semantic_cache = value  # noqa: SLF001
```

**Issue:** The container directly sets a private attribute (`_semantic_cache`) on the `LLMClient` instance. This bypasses any validation or cache invalidation that `LLMClient` might perform, and creates a hidden coupling. The `noqa: SLF001` suppression is an explicit acknowledgement that this violates encapsulation.

**Production risk:** If `LLMClient` changes its internal state management (e.g., renaming the attribute, adding validation), this will silently break. The semantic cache could be set to an incompatible type with no error until an LLM call is made.

**Recommended fix:** Add a public method to `LLMClient`:
```python
class LLMClient:
    def set_semantic_cache(self, cache: SemanticCache | None) -> None:
        self._semantic_cache = cache
```

---

### H-5. `graph.py:1571-1582,1636-1646,1693-1700,1756-1768` -- Unknown HITL action defaults to "approve"

```python
# Unknown action -- treat as approve with a warning.
logger.warning("hitl_bid_unknown_action", action=action, thread_id=thread_id)
return update_state(
    saved_state,
    requires_hitl=False, status="active",
    next_agent="planner", current_agent="planner",
    ...
)
```

**Issue:** All four `_apply_*_approval` functions treat unrecognized HITL actions as "approve". This is done in `_apply_bid_approval`, `_apply_plan_review`, `_apply_final_review`, and `_apply_email_approval`.

**Production risk:** A malformed API request or a UI bug sending an unexpected action string (e.g., `"approv"`, `"skip"`, `""`) will silently approve bids, plans, deliveries, and emails. This is the opposite of the fail-safe principle -- HITL is the primary safety gate for the entire system.

**Recommended fix:** Unknown actions should raise a `ValueError` or return a "failed" state:
```python
raise ValueError(f"Unknown HITL action: {action!r}. Expected: approve, reject, edit")
```

---

## MEDIUM Findings

### M-1. `container.py:43-49` -- All `Any` typing eliminates static type safety

```python
def __init__(self) -> None:
    self._llm_client: Any | None = None
    self._heartbeat: Any | None = None
    self._loop_detector: Any | None = None
    self._semantic_cache: Any | None = None
    self._browser_pool: Any | None = None
    self._telegram_adapter: Any | None = None
```

**Issue:** Every private attribute and property return type is `Any`. The container is the central dependency injection point for all 10 agents -- having no type constraints means mypy/pyright cannot catch type mismatches between what the container provides and what agents expect.

**Production risk:** Any refactoring of `LLMClient`, `HeartbeatMonitor`, or `LoopDetector` interfaces could break agent constructors silently. Type checkers cannot flag these issues.

**Recommended fix:** Use concrete types with `TYPE_CHECKING` guard to avoid circular imports:
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.core.llm_client import LLMClient
    from src.core.heartbeat import HeartbeatMonitor
    from src.core.loop_detector import LoopDetector
```

---

### M-2. `state.py:58` -- `artifacts` type annotation mismatch with actual usage

```python
# In AgentState TypedDict:
artifacts: dict[str, list[str]]
```

**Issue:** The type annotation says artifacts is `dict[str, list[str]]`, but throughout the codebase artifacts is used as `dict[str, Any]` -- holding dicts, bools, ints, floats, and nested objects:
- `artifacts["bid_submitted"] = True` (graph.py:120)
- `artifacts["bid_submission_result"] = result` (graph.py:121)
- `artifacts["_hitl_started_at"] = time.time()` (graph.py:214)
- `artifacts["_critic_revision_count"] = 0` (graph.py:627)
- `artifacts["email_send_result"] = {"sent": 0, ...}` (graph.py:309)

**Production risk:** The TypedDict annotation is actively misleading. Tools relying on the type annotation (e.g., code generation, documentation) will produce incorrect schemas.

**Recommended fix:** Change to `artifacts: dict[str, Any]`.

---

### M-3. `state.py:146` -- `AgentState(**merged)` call with `type: ignore` masks validation errors

```python
def update_state(state: AgentState, /, **overrides: Any) -> AgentState:
    merged: dict[str, Any] = {**state, **overrides}
    merged["updated_at"] = datetime.now(tz=UTC)
    if "mas_checkpoint_id" not in overrides:
        merged["mas_checkpoint_id"] = uuid.uuid4().hex
    return AgentState(**merged)  # type: ignore[typeddict-item]
```

**Issue:** The `type: ignore` on line 146 suppresses all TypedDict validation. Since TypedDict constructors do no runtime validation in Python, this function will happily accept typos like `update_state(state, stauts="failed")` without any error. The misspelled key silently persists in the dict but is never read.

**Production risk:** Silent state corruption from typos in any of the 50+ call sites. The field intended to be updated is not updated; the typo field is ignored.

**Recommended fix:** Add a runtime validation of allowed keys:
```python
_VALID_KEYS = set(AgentState.__annotations__)
unknown = set(overrides) - _VALID_KEYS - {"user_id"}  # user_id is dynamic
if unknown:
    raise ValueError(f"Unknown state keys: {unknown}")
```

---

### M-4. `dependencies.py:24-38` vs `database.py:45-62` -- Duplicated session lifecycle pattern

```python
# dependencies.py
async def provide_db_session() -> AsyncGenerator[AsyncSession, None]:
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

# database.py
@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
```

**Issue:** The session lifecycle logic is duplicated verbatim between `provide_db_session()` (used by Litestar DI for API routes) and `get_db_session()` (used by agents directly via `from src.core.database import get_db_session`). Both have identical commit-on-success / rollback-on-error / close-on-finally logic.

**Production risk:** If one is updated (e.g., adding savepoint support, changing commit behavior) the other is not. The two code paths serving the same purpose will diverge silently.

**Recommended fix:** `provide_db_session()` should delegate to `get_db_session()`:
```python
# dependencies.py
async def provide_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_db_session() as session:
        yield session
```

---

### M-5. `graph.py:75-191,280-373` -- Business logic embedded in graph wiring module

**Issue:** `bid_submission_node` (75-191, 117 lines) and `email_sending_node` (280-373, 93 lines) contain full business logic including credential loading, API calls, database writes, and error handling -- all defined inline in `graph.py` rather than in dedicated agent modules.

**Production risk:** The graph module is 1768 lines and mixing concerns: graph topology definition, routing logic, HITL resume orchestration, and now also execution of business operations. This makes the module harder to test in isolation (you cannot test `bid_submission_node` without importing the entire graph module with all its dependencies).

**Recommended fix:** Extract `bid_submission_node` into `src/agents/bid_submission.py` and `email_sending_node` into `src/agents/email_sender.py`, following the same pattern as the other agent nodes.

---

### M-6. `base.py:454-457` -- Iteration limit uses `retry_count` instead of dedicated iteration counter

```python
def _validate_role_constraints(self, state: AgentState) -> None:
    retry_count = state.get("retry_count", 0)
    if retry_count > self._agent_max_iterations:
        raise AgentException(
            f"Agent '{self.agent_name}' exceeded max iterations ({self._agent_max_iterations})",
```

**Issue:** The role constraint validation checks `retry_count` against `max_iterations`, but `retry_count` is incremented only on LLM or Agent errors (lines 328, 340). If an agent loops due to logic errors (not LLM failures), `retry_count` stays at 0 and the `max_iterations` check never triggers. The loop detector handles this separately, but the constraint check is misleading.

**Production risk:** False sense of security. The `max_iterations` field in `ROLE_CONSTRAINTS` suggests per-agent iteration budgets, but the enforcement mechanism is coupled to error retries, not actual iteration counts.

**Recommended fix:** Either rename the constraint to `max_error_retries` to match actual behavior, or introduce a separate per-agent invocation counter in the state (e.g., `_agent_invocation_counts: dict[str, int]`).

---

## LOW Findings

### L-1. `checkpoints.py:42-43` -- Class-level mutable `serde` shared across instances

```python
class HybridCheckpointSaver(BaseCheckpointSaver):
    serde = JsonPlusSerializer()
```

**Issue:** `serde` is a class-level attribute, meaning all instances share the same serializer. If `JsonPlusSerializer` has any mutable state (e.g., caches, registered custom types), concurrent graph executions using different checkpointer instances could interfere.

**Production risk:** Low in practice (JsonPlusSerializer appears stateless), but the pattern is fragile.

**Recommended fix:** Move to `__init__`:
```python
def __init__(self, ...):
    super().__init__(serde=JsonPlusSerializer())
```

---

### L-2. `container.py:78-79` -- HeartbeatMonitor created with `db_pool=None`

```python
self._heartbeat = HeartbeatMonitor(
    valkey=get_valkey(),
    db_pool=None,
)
```

**Issue:** The HeartbeatMonitor is created without a database pool. If heartbeat logic needs to write health status to the database (e.g., for dashboard display or dead-letter recovery), it silently cannot.

**Production risk:** Heartbeat data is only in Valkey (volatile). If Valkey restarts, all heartbeat state is lost with no DB fallback.

**Recommended fix:** Pass the database pool if available, or document that this is intentional.

---

### L-3. `state.py:19` -- `Platform` literal does not include "outreach"

```python
Platform = Literal["freelancer", "upwork", "flru", "kwork"]
```

But in `graph.py:1225`:
```python
project = ProjectContext(
    ...
    platform="outreach",  # Not in Platform literal
)
```

**Issue:** Pipeline B creates ProjectContext with `platform="outreach"` which is not a valid value per the `Platform` type. This passes at runtime because TypedDict does no runtime validation, but type checkers would flag it.

**Production risk:** Minimal, but indicates the type system is not accurately modeling the domain.

**Recommended fix:** Add `"outreach"` to the `Platform` literal, or use a separate union type for pipeline types.

---

## Actionable Summary (Priority Order)

1. **C-1:** Fix state mutation in `_route_after_critic` -- move counter logic to Critic agent
2. **H-5:** Change unknown HITL actions from "approve" default to error/reject
3. **H-3:** Use `JsonPlusSerializer` for checkpoint serialization instead of `json.dumps(default=str)`
4. **H-2:** Wrap `_pg_put` dual-insert in a transaction
5. **H-1:** Replace `ainvoke()` with `astream()` in convenience runners per documented workaround
6. **C-2/C-3:** Refactor `database.py` to use lazy initialization
7. **M-3:** Add runtime key validation to `update_state`
8. **M-4:** Deduplicate session lifecycle between `dependencies.py` and `database.py`
9. **M-5:** Extract inline business logic nodes from `graph.py`
10. **M-2:** Fix `artifacts` type annotation to `dict[str, Any]`
