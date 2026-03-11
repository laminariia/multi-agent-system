# Phase 1: Production Safety — Implementation Plan

## Overview
6 production safety features required by specs but missing from implementation.
Each is LOW-MEDIUM effort with HIGH impact on operational reliability.

## Order of Implementation
1. Circuit Breaker → 2. LLM Budget Control → 3. Concurrency Locks → 4. Email Suppression List → 5. HITL Capacity Management → 6. LLM Per-Provider Rate Limiting

---

## 1. Circuit Breaker (security-spec.md, infrastructure-spec.md)

### Spec Requirement
- 5-failure window triggers OPEN state
- 300s cooldown before HALF_OPEN
- Single test request in HALF_OPEN; success → CLOSED, failure → OPEN
- Per-platform isolation (freelancer, upwork, fl_ru, kwork, telegram)

### Architecture
```
src/adapters/circuit_breaker.py
├── CircuitState (enum: CLOSED, OPEN, HALF_OPEN)
├── CircuitBreaker
│   ├── __init__(name, failure_threshold=5, recovery_timeout=300, half_open_max=1)
│   ├── record_success() → resets failure count, transitions to CLOSED
│   ├── record_failure() → increments count, may transition to OPEN
│   ├── can_execute() → bool (checks state + timeout)
│   ├── state → CircuitState
│   └── stats → dict (failures, last_failure, state, opened_at)
└── CircuitBreakerOpen(Exception)
```

### Integration Points
- `src/adapters/rate_limiter.py` — wrap `acquire()` with circuit breaker check
- Each platform gets its own CircuitBreaker instance
- On OPEN → raise `CircuitBreakerOpen` → adapter skips platform, logs warning
- On HALF_OPEN → allow 1 request, observe result

### Edge Cases
- Concurrent access: use asyncio.Lock per breaker
- Timer precision: use `time.monotonic()` not `time.time()`
- State persistence: in-memory only (resets on restart — acceptable for circuit breaker)
- Thread safety: all async, no threading concerns

### Tests
- test_circuit_breaker_closed_by_default
- test_circuit_breaker_opens_after_threshold
- test_circuit_breaker_rejects_when_open
- test_circuit_breaker_transitions_to_half_open_after_timeout
- test_circuit_breaker_closes_on_half_open_success
- test_circuit_breaker_reopens_on_half_open_failure
- test_circuit_breaker_resets_on_success_streak
- test_circuit_breaker_per_platform_isolation
- test_circuit_breaker_stats
- test_rate_limiter_integration_with_circuit_breaker

---

## 2. LLM Budget Control (security-spec.md, MASTER-VISION.md)

### Spec Requirement
- $50/day limit, $500/month limit
- 80% threshold → Telegram alert
- BudgetExceeded exception → block LLM calls
- Track per-model costs

### Architecture
```
src/core/budget_tracker.py
├── BudgetTracker
│   ├── __init__(daily_limit=50.0, monthly_limit=500.0, alert_threshold=0.8)
│   ├── record_cost(model, input_tokens, output_tokens) → float (cost)
│   ├── check_budget() → raises BudgetExceeded if over limit
│   ├── get_usage() → dict (daily_spent, monthly_spent, remaining)
│   ├── _calculate_cost(model, in_tokens, out_tokens) → float
│   └── _send_alert_if_needed() → sends Telegram alert at 80%
├── BudgetExceeded(MASException)
└── MODEL_COSTS: dict[str, tuple[float, float]]  # (input_per_1k, output_per_1k)
```

### Storage
- Valkey keys: `budget:daily:{YYYY-MM-DD}` (TTL 48h), `budget:monthly:{YYYY-MM}` (TTL 35d)
- Atomic INCRBYFLOAT for concurrent safety
- No PostgreSQL needed — ephemeral cost tracking

### Integration
- `LLMClient.call()` — call `budget_tracker.check_budget()` before request, `record_cost()` after
- Settings: `LLM_DAILY_BUDGET`, `LLM_MONTHLY_BUDGET` env vars (defaults $50/$500)
- Alert: reuse `TelegramNotifier.send_alert()` pattern

### Tests
- test_budget_tracker_records_cost
- test_budget_tracker_daily_limit_exceeded
- test_budget_tracker_monthly_limit_exceeded
- test_budget_tracker_80_percent_alert
- test_budget_tracker_cost_calculation_per_model
- test_budget_tracker_resets_daily
- test_budget_tracker_concurrent_updates
- test_llm_client_integration_budget_check

---

## 3. Concurrency Locks (infrastructure-spec.md)

### Spec Requirement
- NegotiationLock: prevent two agents from modifying same bid/thread
- ThreadLock: prevent concurrent graph invocations on same thread_id
- Valkey SET NX with TTL

### Architecture
```
src/core/locks.py
├── DistributedLock
│   ├── __init__(valkey_client, name, ttl=300, retry_interval=0.5, max_retries=10)
│   ├── async acquire() → bool
│   ├── async release() → bool
│   ├── async __aenter__ / __aexit__ — context manager
│   └── _lock_key → str
├── ThreadLock(DistributedLock) — lock on thread_id
├── NegotiationLock(DistributedLock) — lock on bid_id/job_id
└── LockAcquisitionFailed(MASException)
```

