# Full Gap Closure Plan — Spec vs Implementation

> **Date**: 2026-03-19
> **Branch**: `auto/2026-03-19/full-gap-closure`
> **Method**: 6 parallel analysis agents verified 20 specs against 120+ source files
> **False positives eliminated**: 8 (Docker isolation, encryption key check, List-Unsubscribe, Experience Store, Design revision index — all already implemented)

---

## Verified FALSE POSITIVES (already implemented, NO action needed)

| Claim | Actual Status | Evidence |
|-------|--------------|----------|
| Docker sandbox lacks isolation | ✅ `mem_limit="512m"`, `network_mode="none"`, `user="1000:1000"` | `src/sandbox/docker_executor.py:256-259` |
| No production encryption key check | ✅ Raises `ValueError` for "change-me-in-production" | `src/core/config.py:178-181` |
| List-Unsubscribe missing | ✅ RFC 8058 headers + tests | `src/enrichment/email_sender.py:468-470` |
| Experience Store not called | ✅ Packager calls `save_experience()` | `src/agents/packager.py:700` |
| Design agent doesn't check revision | ✅ `is_revision` check present | `src/agents/design.py:146-147` |
| Docker non-root not enforced | ✅ `user="1000:1000"` | `src/sandbox/docker_executor.py:259` |
| Docker mem_limit missing | ✅ `mem_limit="512m"` | `src/sandbox/docker_executor.py:256` |
| Docker network not isolated | ✅ `network_mode="none"` | `src/sandbox/docker_executor.py:258` |

---

## PHASE 1 — CRITICAL (P0): Dynamic Routing Fix

**Impact**: Breaks core spec invariant — `agent_sequence` is ignored, hardcoded flow used instead.
**Files**: `src/core/graph.py`, `src/agents/dev.py`, `src/agents/content.py`
**Tests**: `tests/unit/test_dynamic_routing.py` (new), update `tests/integration/test_graph_*.py`

### Group A: Agent Index Fix (1 agent)

**Owner**: Feature Worker (src/ only)
**Files**: `src/agents/dev.py`, `src/agents/content.py`

| # | Task | File:Line | Change |
|---|------|-----------|--------|
| A1 | Dev: check `revision_severity` before incrementing index | `dev.py:185` | Add `is_revision = state.get("revision_severity") is not None; new_index = state["current_sequence_index"] if is_revision else state.get("current_sequence_index", 0) + 1` (same pattern as design.py:146-147) |
| A2 | Content: check `revision_severity` before incrementing index | `content.py:133` | Same pattern as A1 |
| A3 | Dev: set `next_agent` dynamically from `agent_sequence` | `dev.py:183-187` | Replace hardcoded `next_agent="content"` with sequence-aware logic: read `agent_sequence[new_index]` or `"critic"` if end of sequence |
| A4 | Content: set `next_agent` dynamically from `agent_sequence` | `content.py:131-135` | Same pattern as A3 |

### Group B: Graph Routing Fix (1 agent)

**Owner**: Feature Worker (src/ only)
**Files**: `src/core/graph.py`

| # | Task | File:Line | Change |
|---|------|-----------|--------|
| B1 | `_route_after_dev()` → delegate to `_route_next_in_sequence()` | `graph.py:1056-1072` | Replace hardcoded `"content_node"` with `_route_next_in_sequence(state)` |
| B2 | `_route_after_content()` → delegate to `_route_next_in_sequence()` | `graph.py:1075-1091` | Replace hardcoded `"design_node"` with `_route_next_in_sequence(state)` |
| B3 | `_route_after_design()` → delegate to `_route_next_in_sequence()` | `graph.py:1094-1110` | Replace hardcoded `"critic_node"` with `_route_next_in_sequence(state)` |
| B4 | Verify `_route_next_in_sequence()` handles all edge cases | `graph.py:825-878` | Ensure: empty sequence → packager, out-of-bounds → critic, skipped agents, revision_target routing |

### Group C: Tests for P0 (1 agent, parallel)

