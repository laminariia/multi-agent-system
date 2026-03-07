# Performance & Async Patterns Code Review

**Date:** 2026-02-23
**Scope:** 9 files across `src/api/routes/`, `src/core/`, `src/adapters/`, `src/worker/`
**Focus:** N+1 queries, async anti-patterns, race conditions, resource leaks, scalability

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3     |
| HIGH     | 6     |
| MEDIUM   | 7     |
| LOW      | 4     |

---

## CRITICAL Findings

### C1. `_pg_search` ignores `query_type` parameter -- cross-type cache collisions

**File:** `/Users/awon/programming/projects/MAS/src/core/semantic_cache.py`, lines 291-310
**Severity:** CRITICAL

The `_pg_search` method accepts `query_type` as a parameter but never uses it in the SQL query:

```python
async def _pg_search(self, query_embedding: np.ndarray, query_type: str) -> str | None:
    # ...
    row = await conn.fetchrow(
        """
        SELECT id, response, 1 - (embedding <=> $1::vector) AS similarity
        FROM semantic_cache
        WHERE expires_at > NOW()
        ORDER BY embedding <=> $1::vector
        LIMIT 1
        """,
        json.dumps(embedding_list),
    )
```

**Impact:** A "scout" agent query could match a cached "content" agent response (or any other type) because the PostgreSQL cold layer performs a global nearest-neighbor search with no type filter. This silently returns wrong cached results, causing agents to produce incorrect outputs.

**Recommended fix:** Add `AND query_type = $2` to the WHERE clause and pass `query_type` as the second parameter:

```python
row = await conn.fetchrow(
    """
    SELECT id, response, 1 - (embedding <=> $1::vector) AS similarity
    FROM semantic_cache
    WHERE expires_at > NOW()
      AND query_type = $2
    ORDER BY embedding <=> $1::vector
    LIMIT 1
    """,
    json.dumps(embedding_list),
    query_type,
)
```

---

### C2. `run_project_pipeline` creates a new asyncpg pool per invocation -- connection exhaustion

**File:** `/Users/awon/programming/projects/MAS/src/worker/tasks.py`, lines 87-96
**Severity:** CRITICAL

```python
async def run_project_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    # ...
    db_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=5)
    try:
        graph = create_graph_with_persistence(valkey=valkey, db_pool=db_pool, planner_pipeline=True)
        # ...
    finally:
        await db_pool.close()
```

Every call to `run_project_pipeline` opens a fresh connection pool (1-5 connections). This task is invoked as a background `asyncio.create_task` from `jobs.py:run-pipeline` (line 335). If multiple pipelines run concurrently (e.g., 10 jobs triggered in succession), that is 10-50 new PostgreSQL connections on top of the existing SQLAlchemy pool (10 + 20 overflow). PostgreSQL's default `max_connections` is 100; this can easily exhaust the connection limit, causing `too many connections` errors across the entire application.

**Impact:** Database connection exhaustion under moderate concurrency. All API routes and background tasks fail simultaneously.

**Recommended fix:** Use a shared application-level asyncpg pool (created once at startup, similar to the SQLAlchemy engine), or reuse the existing SQLAlchemy async engine's pool. Pass the shared pool into `run_project_pipeline` rather than creating a new one.

---

### C3. `asyncio.sleep` while holding `asyncio.Lock` in rate limiter -- head-of-line blocking

**File:** `/Users/awon/programming/projects/MAS/src/adapters/rate_limiter.py`, lines 186-213
**Severity:** CRITICAL

```python
async def acquire(self, platform: str) -> None:
    lock = self._get_lock(platform)
    async with lock:                           # Lock acquired
        # ...
        if elapsed < min_interval:
            wait_time = min_interval - elapsed
            await asyncio.sleep(wait_time)     # Sleeping while holding lock!

        state.last_request_ts = time.monotonic()
        state.total_requests += 1
```

The method acquires the per-platform `asyncio.Lock`, then sleeps for up to `60 / min_rpm` seconds (e.g., 60 seconds for kwork at min_rpm=1.0) while holding it. During the sleep, ALL other coroutines waiting to `acquire` the same platform are blocked. This serializes all requests per platform to one-at-a-time with the full sleep interval added.

