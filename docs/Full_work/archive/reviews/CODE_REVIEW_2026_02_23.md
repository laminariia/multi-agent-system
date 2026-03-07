# Code Review: MAS v4.2.0

**Date:** 2026-02-23
**Reviewer:** Claude Opus 4.6 (7 parallel research agents)
**Scope:** Full codebase — security, architecture, performance, LLM/agents, testing, frontend, infrastructure
**Methodology:** 7 independent agents analyzed ~120 files, findings deduplicated and cross-validated

---

## Executive Summary

| Severity | Count | Description |
|----------|-------|-------------|
| CRITICAL | 15 | Must fix before next production deploy |
| HIGH | 26 | Fix within 1 sprint |
| MEDIUM | 30 | Fix within 2 sprints |
| LOW | 14 | Backlog |
| **Total** | **85** | Unique findings after deduplication |

**Top 3 systemic risks:**
1. **Auth token lifecycle** — blacklist bypass, no refresh rotation, localStorage storage (Findings S-1, S-2, S-3, S-6)
2. **Prompt injection surface** — zero resistance in all 8 prompts, unsanitized external content flows directly to LLM (Findings A-1, A-7, A-8)
3. **Resource exhaustion under load** — new DB pool per pipeline, asyncio.Lock held during sleep, unbounded caches (Findings P-2, P-3, P-4)

---

## CRITICAL Findings (15)

### S-1. Token blacklist bypass when Valkey is down
- **File:** `src/api/guards.py:95-102`
- **Area:** Security
- **Issue:** `except Exception` silently passes when Valkey is unreachable — all blacklisted (logged-out) tokens accepted. Railway restarts clear Valkey entirely.
- **Fix:** Fail closed — deny access when Valkey is unreachable. Add PostgreSQL as secondary blacklist store.

### A-1. `_route_after_critic` mutates state in routing function
- **File:** `src/core/graph.py:627-628`
- **Area:** Architecture
- **Issue:** Routing function mutates `state["artifacts"]` directly. LangGraph routing functions must be pure. Mutation bypasses checkpoint persistence — revision counter may not persist, enabling infinite revision loops.
- **Fix:** Move counter increment into Critic's `_execute()` method.

### A-2. Module-level eager initialization of DB engine and Valkey pool
- **File:** `src/core/database.py:26-36, 69-73`
- **Area:** Architecture
- **Issue:** `create_async_engine()` and `ConnectionPool.from_url()` execute at import time. Breaks tests, CI without `.env`, prevents lifecycle control via Litestar hooks.
- **Fix:** Lazy initialization via factory pattern or Litestar dependency injection.

### A-3. Unknown HITL action defaults to "approve"
- **File:** `src/core/graph.py:1571-1582` (and 3 other `_apply_*` functions)
- **Area:** Architecture
- **Issue:** All 4 HITL approval functions treat unknown actions as "approve". Typo (`"approv"`, `""`, `"skip"`) silently approves bids, plans, deliveries, or email campaigns.
- **Fix:** Default to reject/error. Add explicit action validation with allowlist.

### P-1. Semantic cache ignores `query_type` — cross-agent cache collisions
- **File:** `src/core/semantic_cache.py:291-310`
- **Area:** Performance
- **Issue:** `_pg_search` accepts `query_type` but never uses it in SQL WHERE clause. Scout query could match cached Content response.
- **Fix:** Add `AND query_type = $2` to the SQL query.

### P-2. New asyncpg pool created per pipeline invocation — connection exhaustion
- **File:** `src/worker/tasks.py:87-96`
- **Area:** Performance
- **Issue:** Each `run_project_pipeline` call opens 1-5 new PostgreSQL connections. 10 concurrent pipelines = 50 connections on top of SQLAlchemy's 30. PostgreSQL default `max_connections=100` exhausted.
- **Fix:** Use shared application-level asyncpg pool, created once at startup.

### P-3. `asyncio.sleep` while holding Lock — head-of-line blocking
- **File:** `src/adapters/rate_limiter.py:186-213`
- **Area:** Performance
- **Issue:** Per-platform `asyncio.Lock` held during sleep (up to 60s for kwork). 10 concurrent requests → 10th waits 10 × min_interval. Convoy effect and cascade timeouts.
- **Fix:** Sleep outside the lock. Lock only protects state mutation.