**Owner**: Quality Worker (tests/ only)
**Files**: `tests/unit/test_dynamic_routing.py` (new)

| # | Task | Description |
|---|------|-------------|
| C1 | Test `agent_sequence=["dev"]` → skips content+design → critic | Verify Dev routes directly to Critic |
| C2 | Test `agent_sequence=["dev", "content"]` → skips design → critic | Verify Content routes to Critic, not Design |
| C3 | Test `agent_sequence=["design"]` → skips dev+content → critic | Verify Design-only projects work |
| C4 | Test `agent_sequence=[]` → planner routes directly to packager | Empty sequence edge case |
| C5 | Test revision_severity prevents index increment in Dev | Revision targets same agent, not next |
| C6 | Test revision_severity prevents index increment in Content | Same for Content |
| C7 | Test full graph integration: Scout→Bid→HITL→Planner(seq=["dev"])→Dev→Critic→Packager | End-to-end with reduced sequence |

**Acceptance**: All 7 tests pass + existing graph tests still green.

---

## PHASE 2 — HIGH (P1): Missing Features from Spec

### Group D: WebScout Agent Implementation (1 agent)

**Owner**: Feature Worker
**Files**: `src/agents/web_scout.py`
**Spec**: `docs/Full_work/specs/sales-agent-spec.md` (WebScout section), `pipeline-b-spec.md`

| # | Task | Description |
|---|------|-------------|
| D1 | Implement DuckDuckGo search source | `_search_duckduckgo(query, max_results)` → business listings without websites |
| D2 | Implement Yandex.Business stub | `_search_yandex_business(city, category)` → placeholder with interface |
| D3 | Implement 2GIS stub | `_search_2gis(city, category)` → placeholder with interface |
| D4 | Implement Google Maps stub | `_search_google_maps(city, category)` → placeholder with interface |
| D5 | Multi-source dedup + LLM qualification | Merge results, dedup by business name+city, LLM filter for "needs website" |
| D6 | Wire WebScout into Pipeline B graph | Add `web_scout_node` in `build_pipeline_b_graph()` |
| D7 | Tests: 30+ unit tests for WebScout | Mock all sources, test dedup, LLM filtering, error handling |

### Group E: BusinessAnalyzer Medium + Deep Tiers (1 agent)

**Owner**: Feature Worker
**Files**: `src/core/business_analyzer.py`
**Spec**: `docs/Full_work/specs/sales-agent-spec.md` §BusinessAnalyzer

| # | Task | Description |
|---|------|-------------|
| E1 | Implement `_analyze_medium()` (30s tier) | Lighthouse score (stub), basic SEO check (title/meta/h1), social activity dates |
| E2 | Implement `_analyze_deep()` (2-3min tier) | SimilarWeb traffic stub, Wappalyzer tech detection stub, competitor scan via Overpass |
| E3 | Implement `_generate_battlecard()` | Feature matrix, market share estimate, weak points, objection handlers |
| E4 | Wire tiers to lead_scorer temperature | hot → deep, warm → medium, cold → quick (already exists) |
| E5 | Tests: 25+ tests for medium/deep tiers | Mock external APIs, test tier routing, battlecard generation |

### Group F: Knowledge Base RAG Wiring (1 agent)

**Owner**: Feature Worker
**Files**: `src/core/llm_client.py`, `src/agents/bid.py`, `src/agents/scout.py`, `src/agents/dev.py`, `src/knowledge/experience_store.py`
**Spec**: `docs/Full_work/specs/rag-memory-spec.md`

| # | Task | Description |
|---|------|-------------|
| F1 | Add `retrieve_context(query, category, top_k=5)` to ExperienceStore | Unified RAG retrieval API |
| F2 | Wire RAG into Bid agent | Before LLM call: retrieve similar proposals, inject as context |
| F3 | Wire RAG into Dev agent | Before code generation: retrieve similar code snippets |
| F4 | Wire RAG into Scout agent | Enrich scoring with historical success rates from KB |
| F5 | Add semantic cache cleanup cron | APScheduler job every 6h: LRU eviction at 10K entries, TTL purge |
| F6 | Tests: 20+ tests for RAG wiring | Mock ExperienceStore, verify context injection, test cron |