**Impact:** If 10 coroutines call `acquire("kwork")` concurrently, the 10th waits `10 * min_interval` seconds (could be minutes). This creates a convoy effect and massive latency spikes. Under load, timeouts cascade upstream.

**Recommended fix:** Use an `asyncio.Semaphore` or event-based approach where the lock only protects the state mutation (checking/updating `last_request_ts`), and the sleep happens outside the lock:

```python
async def acquire(self, platform: str) -> None:
    lock = self._get_lock(platform)
    async with lock:
        state = self._get_state(platform)
        now = time.monotonic()
        if state.paused_until > now:
            raise PlatformBannedError(...)
        if state.last_request_ts > 0 and state.current_rpm > 0:
            min_interval = 60.0 / state.current_rpm
            elapsed = now - state.last_request_ts
            wait_time = max(0, min_interval - elapsed)
        else:
            wait_time = 0
        state.last_request_ts = time.monotonic() + wait_time
        state.total_requests += 1
    # Sleep OUTSIDE the lock
    if wait_time > 0:
        await asyncio.sleep(wait_time)
```

---

## HIGH Findings

### H1. HITL `stats` endpoint fires 4 sequential DB queries with no caching

**File:** `/Users/awon/programming/projects/MAS/src/api/routes/hitl.py`, lines 545-613
**Severity:** HIGH

The `stats` endpoint executes 4 separate queries in sequence:
1. `pending_count` (line 556-560)
2. `resolved_count` (line 562-566)
3. `expired_count` (line 568-572)
4. `avg_resolution_time` (line 575-580)
5. `per-type breakdown` (line 584-593)

That is actually 5 sequential round-trips to the database on every call. The `stats` endpoint is typically polled by the dashboard (e.g., every 5-10 seconds).

**Impact:** Under load, each poll adds 5 DB round-trips. With 5 concurrent dashboard users polling every 5 seconds, that is 5 queries/req * 1 req/5s * 5 users = 5 queries/second just for stats. Latency stacks up due to sequential execution.

**Recommended fix:**
1. Combine the today's counts (pending, resolved, expired) into a single query using conditional aggregation:
```sql
SELECT
    COUNT(*) FILTER (WHERE status = 'pending') AS pending,
    COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
    COUNT(*) FILTER (WHERE status = 'expired') AS expired
FROM hitl_queue WHERE created_at >= $1
```
2. Run the remaining 2 queries (avg resolution, per-type) concurrently using `asyncio.gather`.
3. Add short-lived caching (10-30 seconds) since stats do not need to be real-time.

---

### H2. `list_pending` fires 3 sequential queries when 2 would suffice

**File:** `/Users/awon/programming/projects/MAS/src/api/routes/hitl.py`, lines 154-200
**Severity:** HIGH

```python
# Query 1: total count
count_stmt = select(func.count()).select_from(base.subquery())
total = (await db_session.execute(count_stmt)).scalar_one()

# Query 2: urgent count
urgent_stmt = select(func.count()).select_from(base.where(HITLQueue.priority == "urgent").subquery())
pending_urgent = (await db_session.execute(urgent_stmt)).scalar_one()

# Query 3: actual data
items_stmt = base.order_by(priority_order, ...).limit(limit).offset(offset)
result = await db_session.execute(items_stmt)
```

**Impact:** 3 serial DB round-trips per list request. High-frequency dashboard polling amplifies latency.

**Recommended fix:** Merge count and urgent count into one query using conditional aggregation:
```sql
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE priority = 'urgent') AS urgent
FROM (base_subquery)
```

---

### H3. `list_jobs` count query includes `selectinload(Job.bids)` unnecessarily

**File:** `/Users/awon/programming/projects/MAS/src/api/routes/jobs.py`, lines 88-101
**Severity:** HIGH

```python
stmt = select(Job).options(selectinload(Job.bids))
# ...
count_stmt = select(func.count()).select_from(stmt.subquery())
total = (await db_session.execute(count_stmt)).scalar_one()
```