### A-4. `json.dumps(default=str)` in checkpoint saver corrupts state on resume
- **File:** `src/core/checkpoints.py:205, 220, 267`
- **Area:** Architecture
- **Issue:** `datetime`, `UUID`, `BaseMessage` objects irreversibly converted to strings. After checkpoint resume, state is subtly corrupted (string "2026-02-23T..." instead of datetime object).
- **Fix:** Use `JsonPlusSerializer` (already defined but unused) which handles LangGraph types correctly.

### A-5. Two INSERT without transaction in `_pg_put`
- **File:** `src/core/checkpoints.py:273-307`
- **Area:** Architecture
- **Issue:** Upsert into `langgraph_checkpoints` + insert into `langgraph_checkpoint_history` not wrapped in `async with conn.transaction()`. If second INSERT fails, checkpoint and history diverge.
- **Fix:** Wrap both operations in a transaction.

### L-1. No bid amount bounds validation
- **File:** `src/agents/bid.py:370-373`
- **Area:** LLM/Agents
- **Issue:** `bid_amount` cast to float but no min/max check. LLM could return $0.01 or $999,999. No cross-validation against job budget range.
- **Fix:** Add `if bid_amount < 5.0 or bid_amount > 50000.0: return None`. Cross-validate against job's budget.

### L-2. Empty exception tuples silently disable retry-on-rate-limit
- **File:** `src/core/llm_client.py:201-241, 544`
- **Area:** LLM/Agents
- **Issue:** `_RATE_LIMIT_EXCEPTIONS = ()` if import fails silently. `except ()` never matches. Rate-limit errors become non-retryable — system exhausts all providers on first 429.
- **Fix:** Add fallback string-based 429 detection. Log warning if no exception classes loaded.

### L-3. HeartbeatTimeoutError aborts monitoring of remaining agents
- **File:** `src/core/heartbeat.py:238-246, 273`
- **Area:** LLM/Agents
- **Issue:** `_handle_timeout` raises exception that propagates out of `for` loop in `_check_timeouts`. Remaining agents not checked in that cycle. Multi-agent failure detection delayed by N × 30s.
- **Fix:** Wrap per-agent handling in try/except inside the loop.

### T-1. Integration test graph diverges from production graph
- **File:** `tests/integration/test_full_pipeline.py:58-183`
- **Area:** Testing
- **Issue:** Test builds its own graph missing `bid_submission_node`, different HITL routing, no revision cycle limits. Bugs in production routing pass these tests.
- **Fix:** Use production graph builder with mock node functions, or delete in favor of `test_production_graph.py`.

### T-2. `_mock_semgrep_gate` autouse hides real failures
- **File:** `tests/conftest.py:229-256`
- **Area:** Testing
- **Issue:** Every test runs with `blocked=False` and `exit_code=0`. Critic's "block on critical findings" path completely untestable. Dev's sandbox error handling invisible.
- **Fix:** Remove `autouse=True`. Apply fixture explicitly where needed.

### L-3a. Outreach prompt inline without CAN-SPAM/GDPR guardrails
- **File:** `src/agents/outreach.py:47-65`
- **Area:** LLM/Agents
- **Issue:** Unlike all other agents, Outreach system prompt is defined inline (no `src/prompts/outreach.py`). Lacks CAN-SPAM compliance rules, GDPR awareness, anti-phishing constraints, and warm-up protocol references. No `src/prompts/geo_scout.py` either.
- **Fix:** Create dedicated prompt files with legal compliance guardrails matching other agents' pattern.

### F-1. Token refresh race condition — concurrent 401s cause logout
- **File:** `dashboard/app/lib/api.ts:95-172`
- **Area:** Frontend
- **Issue:** No deduplication on `refreshAccessToken()`. 7+ parallel React Query requests all get 401 → all call refresh independently. If backend invalidates on use, 6 of 7 fail → `clearAuth()` → unexpected logout.
- **Fix:** Wrap `refreshAccessToken` in a shared promise (deduplication pattern).

---

## HIGH Findings (26)

### Security (5)

| # | Finding | File | Issue |
|---|---------|------|-------|
| S-2 | Refresh token not blacklisted on rotation | `auth.py:207-254` | Old refresh token valid for 7 days after exchange |
| S-3 | Tokens in localStorage | `auth-store.ts:15-48` | XSS reads both access + refresh tokens |
| S-4 | Fixed PBKDF2 salt | `encryption.py:37` | Hardcoded salt enables rainbow table on code leak |
| S-5 | `lru_cache` on Fernet | `encryption.py:62-84` | Emergency key rotation requires process restart |
| S-6 | Refresh endpoint skips blacklist check | `auth.py:225-232` | Even with blacklisting, refresh endpoint ignores it |