### Group G: Embeddings Migration to Qwen3 (1 agent)

**Owner**: Feature Worker
**Files**: `src/core/llm_client.py`, `src/core/semantic_cache.py`, `src/core/config.py`
**Spec**: `CLAUDE.md` — "Qwen3-Embedding-8B (3072 dim Matryoshka, via OpenRouter)"

| # | Task | Description |
|---|------|-------------|
| G1 | Add `EMBEDDING_MODEL` config | Default: `"qwen/qwen3-embedding-8b"`, fallback: `"text-embedding-3-large"` |
| G2 | Add `EMBEDDING_PROVIDER` config | `"openrouter"` (default) or `"openai"` (fallback) |
| G3 | Update embedding generation to use OpenRouter | Route through OpenRouter API with `OPENROUTER_API_KEY` |
| G4 | Verify dimension compatibility (3072) | Both Qwen3 and text-embedding-3-large output 3072 dims — verify HNSW index compat |
| G5 | Add fallback logic | If OpenRouter embedding fails → fallback to OpenAI |
| G6 | Tests: embedding provider switching | Mock both providers, test fallback |

### Group H: Edge Case Auto-Escalation (1 agent)

**Owner**: Feature Worker
**Files**: `src/core/graph.py`, `src/agents/base.py`, `src/core/heartbeat.py`
**Spec**: `docs/Full_work/specs/hitl-spec.md` §Edge Cases

| # | Task | Description |
|---|------|-------------|
| H1 | Account Ban → auto-HITL alert | On `PlatformBannedError`: create HITL type="alert", priority="urgent", pause pipeline |
| H2 | LLM API Down → agent pause + HITL | On 3 consecutive LLM failures: pause agent, create HITL type="alert" |
| H3 | Capacity Overflow (10+ active wins) → HITL | When active projects > threshold: create HITL type="capacity_warning" |
| H4 | Dev Agent fails 3+ times → HITL escalation | After MAX_CRASH_RETRIES exhausted: create HITL type="agent_failure" |
| H5 | Email bounce >10% → auto-pause campaign | Monitor bounce rate, pause on red line, create HITL alert |
| H6 | Tests: 15+ edge case escalation tests | Simulate each scenario, verify HITL creation |

### Group I: Data Retention Soft Delete (1 agent)

**Owner**: Feature Worker
**Files**: `src/core/data_retention.py`, `src/core/models.py`, `alembic/versions/` (new migration)
**Spec**: `docs/Full_work/specs/legal-compliance-spec.md`

| # | Task | Description |
|---|------|-------------|
| I1 | Add `deleted_at` column to 5 tables | agent_logs, hitl_queue, ab_test_results, scheduled_messages, langgraph_checkpoints |
| I2 | Create Alembic migration | `ADD COLUMN deleted_at TIMESTAMPTZ DEFAULT NULL` + index |
| I3 | Update `DataRetentionManager` to use soft delete | `UPDATE SET deleted_at=now()` instead of `DELETE` |
| I4 | Add `purge_soft_deleted()` method | Hard delete records with `deleted_at < now() - 30 days` |
| I5 | Add retention audit logging | Log count of soft-deleted + hard-deleted per table per run |
| I6 | Update all queries to exclude soft-deleted | Add `WHERE deleted_at IS NULL` to relevant queries |
| I7 | Tests: 15+ retention tests | Test soft delete, hard purge, audit log, query exclusion |

### Group J: LinkedIn InMail Implementation (1 agent)

**Owner**: Feature Worker
**Files**: `src/channels/linkedin.py`
**Spec**: `docs/Full_work/specs/outreach-spec.md`