The `selectinload(Job.bids)` is included in the base `stmt` that is used for both the count subquery and the data fetch. While SQLAlchemy may optimize the count path, the subquery still carries the relationship loading option. More importantly, the `stmt` is then re-used with `.order_by().limit().offset()` which correctly triggers `selectinload`. However, creating the count from the same statement with `selectinload` can lead to unexpected behavior with subquery materialization in some SQLAlchemy/PostgreSQL scenarios.

**Impact:** Potentially wasteful count query. The eager load option in the subquery context adds unnecessary complexity.

**Recommended fix:** Build a separate base query without `selectinload` for the count, and add `selectinload` only to the data-fetching query:

```python
base = select(Job)
# ... apply filters to base ...
count_stmt = select(func.count()).select_from(base.subquery())
data_stmt = base.options(selectinload(Job.bids)).order_by(order).limit(limit).offset(offset)
```

---

### H4. `_chat_model_cache` in LLMClient is not thread-safe and grows unbounded

**File:** `/Users/awon/programming/projects/MAS/src/core/llm_client.py`, lines 301, 614-655
**Severity:** HIGH

```python
def _get_or_create_model(self, model_key, spec, temperature, max_tokens):
    cache_key = f"{model_key}:{temperature}:{max_tokens}"
    if cache_key in self._chat_model_cache:
        return self._chat_model_cache[cache_key]
    # ... create model ...
    self._chat_model_cache[cache_key] = model
    return model
```

The cache key includes `temperature` and `max_tokens`. Since `temperature` is a float and `max_tokens` can vary per call, the cache grows without bound. Each unique combination creates a new `ChatOpenAI` instance (which holds an HTTP connection pool internally).

**Impact:** Memory leak proportional to unique `(model, temperature, max_tokens)` combinations. Each `ChatOpenAI` instance holds an `httpx.AsyncClient` with its own connection pool, so this also leaks file descriptors and connections.

**Recommended fix:** Either (a) use `functools.lru_cache` with a max size, or (b) normalize temperature to 1 decimal place and max_tokens to known tiers, or (c) cap the cache size with an LRU eviction policy.

---

### H5. Double embedding computation on cache miss in `SemanticCache.get` + `LLMClient.call`

**File:** `/Users/awon/programming/projects/MAS/src/core/semantic_cache.py`, lines 142-182; `/Users/awon/programming/projects/MAS/src/core/llm_client.py`, lines 347-428
**Severity:** HIGH

When `SemanticCache.get()` is called (line 153), it computes an embedding via `self._embed(query)` -- an OpenAI API call costing ~$0.00013/query for 3072-dim. If the cache misses, `LLMClient.call()` proceeds to call the LLM, and then calls `SemanticCache.set()` (line 424) which computes the embedding AGAIN (line 179):

```python
async def set(self, query, response, query_type="default"):
    query_embedding = await self._embed(query)   # Second embedding call!
    await self._valkey_store(query, response, query_type, query_embedding)
    await self._pg_store(query, response, query_type, query_embedding)
```

**Impact:** Every cache miss results in 2 embedding API calls for the same text. At scale (hundreds of LLM calls/hour), this doubles embedding costs and adds ~200-500ms extra latency per miss.

**Recommended fix:** Return the computed embedding from `get()` so `set()` can reuse it:
```python
async def get(self, query, query_type) -> tuple[str | None, np.ndarray]:
    embedding = await self._embed(query)
    # ... search with embedding ...
    return result, embedding

async def set(self, query, response, query_type, embedding=None):
    if embedding is None:
        embedding = await self._embed(query)
    # ... store ...
```

---

### H6. `record_success` / `record_rate_limit` methods in AdaptiveRateLimiter are not locked

**File:** `/Users/awon/programming/projects/MAS/src/adapters/rate_limiter.py`, lines 219-255
**Severity:** HIGH

```python
async def record_success(self, platform: str) -> None:
    state = self._get_state(platform)
    state.consecutive_successes += 1       # Not atomic
    state.consecutive_failures = 0
    if state.consecutive_successes % 5 == 0:
        new_rpm = min(state.current_rpm * _SUCCESS_INCREASE, state.max_rpm)
        state.current_rpm = new_rpm        # Race condition
    await self._save_state(platform)
```