### Architecture (2)

| # | Finding | File | Issue |
|---|---------|------|-------|
| A-6 | `ainvoke()` instead of `astream()` | `graph.py:1148,1193,1248` | Own debugging.md documents this returns initial state in LangGraph 1.0.8 |
| A-7 | `_SUBMIT_GUARD` declared but never checked | `upwork.py:30` | Provides zero runtime protection against Upwork bid submission |

### Performance (4)

| # | Finding | File | Issue |
|---|---------|------|-------|
| P-4 | `_chat_model_cache` grows unbounded | `llm_client.py:614-655` | Each unique `(model, temp, max_tokens)` creates new `ChatOpenAI` with httpx pool |
| P-5 | Double embedding on cache miss | `semantic_cache.py:142-182` | `get()` and `set()` both call `_embed()` — doubles API cost per miss |
| P-6 | `record_success/record_rate_limit` not locked | `rate_limiter.py:219-255` | State mutations without per-platform lock — lost updates under concurrency |
| P-7 | HITL stats fires 5 sequential DB queries | `hitl.py:545-613` | Dashboard polls every 5-10s, 5 round-trips each. Combine with conditional aggregation |

### LLM/Agents (7)

| # | Finding | File | Issue |
|---|---------|------|-------|
| A-8 | No prompt injection resistance in any prompt | `src/prompts/*.py` | Zero defense against adversarial job descriptions from public platforms |
| A-9 | Unsanitized external content in LLM calls | `scout.py:240-249`, `bid.py:324` | Job descriptions passed raw to HumanMessage — direct injection pipeline |
| A-10 | Scout dedup query cartesian product | `scout.py:225-228` | Two separate IN clauses create false matches across platforms |
| A-11 | No programmatic guard against Upwork bids | `bid.py:86-158` | HITL is only safety net; reviewer might miss platform is Upwork |
| A-12 | Credential leakage in multi-user concurrent scenarios | `llm_client.py:625`, `base.py:495-512` | Singleton LLMClient + `update_credentials()` = User A gets User B's model |
| A-13 | Loop detector memory leak | `loop_detector.py:56,85` | `_histories` dict grows unbounded — completed threads never cleaned |
| A-14 | Dead agent `last_heartbeat_ts` always 0.0 | `heartbeat.py:261-277` | Agent popped from dict before timestamp read for error |

### Testing (4)

| # | Finding | File | Issue |
|---|---------|------|-------|
| T-3 | Property tests empty (hypothesis dead dep) | `tests/property/` | Zero property tests despite hypothesis in deps |
| T-4 | Golden set missing for 4 agents | `tests/golden_set/` | dev, design, packager, geo_scout have no golden tests |
| T-5 | Zero test coverage for telegram_listener | `src/services/telegram_listener.py` | 252 lines, Telethon MTProto listener, completely untested |
| T-6 | Dashboard has zero frontend tests | `dashboard/package.json` | No vitest/jest/testing-library. Auth, HITL approval, credentials untested |

### Frontend (3)

| # | Finding | File | Issue |
|---|---------|------|-------|
| F-2 | WebSocket event listener leak | `use-ws-log-stream.ts:121-150` | Listeners added but never removed on cleanup. Hundreds accumulate on navigation |
| F-3 | No ErrorBoundary in `_app.tsx` layout | `_app.tsx` | Errors in layout (WS, auth) cause full white screen |
| F-4 | Auth guard hydration timing issue | `_app.tsx` | `useEffect` + Zustand `persist` → flash redirect to /login on hard refresh |

### Infrastructure (2)

| # | Finding | File | Issue |
|---|---------|------|-------|
| I-1 | `submit_bid` called without required `period` parameter | `graph.py:115-118` | Every Freelancer bid submission crashes with `TypeError` |
| I-2 | XML entity expansion in fl_ru.py | `fl_ru.py:271` | `ElementTree.fromstring()` without `defusedxml` — XXE attack vector |

---

## MEDIUM Findings (30)