| # | Task | Description |
|---|------|-------------|
| J1 | Implement OAuth 2.0 three-legged flow | Token exchange, refresh, storage via Fernet encryption |
| J2 | Implement `send_inmail()` via LinkedIn API | Marketing API message send, response parsing |
| J3 | Implement rate limiting | LinkedIn API: 20 InMails/day, 100/month |
| J4 | Add error handling | 429 → rate limit, 403 → token expired, retry logic |
| J5 | Tests: 15+ tests | Mock OAuth flow, message send, rate limiting, errors |

### Group K: Adaptive Rate Limiter Verification (1 agent)

**Owner**: Quality Worker (tests/) + Feature Worker (src/ if fixes needed)
**Files**: `src/adapters/rate_limiter.py`, `tests/unit/test_adaptive_rate_limiter.py`

| # | Task | Description |
|---|------|-------------|
| K1 | Verify success adaptation (×1.1 after 5 successes) | Read code, add explicit tests |
| K2 | Verify failure adaptation (×0.5 on 429) | Test with simulated 429 responses |
| K3 | Verify captcha/ban pause (30 min) | Test pause duration, Telegram alert trigger |
| K4 | Verify Valkey persistence | Test rate state save/restore across restarts |
| K5 | Fix any gaps found during verification | Implement missing logic if tests fail |

---

## PHASE 3 — MEDIUM (P2): Enhancements

### Group L: Sisyphus Orchestration Patterns (1 agent)

**Owner**: Feature Worker
**Files**: `src/core/sisyphus/` (new module)
**Spec**: `docs/Full_work/specs/orchestrator-spec.md` §Sisyphus

| # | Task | Description |
|---|------|-------------|
| L1 | Implement `HookRegistry` class | PreToolUse, PostToolUse, AgentStart, AgentStop trigger points |
| L2 | Implement `TodoContinuationEnforcer` | Agent cannot quit until goals done or max retries (3) → HITL |
| L3 | Implement `BackgroundExecutor` | Parallel agent execution with concurrency limits per LLM provider |
| L4 | Implement `TaskCategoryPresets` | Presets by type: freelance-bid, code-gen, design-ui, outreach |
| L5 | Wire into OrchestratorService | Register hooks, start enforcer on runner boot |
| L6 | Tests: 30+ tests | Hook firing, continuation enforcer, parallel execution |

### Group M: Russian Geo APIs (1 agent)

**Owner**: Feature Worker
**Files**: `src/geo/yandex_maps.py` (new), `src/geo/dgis.py` (new), `src/agents/web_scout.py`

| # | Task | Description |
|---|------|-------------|
| M1 | Implement Yandex Maps Geocoder client | `geocode_city()`, `search_businesses()` via Yandex HTTP API |
| M2 | Implement 2GIS API client | `search_businesses()` via 2GIS public API, rate limiting |
| M3 | Wire into WebScout as additional sources | Add to multi-source pipeline after DuckDuckGo |
| M4 | Wire into GeoScout as alternative geocoder | Fallback: Nominatim → Yandex → error |
| M5 | Tests: 20+ tests | Mock API responses, test fallback chain, rate limiting |

### Group N: Multi-User Telegram (1 agent)

**Owner**: Feature Worker
**Files**: `src/bot/handler.py`, `src/bot/notifications.py`, `src/core/models.py`
**Spec**: `docs/Full_work/specs/telegram-bot-spec.md` §Not Implemented

| # | Task | Description |
|---|------|-------------|
| N1 | Support multiple `telegram_chat_id` values | Query all users with linked Telegram accounts |
| N2 | Notification preferences model | `telegram_notification_prefs` table: user_id, types[], quiet_hours |
| N3 | Per-user notification routing | Send HITL alerts only to users with matching role/permissions |
| N4 | HITL timeout/expiry alerts | Cron: check HITL items approaching expiry, send reminder |
| N5 | `telegram_notifications` audit table | Log all sent notifications: user_id, type, message_id, sent_at |
| N6 | Tests: 20+ tests | Multi-user dispatch, preferences filtering, audit logging |

### Group O: Pencil.dev MCP Integration (1 agent)