The `record_success` and `record_rate_limit` methods mutate shared state (`consecutive_successes`, `current_rpm`) without acquiring the per-platform lock. If two concurrent requests for the same platform both call `record_success` simultaneously, `consecutive_successes` can be incremented inconsistently (lost updates), and the RPM adjustment logic can be skipped or applied multiple times.

**Impact:** Rate limiter state becomes inconsistent under concurrency. RPM may not adjust correctly, leading to either throttled performance (too slow) or ban risk (too fast).

**Recommended fix:** Wrap state mutations in the per-platform lock:
```python
async def record_success(self, platform: str) -> None:
    async with self._get_lock(platform):
        state = self._get_state(platform)
        state.consecutive_successes += 1
        # ...
    await self._save_state(platform)
```

---

## MEDIUM Findings

### M1. `_save_state` and `_load_state` are not atomic in Valkey -- state can be partially written

**File:** `/Users/awon/programming/projects/MAS/src/adapters/rate_limiter.py`, lines 123-142
**Severity:** MEDIUM

```python
async def _save_state(self, platform: str) -> None:
    await self._valkey.hset(key, mapping=data)    # Write fields
    await self._valkey.expire(key, 86400)          # Set TTL
```

Two separate Valkey commands without a pipeline/transaction. If the process crashes between `hset` and `expire`, the key loses its TTL and persists indefinitely. More critically, if `_load_state` reads during a concurrent `_save_state`, it can see a partially updated hash (e.g., new `current_rpm` but old `consecutive_successes`).

**Impact:** State inconsistency on crashes or under concurrency. Stale rate limiter state in Valkey can accumulate.

**Recommended fix:** Use a Valkey pipeline to make `hset` + `expire` atomic:
```python
pipe = self._valkey.pipeline()
pipe.hset(key, mapping=data)
pipe.expire(key, 86400)
await pipe.execute()
```

---

### M2. `invalidate_by_type` uses SCAN + individual DELETE -- O(N) per key

**File:** `/Users/awon/programming/projects/MAS/src/core/semantic_cache.py`, lines 184-210
**Severity:** MEDIUM

```python
async for key in self.valkey.scan_iter(f"{VALKEY_PREFIX}*"):
    entry_type = await self.valkey.hget(key, "query_type")
    if decoded == query_type:
        await self.valkey.delete(key)
```

For each key in the cache, this does: 1 HGET + potentially 1 DELETE. With 10,000 cache entries, that is 10,000-20,000 Valkey round-trips. The same issue exists in `flush()` (lines 214-216).

**Impact:** Cache invalidation can take seconds to minutes with large caches, blocking the calling coroutine.

**Recommended fix:** Use a Valkey pipeline to batch HGET calls, then batch DELETE calls. Or better, use RediSearch to find matching keys by `query_type` field and delete them in bulk:
```python
# Batch with pipeline
pipe = self.valkey.pipeline()
keys = []
async for key in self.valkey.scan_iter(f"{VALKEY_PREFIX}*"):
    keys.append(key)
    pipe.hget(key, "query_type")
results = await pipe.execute()
to_delete = [k for k, t in zip(keys, results) if t and (t.decode() if isinstance(t, bytes) else t) == query_type]
if to_delete:
    await self.valkey.delete(*to_delete)
```

---

### M3. `_pg_search` acquires 2 separate connections from pool -- double pool pressure

**File:** `/Users/awon/programming/projects/MAS/src/core/semantic_cache.py`, lines 297-328
**Severity:** MEDIUM

```python
async with self.db_pool.acquire() as conn:
    row = await conn.fetchrow(...)              # Connection 1

# ... check row ...

async with self.db_pool.acquire() as conn:     # Connection 2
    await conn.execute("UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = $1", row["id"])
```

The search and the hit-count update are in separate `acquire()` blocks. Under pool pressure, the second `acquire()` may wait, adding latency. In the worst case, if all pool connections are exhausted by concurrent `_pg_search` calls, the `UPDATE` calls wait indefinitely, causing a deadlock-like stall.

**Impact:** Under high concurrency, pool starvation risk. Unnecessary latency from two pool round-trips.