### Security (3)
- **S-7.** No CSP header in SecurityHeadersMiddleware (`main.py:75-101`)
- **S-8.** `dangerouslySetInnerHTML` with `JSON.stringify(ENV)` — no `</script>` escaping (`root.tsx:109-113`)
- **S-9.** `os.environ.get("E2B_API_KEY")` bypasses Settings/DB-first pattern (`sandbox/manager.py:43`)

### Architecture (4)
- **A-15.** `artifacts: dict[str, list[str]]` in TypedDict doesn't match actual usage `dict[str, Any]` (`state.py`)
- **A-16.** `update_state` with `type: ignore` masks key typos (`state.py`)
- **A-17.** Business logic inline in 1768-line `graph.py` (`bid_submission_node`, `email_sending_node`)
- **A-18.** Duplicate session management between `dependencies.py` and `database.py`

### Performance (5)
- **P-8.** `_save_state` hset + expire not atomic — partial writes on crash (`rate_limiter.py:123-142`)
- **P-9.** `invalidate_by_type` uses SCAN + individual DELETE — O(N) per key (`semantic_cache.py:184-210`)
- **P-10.** `_pg_search` acquires 2 connections from pool for search + hit-count update (`semantic_cache.py:297-328`)
- **P-11.** `pool_size=10, max_overflow=20` likely insufficient with background tasks (`database.py:28-36`)
- **P-12.** `CostTracker.records` grows unbounded in long-running process (`llm_client.py:171-195`)

### LLM/Agents (10)
- **A-19.** Bid prompt pricing strategy lacks review count context — perpetual underbidding (`prompts/bid.py:47-48`)
- **A-20.** Critic scoring gameable by LLM sycophancy — no independent quality checks (`prompts/critic.py:88-93`)
- **A-21.** Milestone amounts not validated against bid_amount total (`bid.py:390-393`)
- **A-22.** Revision loop bypassable via LLM score manipulation (`critic.py:237,255`)
- **A-23.** SemgrepGate instance recreated on every review call (`critic.py:406`)
- **A-24.** Revision count stored as string-in-list in artifacts — fragile encoding (`critic.py:529-538`)
- **A-25.** Verdict/score inconsistency handled by implicit fall-through (`critic.py:237-364`)
- **A-26.** Context overflow detection relies on fragile string matching (`llm_client.py:585-593`)
- **A-27.** Semantic cache key doesn't include temperature/model — cross-parameter collisions (`llm_client.py:352`)
- **A-28.** Loop detector off-by-one allows 1 extra identical step (`loop_detector.py:100-101`)

### Testing (4)
- **T-7.** `test_full_pipeline.py` uses `ainvoke` despite known bug (`test_full_pipeline.py`)
- **T-8.** Factory `_counter` class var leaks between tests (`tests/factories.py`)
- **T-9.** `mock_db_pool` always returns empty results (`tests/conftest.py`)
- **T-10.** No negative golden set cases (malformed JSON, truncated output)

### Frontend (2)
- **F-5.** Settings Telegram linking uses raw `fetch` instead of `apiFetch` — bypasses token refresh (`_app.settings.tsx:158`)
- **F-6.** Retry after 401 reuses same AbortController (`api.ts`)

### Infrastructure (2)
- **I-3.** Valkey defaults to password "changeme" in prod compose (`docker-compose.prod.yml:47,53,77,111,142,283`)
- **I-4.** Docker socket mounted in worker container — host root access if compromised (`docker-compose.prod.yml:122`)
- **I-5.** FL.ru rate limiter per-instance, not distributed (`fl_ru.py:72`)
- **I-6.** `time.monotonic()` for `paused_until` — meaningless after process restart (`rate_limiter.py:263`)
- **I-7.** Prod compose API has no Alembic migration retry loop (`docker-compose.prod.yml:72-73`)
- **I-8.** Playwright Chromium in all containers (+400-600MB), only worker needs it

---

## LOW Findings (14)