**Owner**: Feature Worker
**Files**: `src/agents/design.py`, `src/integrations/pencil_mcp.py` (new or existing)
**Spec**: `docs/Full_work/specs/agents-spec.md` §Design Agent

| # | Task | Description |
|---|------|-------------|
| O1 | Implement `PencilMCPClient.create_design()` | Generate .pen file from JSON design spec |
| O2 | Implement `PencilMCPClient.export_screenshot()` | Export .pen node as PNG/JPEG |
| O3 | Implement `PencilMCPClient.export_code()` | Export .pen design as HTML/CSS/React code |
| O4 | Wire into Design Agent `_execute()` | After LLM spec generation → create .pen → screenshot → store artifact |
| O5 | Wire design HITL with screenshot preview | Include screenshot URL in HITL payload |
| O6 | Tests: 15+ tests | Mock MCP client, test .pen creation, export, artifact storage |

### Group P: HITL Enhancements (1 agent)

**Owner**: Feature Worker
**Files**: `src/api/routes/hitl.py`, `src/core/models.py`, `src/bot/handler.py`

| # | Task | Description |
|---|------|-------------|
| P1 | Payload edit history tracking | Store before/after snapshots in `hitl_edit_history` table |
| P2 | Create Alembic migration for edit history | `hitl_edit_history`: hitl_id, edited_by, before_payload, after_payload, edited_at |
| P3 | Telegram edit flow | Allow editing bid text/amount via Telegram (multi-step conversation) |
| P4 | HITL expiry countdown in Telegram | Send reminder at 50% and 90% of TTL elapsed |
| P5 | Tests: 15+ tests | Edit history, Telegram edit flow, expiry reminders |

### Group Q: Backup & DR Automation (1 agent)

**Owner**: Infra Worker
**Files**: `scripts/backup.sh` (new), `scripts/restore.sh` (new), `scripts/health_check.sh`
**Spec**: `docs/Full_work/specs/deploy-spec.md` §Backup

| # | Task | Description |
|---|------|-------------|
| Q1 | `scripts/backup.sh` — automated pg_dump | Compress, timestamp, upload to S3/R2, rotate (keep 30 daily, 12 monthly) |
| Q2 | `scripts/restore.sh` — pg_restore from backup | Download, decompress, restore, verify row counts |
| Q3 | `scripts/valkey_backup.sh` — RDB snapshot export | Copy RDB file to backup storage |
| Q4 | Add backup cron to docker-compose.prod.yml | Cron container: daily backup at 03:00 UTC |
| Q5 | DR testing script | Restore to temp DB, run health checks, compare row counts |
| Q6 | Secret rotation documentation | Procedure for rotating JWT_SECRET_KEY, ENCRYPTION_KEY, API keys |

### Group R: CI/CD Enhancements (1 agent)