**Recommended fix:** Combine into one connection:
```python
async with self.db_pool.acquire() as conn:
    row = await conn.fetchrow(...)
    if row and float(row["similarity"]) >= self.similarity_threshold:
        await conn.execute("UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = $1", row["id"])
        return str(row["response"])
return None
```

---

### M4. Module-level engine and pool creation at import time

**File:** `/Users/awon/programming/projects/MAS/src/core/database.py`, lines 26-73
**Severity:** MEDIUM

```python
_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.async_database_url,
    pool_size=10,
    # ...
)

_valkey_pool: aioredis.ConnectionPool = aioredis.ConnectionPool.from_url(
    _settings.valkey_redis_url,
    # ...
)
```

Both the SQLAlchemy engine and Valkey connection pool are created at module import time. This means:
1. Importing `src.core.database` anywhere (even in tests) triggers real connection setup.
2. `get_settings()` is called at import time -- any missing environment variables cause immediate ImportError in any module that transitively imports `database`.
3. The engine/pool cannot be reconfigured or mocked without monkeypatching module globals.

**Impact:** Test isolation difficulties, startup failures in CI environments without a running database, and inability to cleanly reconfigure connections.

**Recommended fix:** Use lazy initialization (factory pattern or Litestar dependency injection). Create engine/pool on first access rather than at import.

---

### M5. `pool_size=10, max_overflow=20` may be insufficient for background tasks

**File:** `/Users/awon/programming/projects/MAS/src/core/database.py`, lines 28-36
**Severity:** MEDIUM

The pool allows 10 steady-state + 20 overflow = 30 max connections. The application has:
- API routes (concurrent HTTP requests)
- Background tasks (`run_project_pipeline` creates its own pool -- see C2)
- Scheduler jobs (`_collect_metrics`, `_run_scout_cycle`)
- WebSocket connections

A single API request can hold a connection for the entire request duration (Litestar injects `AsyncSession` per request). With 30 concurrent API requests, the pool is exhausted.

**Impact:** Under moderate load, new requests block on `pool_timeout=30` and eventually get `TimeoutError`. Combined with C2 (additional pools), the risk is amplified.

**Recommended fix:** Consider increasing to `pool_size=20, max_overflow=30` or making these configurable via settings. Fix C2 to use the shared pool.

---

### M6. `CostTracker.records` grows unbounded in long-running processes

**File:** `/Users/awon/programming/projects/MAS/src/core/llm_client.py`, lines 171-195
**Severity:** MEDIUM

```python
@dataclass
class CostTracker:
    records: list[CallMetrics] = field(default_factory=list)

    def record(self, metrics: CallMetrics) -> None:
        self.records.append(metrics)
```

Every LLM call appends to `records`. In a long-running production process (the API server runs for days/weeks), this list grows unbounded. Each `CallMetrics` is ~200 bytes, so 100K LLM calls = ~20MB of metrics accumulated in memory that are never flushed.

**Impact:** Gradual memory growth over days. The `total_cost_usd` and `by_agent()` properties iterate the full list, becoming increasingly slow.

**Recommended fix:** Add periodic flushing to a persistent store (DB or metrics system), or use a bounded deque, or emit metrics via Prometheus (which is already partially done on line 518-528) and keep only recent records.

---

### M7. `time.monotonic()` used for `paused_until` is not portable across restarts

**File:** `/Users/awon/programming/projects/MAS/src/adapters/rate_limiter.py`, line 263
**Severity:** MEDIUM

```python
state.paused_until = time.monotonic() + _BAN_PAUSE_SECONDS
```

`time.monotonic()` returns a relative time since process start. This value is persisted to Valkey (in `_save_state`), but after a process restart, `time.monotonic()` resets to a different epoch. A saved `paused_until = 500000.0` might be compared against `now = 50.0` after restart, causing the ban to appear still active for an impossibly long duration -- or the ban might be immediately expired if the new monotonic value exceeds the old one.

**Impact:** After restart, ban pauses are either prematurely expired or incorrectly extended, depending on the new monotonic clock value.

**Recommended fix:** Use `time.time()` (wall clock) for `paused_until` since it is persisted to Valkey, or convert to absolute UTC timestamps before saving.

---

## LOW Findings

### L1. `httpx.AsyncClient` created per credential test -- no connection reuse