### Implementation
- Valkey `SET key owner_id NX EX ttl`
- Release: Lua script `if GET==owner then DEL` (atomic)
- owner_id: `uuid4()` per lock instance (prevents accidental release by other holder)
- Retry with exponential backoff (0.5s, 1s, 2s... up to max_retries)

### Integration
- `resume_from_hitl()` — wrap with `ThreadLock(thread_id)`
- `run_full_pipeline()` — wrap with `ThreadLock(thread_id)`
- Future: NegotiationEngine will use `NegotiationLock`

### Tests
- test_lock_acquire_release
- test_lock_prevents_double_acquire
- test_lock_auto_release_on_exit
- test_lock_ttl_expiry
- test_lock_owner_isolation (can't release someone else's lock)
- test_lock_retry_on_contention
- test_lock_acquisition_failed_after_max_retries
- test_thread_lock_on_thread_id
- test_negotiation_lock_on_job_id

---

## 4. Email Suppression List (security-spec.md, outreach-spec.md)

### Spec Requirement
- CAN-SPAM: honor unsubscribe within 10 business days
- GDPR: right to erasure
- Bounce handling: hard bounce → permanent suppress, soft bounce → retry 3x then suppress
- Check suppression list BEFORE every send

### Architecture
```
Alembic migration: add `email_suppression_list` table
  - id (UUID, PK)
  - email (VARCHAR(255), UNIQUE, indexed)
  - reason (VARCHAR(50): hard_bounce | unsubscribe | complaint | manual | gdpr_erasure)
  - source (VARCHAR(100): bounce_handler | user_request | admin | webhook)
  - suppressed_at (TIMESTAMP)
  - expires_at (TIMESTAMP, nullable — for soft-bounce temporary suppression)

src/core/models.py: EmailSuppressionEntry model

src/enrichment/suppression.py:
├── SuppressionList
│   ├── async is_suppressed(email) → bool
│   ├── async suppress(email, reason, source, expires_at=None)
│   ├── async unsuppress(email) → for admin override only
│   ├── async bulk_check(emails) → set[str] (suppressed ones)
│   └── async cleanup_expired() → int (removed count)
```

### Integration
- `EmailSender.send()` — check `is_suppressed()` before SMTP
- `EmailSender._handle_bounce()` — call `suppress(email, "hard_bounce")` on permanent failures
- `TelegramDMSender` — no suppression (different channel)
- Scheduled job in `WorkerScheduler` — `cleanup_expired()` daily

### Tests
- test_suppress_email
- test_is_suppressed_returns_true
- test_unsuppressed_email_returns_false
- test_bulk_check_filters_suppressed
- test_hard_bounce_auto_suppresses
- test_soft_bounce_temporary_suppression_expires
- test_gdpr_erasure_reason
- test_duplicate_suppress_idempotent
- test_cleanup_expired_removes_old
- test_email_sender_skips_suppressed

---

## 5. HITL Capacity Management (hitl-spec.md, MASTER-VISION.md)

### Spec Requirement
- MAX_PENDING_HITL = 100 (configurable)
- Reject new HITL entries when at capacity
- 5-min timeout on stale pending items (mark expired)
- 1-hour prune of resolved items from active queue

### Architecture
```
src/api/routes/hitl.py — add capacity check
src/core/hitl_capacity.py:
├── HITL_MAX_PENDING (from env, default 100)
├── async check_hitl_capacity(session) → raises HITLCapacityExceeded if over
├── async expire_stale_hitl(session, timeout_minutes=60) → int
```

### Integration
- Every HITL creation path (graph nodes, agent base) — call `check_hitl_capacity()`
- `WorkerScheduler` — add `expire_stale_hitl()` every 15 minutes
- Graceful: capacity exceeded → log warning + skip HITL creation (don't crash pipeline)

### Tests
- test_capacity_check_under_limit_passes
- test_capacity_check_at_limit_raises
- test_expire_stale_hitl_marks_expired
- test_capacity_configurable_via_env
- test_hitl_creation_respects_capacity

---

## 6. LLM Per-Provider Rate Limiting (security-spec.md)

### Spec Requirement
- Rate limit Layer 3: per-provider quotas
- OpenRouter: 200 RPM (requests per minute)
- OpenAI (embeddings): 500 RPM
- Prevent 429 errors from provider

### Architecture
```
src/core/llm_rate_limiter.py:
├── LLMRateLimiter
│   ├── __init__(provider, rpm_limit)
│   ├── async acquire() → waits if at limit
│   ├── async release() → decrements counter
│   └── _window_key → Valkey key with minute granularity
├── PROVIDER_LIMITS = {"openrouter": 200, "openai": 500}
```

### Integration
- `LLMClient.call()` — acquire before request
- `EmbeddingService` — acquire before embedding call
- Valkey-based sliding window counter (INCR + EXPIRE)

### Tests
- test_rate_limiter_allows_under_limit
- test_rate_limiter_blocks_at_limit
- test_rate_limiter_resets_after_window
- test_per_provider_isolation
- test_llm_client_integration