**Owner**: Infra Worker
**Files**: `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

| # | Task | Description |
|---|------|-------------|
| R1 | Add load tests to CI (Locust) | Run basic load scenarios on PR, fail on p95 > 500ms |
| R2 | Automate golden set tests | Run golden set regression on every PR (not just manual) |
| R3 | Dashboard Dockerfile | Explicit multi-stage Node build instead of nixpacks |
| R4 | Add property-based tests to CI | Run `tests/property/` in unit test stage |
| R5 | Trivy image scan for dashboard | Scan Node image same as Python image |

### Group S: Stealth Improvements (1 agent)

**Owner**: Feature Worker
**Files**: `src/browser/stealth.py`, `src/adapters/rate_limiter.py`

| # | Task | Description |
|---|------|-------------|
| S1 | User-Agent rotation | Pool of 20+ real UA strings, rotate per session |
| S2 | Canvas fingerprint randomization | Inject noise into canvas.toDataURL() via page.addInitScript() |
| S3 | WebGL fingerprint randomization | Override WebGL renderer/vendor strings |
| S4 | Ban recovery automation | On `PlatformBannedError`: swap account, fresh proxy, 30min cooldown |
| S5 | Fiverr `get_request_details()` | Implement detail-page scraping for Buyer Requests |
| S6 | Tests: 15+ tests | UA rotation, fingerprint injection, ban recovery |

---

## PHASE 4 — LOW (P3): Spec-Marked "Not Implemented"

### Group T: Future Features (backlog, no timeline)

| # | Task | Spec Status | Description |
|---|------|-------------|-------------|
| T1 | Groupchat support for Telegram bot | Spec: "not implemented" | Support group chats with @mention |
| T2 | DecisionMemory KB integration | Spec: "future" | Pattern recording → knowledge base ingestion |
| T3 | Pipeline C: Client Growth + Upselling | Spec: "idea/backlog" | Auto-Monitor → Upsell → new project |
| T4 | Dynamic Capability Registry confidence recalc | Spec: "future" | Recalculate capability scores from experience feedback |
| T5 | Prometheus Planning (interview-based) | Spec: "future" | Clarifying questions before task decomposition |

---

## Execution Matrix: Parallel Agent Assignments

```
PHASE 1 (P0) — 3 agents in parallel, ~2 hours
┌─────────────────────────────────────────────────────┐
│  Agent 1: Group A (dev.py + content.py index fix)   │
│  Agent 2: Group B (graph.py routing fix)            │
│  Agent 3: Group C (tests for dynamic routing)       │
└──────────────────────┬──────────────────────────────┘
                       │ merge + verify all tests pass
                       ▼
PHASE 2 (P1) — 8 agents in parallel, ~4 hours
┌─────────────────────────────────────────────────────┐
│  Agent 4:  Group D (WebScout implementation)        │
│  Agent 5:  Group E (BusinessAnalyzer tiers)         │
│  Agent 6:  Group F (KB RAG wiring)                  │
│  Agent 7:  Group G (Embeddings → Qwen3)             │
│  Agent 8:  Group H (Edge case auto-escalation)      │
│  Agent 9:  Group I (Data retention soft delete)     │
│  Agent 10: Group J (LinkedIn InMail)                │
│  Agent 11: Group K (Rate limiter verification)      │
└──────────────────────┬──────────────────────────────┘
                       │ merge + full test suite
                       ▼
PHASE 3 (P2) — 8 agents in parallel, ~4 hours
┌─────────────────────────────────────────────────────┐
│  Agent 12: Group L (Sisyphus patterns)              │
│  Agent 13: Group M (Russian geo APIs)               │
│  Agent 14: Group N (Multi-user Telegram)            │
│  Agent 15: Group O (Pencil.dev MCP)                 │
│  Agent 16: Group P (HITL enhancements)              │
│  Agent 17: Group Q (Backup/DR automation)           │
│  Agent 18: Group R (CI/CD enhancements)             │
│  Agent 19: Group S (Stealth improvements)           │
└──────────────────────┬──────────────────────────────┘
                       │ merge + full test suite
                       ▼