**File:** `/Users/awon/programming/projects/MAS/src/api/routes/settings.py`, lines 597-601
**Severity:** LOW

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    resp = await client.get(url, headers=headers)
```

A new `httpx.AsyncClient` (with TLS handshake, TCP connection) is created for each credential test call. Since this is a user-triggered action (not a hot path), the impact is minimal.

**Impact:** ~100-300ms extra latency per test due to connection establishment.

**Recommended fix:** Acceptable as-is given low frequency. If testing multiple keys in sequence, consider a shared client.

---

### L2. `_background_resume_tasks` set has no size limit

**File:** `/Users/awon/programming/projects/MAS/src/api/routes/hitl.py`, lines 47, 350-351
**Severity:** LOW

```python
_background_resume_tasks: set[Any] = set()
# ...
_background_resume_tasks.add(task)
task.add_done_callback(_background_resume_tasks.discard)
```

The `done_callback` cleans up after each task completes, so the set only holds in-flight tasks. This is actually the correct pattern for preventing GC of fire-and-forget tasks. However, if `resume_from_hitl` hangs indefinitely (no timeout), the set grows with zombie tasks.

**Impact:** Negligible under normal conditions. Could accumulate if pipeline resumption hangs.

**Recommended fix:** Add a timeout wrapper around `_resume()`:
```python
task = asyncio.create_task(asyncio.wait_for(_resume(...), timeout=600))
```

---

### L3. `list_pending` does not have a database index optimized for priority ordering

**File:** `/Users/awon/programming/projects/MAS/src/api/routes/hitl.py`, lines 170-178
**Severity:** LOW

The query orders by a computed `CASE(priority)` expression then `created_at DESC`. The HITL model (from models.py) likely has an index on `status` but no composite index that covers `(status, priority, created_at)`.

**Impact:** As the HITL table grows (thousands of pending items), the sort becomes increasingly expensive -- filesort on every request.

**Recommended fix:** Add a composite index:
```sql
CREATE INDEX idx_hitl_pending_sort ON hitl_queue (status, priority, created_at DESC)
WHERE status = 'pending';
```

---

### L4. `_collect_metrics` only tracks 2 HITL types

**File:** `/Users/awon/programming/projects/MAS/src/worker/scheduler.py`, lines 222-230
**Severity:** LOW

```python
for hitl_type in ("bid_approval", "final_review"):
    stmt = select(func.count()).where(
        HITLQueue.status == "pending",
        HITLQueue.type == hitl_type,
    )
```

Only `bid_approval` and `final_review` are tracked. The system has 10 HITL types (defined in `_NEXT_ACTION_MAP` in hitl.py). Missing types (`code_review`, `plan_review`, `email_approval`, etc.) are invisible in metrics.

**Impact:** Incomplete observability. Pending items of untracked types go unnoticed.

**Recommended fix:** Either iterate over all known HITL types, or use a single query with `GROUP BY type`:
```python
stmt = select(HITLQueue.type, func.count()).where(
    HITLQueue.status == "pending"
).group_by(HITLQueue.type)
```

---

## Architecture Notes (not bugs, but worth tracking)

1. **Distributed lock pattern in scheduler** (lines 139-154, 185-206) is correct but uses `nx=True, ex=300` without renewal. If the scout cycle takes longer than 300 seconds, the lock expires and a second instance can start overlapping. Consider using a self-renewing lock (e.g., redlock with extension).

2. **`graph.ainvoke` vs `astream`** -- Per `debugging.md`, `ainvoke()` may return initial state instead of final state for `StateGraph(dict)` in LangGraph 1.0.8. The `run_project_pipeline` task (tasks.py line 94) uses `ainvoke`, which may silently lose the final pipeline state. The `run_pipeline_b_scan` (tasks.py line 186) has the same issue.

3. **No backpressure on fire-and-forget tasks** -- Both `hitl.py` (resolve endpoint) and `jobs.py` (scan/pipeline endpoints) spawn background tasks via `asyncio.create_task` with no concurrency limit. A burst of HITL resolutions or pipeline triggers could spawn dozens of concurrent graph executions, each with their own DB pools (C2), LLM calls, and memory usage.