| # | Finding | File |
|---|---------|------|
| S-10 | `mask_value` leaks secrets ≤4 chars | `encryption.py:130-157` |
| S-11 | Role disclosure in PermissionDeniedException | `guards.py:147-149` |
| S-12 | Email enumeration via /register | `auth.py:86-89` |
| S-13 | API keys in URL query parameters | `settings.py:556-558, 571-573` |
| A-29 | `max_iterations` checks `retry_count` not actual iterations | `base.py` |
| A-30 | Model assignments in prompt docstrings don't match registry | `prompts/*.py` vs `llm_client.py:124-135` |
| P-13 | `list_pending` fires 3 sequential queries when 2 suffice | `hitl.py:154-200` |
| P-14 | `list_jobs` count includes unnecessary `selectinload` | `jobs.py:88-101` |
| L-4 | No output token budget in any prompt | `src/prompts/*.py` |
| L-5 | `match_score` not clamped in Scout parser | `scout.py:289` |
| L-6 | `confidence_score` not clamped in Bid parser | `bid.py:382-385` |
| L-7 | Loop detector hash doesn't include `retry_count` | `loop_detector.py:155-157` |
| T-11 | No shared conftest.py in integration tests | `tests/integration/` |
| T-12 | `sample_project` fixture hardcoded to platform="freelancer" | `tests/conftest.py` |

---

## Positive Findings

1. **HITL defense-in-depth** (`bid.py:129,155,388`) — `requires_hitl=True` enforced at 3 independent levels. Excellent pattern.
2. **GitHub Actions SHA-pinned** (`.github/workflows/ci.yml`) — All actions pinned to commit SHAs. Excellent supply chain security.
3. **Multi-stage Docker with non-root user** (`Dockerfile`) — Correctly implemented.
4. **Network segmentation** (`docker-compose.prod.yml`) — `backend` network is `internal: true`.
5. **Production secret validation** (`config.py:150-156`) — Refuses to start with default secrets when `DEBUG=False`.
6. **No eval/exec in json_repair.py** — Reviewed clean. Only `json.loads` + regex.
7. **Upwork adapter has no `submit_bid` method** — Correct architectural enforcement.
8. **SQL wildcard injection prevention** — `_escape_like()` helper consistently used in API routes.
9. **2,094 tests passing, grade A+** — Strong test foundation.
10. **Structured logging** — Consistent use across all adapters with timing metrics.

---

## Priority Remediation Roadmap

### Phase 1: Hotfix (before next deploy)
| Priority | Findings | Effort | Impact |
|----------|----------|--------|--------|
| 1 | S-1 (Valkey blacklist fail-open) | 2h | Auth bypass prevention |
| 2 | A-3 (unknown HITL action = approve) | 1h | Accidental bid/delivery approval |
| 3 | L-1 (bid amount bounds) | 1h | Financial safety |
| 4 | P-1 (semantic cache cross-type) | 30min | Wrong cached responses |
| 5 | I-1 (submit_bid missing `period`) | 30min | Freelancer bids crash |
| 6 | A-1 (routing function mutation) | 1h | Infinite revision loops |

### Phase 2: Sprint 1 (auth & LLM safety)
| Priority | Findings | Effort | Impact |
|----------|----------|--------|--------|
| 7 | S-2+S-6 (refresh token rotation) | 4h | Token replay prevention |
| 8 | A-8+A-9 (prompt injection resistance) | 4h | LLM manipulation prevention |
| 9 | F-1 (token refresh race condition) | 2h | User experience (random logouts) |
| 10 | L-2 (retry exception tuples) | 2h | Rate limit handling |
| 11 | A-12 (credential leakage multi-user) | 4h | Security isolation |
| 12 | P-2 (DB pool per pipeline) | 3h | Connection exhaustion |
| 13 | P-3 (Lock held during sleep) | 2h | Convoy effect |

### Phase 3: Sprint 2 (stability & testing)
| Priority | Findings | Effort | Impact |
|----------|----------|--------|--------|
| 14 | A-4+A-5 (checkpoint serde + tx) | 4h | State corruption on resume |
| 15 | T-1+T-2 (test graph + autouse mocks) | 4h | Test reliability |
| 16 | A-6 (ainvoke → astream) | 2h | Pipeline returns correct state |
| 17 | F-2 (WS listener leak) | 2h | Dashboard memory leak |
| 18 | A-10 (Scout dedup cartesian) | 1h | Cross-platform false negatives |
| 19 | S-3 (tokens to httpOnly cookies) | 8h | XSS token theft prevention |

### Phase 4: Sprint 3 (hardening)
All remaining MEDIUM/LOW findings, property tests, frontend test setup, golden set expansion.

---

## Methodology Notes

- Each agent independently reviewed its file set, then findings were cross-referenced and deduplicated
- 12 findings appeared in multiple agents' reports (counted once at highest severity)
- All CRITICAL/HIGH findings verified by reading source code at cited line numbers
- Recommendations respect existing project patterns (RULES.md, .claude/rules/)
- The review was read-only — no files were modified in the repository