PHASE 4 (P3) — backlog, no agents assigned
```

## File Ownership Boundaries (Conflict Prevention)

| Group | Exclusive Files | Shared (coordinate) |
|-------|----------------|---------------------|
| A | `src/agents/dev.py`, `src/agents/content.py` | — |
| B | `src/core/graph.py` | — |
| C | `tests/unit/test_dynamic_routing.py` | — |
| D | `src/agents/web_scout.py` | `src/core/graph.py` (add node — coordinate with B) |
| E | `src/core/business_analyzer.py` | — |
| F | `src/knowledge/experience_store.py`, `src/agents/bid.py` (RAG only) | `src/core/llm_client.py` (coordinate with G) |
| G | `src/core/semantic_cache.py`, `src/core/config.py` (embedding fields) | `src/core/llm_client.py` (coordinate with F) |
| H | `src/agents/base.py` (escalation mixin) | `src/core/graph.py` (coordinate with B/D) |
| I | `src/core/data_retention.py`, `src/core/models.py` (deleted_at) | `alembic/versions/` |
| J | `src/channels/linkedin.py` | — |
| K | `tests/unit/test_adaptive_rate_limiter.py` | `src/adapters/rate_limiter.py` (if fixes needed) |
| L | `src/core/sisyphus/` (new module) | `src/api/services/orchestrator.py` |
| M | `src/geo/yandex_maps.py`, `src/geo/dgis.py` (new) | `src/agents/web_scout.py` (coordinate with D) |
| N | `src/bot/notifications.py`, `src/bot/handler.py` | `src/core/models.py` (coordinate with I) |
| O | `src/integrations/pencil_mcp.py`, `src/agents/design.py` | — |
| P | `src/api/routes/hitl.py` | `src/core/models.py` (coordinate with I/N) |
| Q | `scripts/` (new), `docker/` | — |
| R | `.github/workflows/` | `dashboard/Dockerfile` (new) |
| S | `src/browser/stealth.py`, `src/adapters/*.py` | — |

## Dependency Graph

```
Phase 1:
  A ──┐
  B ──┤──► merge P0 ──► Phase 2
  C ──┘

Phase 2:
  D (WebScout) ──────────────────────────┐
  E (BusinessAnalyzer) ──────────────────┤
  F (KB RAG) ←→ G (Embeddings) ─────────┤
  H (Edge Cases) ────────────────────────┤──► merge P1 ──► Phase 3
  I (Soft Delete) ───────────────────────┤
  J (LinkedIn) ──────────────────────────┤
  K (Rate Limiter) ─────────────────────-┘

Phase 3:
  L (Sisyphus) ──────────────────────────┐
  M (Geo APIs) → depends on D (WebScout) │
  N (Multi-user TG) ────────────────────-┤
  O (Pencil MCP) ────────────────────────┤──► merge P2 ──► release
  P (HITL enhance) ─────────────────────-┤
  Q (Backup/DR) ─────────────────────────┤
  R (CI/CD) ─────────────────────────────┤
  S (Stealth) ───────────────────────────┘
```

## Acceptance Criteria (per phase)

### Phase 1 DONE when:
- [ ] `agent_sequence=["dev"]` routes Dev→Critic (skips Content+Design)
- [ ] `agent_sequence=["dev","content"]` routes Dev→Content→Critic (skips Design)
- [ ] Revision targets same agent (no index increment when `revision_severity` set)
- [ ] All existing graph tests still pass
- [ ] 7+ new dynamic routing tests pass

### Phase 2 DONE when:
- [ ] WebScout returns real results from DuckDuckGo (other sources can be stubs)
- [ ] BusinessAnalyzer medium tier returns Lighthouse stub + SEO check
- [ ] Bid agent receives RAG context from ExperienceStore
- [ ] Embeddings configurable (Qwen3 default, OpenAI fallback)
- [ ] Account ban creates HITL alert automatically
- [ ] `deleted_at` column exists, soft delete works, hard purge at 30 days
- [ ] LinkedIn InMail sends via OAuth (not stub)
- [ ] Rate limiter scales up/down verified by tests

### Phase 3 DONE when:
- [ ] HookRegistry fires on AgentStart/AgentStop
- [ ] Yandex/2GIS return geocoding results (stubs OK for API keys)
- [ ] Multiple Telegram users receive HITL notifications
- [ ] Pencil MCP creates .pen files from design specs
- [ ] HITL edit history tracked per resolution
- [ ] `scripts/backup.sh` completes pg_dump + upload
- [ ] Load tests run in CI
- [ ] UA rotation works across sessions

## Metrics

| Metric | Before | After Phase 1 | After Phase 2 | After Phase 3 |
|--------|:------:|:-------------:|:-------------:|:-------------:|
| Spec compliance | 88% | 91% | 96% | 99% |
| Tests | ~3100 | ~3115 | ~3250 | ~3450 |
| CRITICAL gaps | 5 | 0 | 0 | 0 |
| HIGH gaps | 11 | 11 | 0 | 0 |
| MEDIUM gaps | 14 | 14 | 14 | 0 |
