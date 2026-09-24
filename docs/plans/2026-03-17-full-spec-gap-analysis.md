# Full Specification Gap Analysis

> Date: 2026-03-17 | Branch: auto/2026-03-16/production-readiness
> Method: 5 parallel agents audited ALL spec files against codebase
> False positives filtered: NegotiationEngine, ExperienceStore, SalesAgent, SalesConversationEngine, DealMemory, TouchSequence, LeadScorer, BusinessAnalyzer, CrashRecovery — all EXIST in code

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| CRITICAL | 9 | Blocking production or violating spec contracts |
| HIGH | 22 | Core vision features missing or not integrated |
| MEDIUM | 16 | Partial implementations, missing integrations |
| LOW | 11 | Enhancements, polish, future features |
| **TOTAL** | **58** | Unique gaps after deduplication |

---

## CRITICAL (9)

### C1. Pencil.dev MCP Integration — Design Agent
- **Specs**: agents-spec.md, pipeline-a-spec.md (#18), pipeline-b-spec.md, design-workflow.md
- **Status**: NOT IMPLEMENTED (explicitly marked "не реализовано")
- **Gap**: Design Agent outputs JSON specs. Spec requires MCP calls to Pencil.dev to generate `.pen` files, visual mockups, PNG screenshots, HTML/CSS/React code export
- **Note**: Pencil MCP server is ALREADY connected in tooling
- **Effort**: ~20h

### C2. Design HITL Gates (#2 Design Review, #3 Client Approval)
- **Specs**: pipeline-b-spec.md (Фаза 6), hitl-spec.md, design-workflow.md
- **Status**: NOT IMPLEMENTED
- **Gap**: No `hitl_design_node` or `hitl_design_approval_node` in graph.py. Spec requires: operator reviews mockup (max 3 revision rounds) → client approves → Dev starts. Currently design goes straight to Critic
- **Effort**: ~15h

### C3. Portfolio Agent — 11th Agent
- **Specs**: agents-spec.md (Tier 6), portfolio-agent.md (200+ lines design doc)
- **Status**: NOT IMPLEMENTED (`src/agents/portfolio.py` missing)
- **Gap**: Entire agent missing: screenshot automation, git-based portfolio, multi-platform adaptation (6 platforms), HITL gate, audit triggers
- **Effort**: ~30h

### C4. SalesAgent NOT Wired Into Pipeline B Graph
- **Specs**: pipeline-b-spec.md (Фаза 5), outreach-spec.md
- **Status**: Code EXISTS (`src/agents/sales_agent.py`, `sales_conversation.py`) but NOT in graph
- **Gap**: No `sales_agent_node()` in graph. Pipeline B ends at message dispatch without multi-turn negotiation. SalesConversationEngine exists but unreachable
- **Effort**: ~8h

### C5. Pipeline B → Pipeline A Transition
- **Specs**: dev-cycle-spec.md (Склейка), pipeline-b-spec.md (Фаза 7)
- **Status**: NOT IMPLEMENTED
- **Gap**: No `POST /api/v1/deals/{id}/start-development` endpoint. After Pipeline B concept approval, no path to Pipeline A (Planner → Dev/Design/Content). Two pipelines are disconnected
- **Effort**: ~10h

### C6. Notification System — Infrastructure Component
- **Specs**: agents-spec.md (Section: Notification System)
- **Status**: NOT IMPLEMENTED (`src/notifications/` empty)
- **Gap**: 3-channel routing (Telegram realtime, Dashboard WebSocket, Email batch). Severity-based: critical→Telegram+phone, error→Telegram, warning→batch 30min, info→dashboard
- **Effort**: ~20h

### C7. Email Warmup Phase
- **Specs**: enrichment-spec.md, outreach-spec.md, pipeline-b-spec.md
- **Status**: NOT IMPLEMENTED
- **Gap**: EmailSender sends immediately. Spec requires 6-week warmup: Week 1-2 (1/day own domain), Week 3-4 (2/day similar), Week 5-6 (5/day target). Without warmup, production outreach triggers spam filters
- **Effort**: ~15h

### C8. Telegram Webhook Mode
- **Specs**: telegram-bot-spec.md (Bot Architecture)
- **Status**: NOT IMPLEMENTED (polling only)
- **Gap**: Bot uses long polling (25-30s latency). Spec requires webhook POST `/api/v1/telegram/webhook` for real-time delivery. Polling blocks workers at scale
- **Effort**: ~8h

### C9. HITL Viewing Locks (Concurrent Access Protection)
- **Specs**: hitl-spec.md (HITL Viewing & Locks)
- **Status**: NOT IMPLEMENTED
- **Gap**: No `POST/DELETE /api/v1/hitl/{id}/viewing` endpoints. Two operators can resolve same HITL item simultaneously → race condition
- **Effort**: ~6h

---

## HIGH (22)

### H1. WebScoutAgent (Web Search Mode)
- **Spec**: pipeline-b-spec.md (Фаза 1B)
- **Gap**: No nationwide web search agent: 2GIS API, Яндекс.Бизнес, VK Business, Google Search API. Only GeoScout (geo-bounded) exists
- **Effort**: ~25h

### H2. TelegramProfileAggregator
- **Spec**: pipeline-b-spec.md (Фаза 1C)
- **Gap**: TelegramListener exists but no batch aggregation (3+ messages per user → LLM scoring → Lead promotion)
- **Effort**: ~15h

### H3. Business Analyzer Medium/Deep Tiers
- **Spec**: pipeline-b-spec.md (Фаза 2)
- **Gap**: Quick tier works. Medium (Lighthouse, 30s) and Deep (traffic analysis, 2-3m) are stubs with no real logic
- **Effort**: ~20h

### H4. Lead Scorer/Business Analyzer NOT Integrated into Pipeline
- **Spec**: outreach-spec.md
- **Gap**: Code exists but GeoScout doesn't call `lead_scorer.score_lead()`, Outreach doesn't call `business_analyzer.analyze()`. Leads aren't prioritized by value
- **Effort**: ~6h

### H5. Touch Sequence NOT Integrated into Outreach
- **Spec**: outreach-spec.md
- **Gap**: `touch_sequence.py` exists (87 tests!) but Outreach agent doesn't create TouchSequenceState records. No scheduled follow-ups Day 1/3/5/10
- **Effort**: ~8h

### H6. Experience Store Save NOT Triggered
- **Spec**: pipeline-a-spec.md (Phase 6: RAG)
- **Gap**: Experience Store exists but Packager doesn't call `save_experience()` after HITL Final approve. Knowledge accumulation broken
- **Effort**: ~4h

### H7. Execution Cloaking (Double Estimation + Delivery Throttling)
- **Spec**: dev-cycle-spec.md (Фаза 4.5), pipeline-a-spec.md
- **Gap**: `delivery_scheduler.py` generates templates but: (a) Bid Agent no double estimation, (b) no 70% time delay before sending final artifact, (c) no cron dispatch loop
- **Effort**: ~15h

### H8. Platform Adapters: YouDo, Fiverr, Profi.ru
- **Spec**: platform-adapters-spec.md
- **Gap**: Only 4/7 platforms: Freelancer, FL.ru, Kwork, Upwork. Missing 3 adapters entirely
- **Effort**: ~30h (10h each)

### H9. Platform Pagination (Kwork, Freelancer)
- **Spec**: pipeline-a-spec.md, dev-cycle-spec.md
- **Gap**: No limit=100 pagination for Kwork/Freelancer. Scout only scans first page
- **Effort**: ~6h

### H10. GDPR Erasure/Access Requests
- **Spec**: legal-compliance-spec.md
- **Gap**: No "delete my data" endpoint (30-day GDPR requirement). No data export. No consent tracking. No breach notification system
- **Effort**: ~20h

### H11. Email Bounce Detection & Suppression
- **Spec**: enrichment-spec.md
- **Gap**: HARD_BOUNCE_CODES/SOFT_BOUNCE_CODES defined but not acted upon. Hard bounces should auto-add to suppression list
- **Effort**: ~6h

### H12. Telegram Rate Limiting on Commands
- **Spec**: telegram-bot-spec.md
- **Gap**: No Valkey-backed rate limit per user/command. Spec: 10 cmd/min per user, 5/min for /scan
- **Effort**: ~4h

### H13. Telegram Quiet Hours
- **Spec**: telegram-bot-spec.md
- **Gap**: No 22:00-08:00 notification suppression. Operators get 3am alerts
- **Effort**: ~3h

### H14. HITL Telegram Auto-Notifications
- **Spec**: hitl-spec.md
- **Gap**: HITLQueue.telegram_sent flag exists but no automatic notification on HITL creation. Operators must poll /pending
- **Effort**: ~6h

### H15. WebSocket project:update Auto-Subscribe
- **Spec**: api-spec.md
- **Gap**: Channel defined but not in _DEFAULT_CHANNELS. Clients must manually subscribe. Spec requires auto-subscribe for authenticated users
- **Effort**: ~3h

### H16. Prometheus /metrics Endpoint
- **Spec**: api-spec.md
- **Gap**: MASMetrics class exists but no `/api/v1/metrics` endpoint in Litestar router. Prometheus can't scrape
- **Effort**: ~3h

### H17. Orchestrator Goals DELETE
- **Spec**: api-spec.md
- **Gap**: GET + POST exist, DELETE `/api/v1/orchestrator/goals/{id}` missing
- **Effort**: ~2h

### H18. Loki + Promtail Log Aggregation
- **Spec**: deploy-spec.md (Observability Stack)
- **Gap**: No Loki service in docker-compose. Only local json-file logging
- **Effort**: ~8h

### H19. Grafana Alert Rules
- **Spec**: deploy-spec.md
- **Gap**: Missing 10+ alert definitions (AllAgentsDead, DatabaseDown, etc). No Telegram webhook for critical alerts
- **Effort**: ~8h

### H20. Disaster Recovery Scripts
- **Spec**: deploy-spec.md (Backup & DR)
- **Gap**: No `pg_dump` cron, no S3 backup, no rollback scripts
- **Effort**: ~10h

### H21. Capability Registry
- **Spec**: infrastructure-spec.md
- **Gap**: No `CapabilityRegistry` class. No Scout Capability Report feature
- **Effort**: ~10h

### H22. Property-Based Testing (Hypothesis)
- **Spec**: testing-spec.md
- **Gap**: `tests/property/` exists but empty. No `@given` decorators, no invariant testing
- **Effort**: ~12h

---

## MEDIUM (16)

| # | Feature | Spec | Gap |
|---|---------|------|-----|
| M1 | HITL Plan Review Edit action | dev-cycle-spec.md | Cannot modify agent_sequence via HITL edit |
| M2 | Hardcoded next_agent in agents | dev-cycle-spec.md | Agents still set next_agent explicitly, should use index-only |
| M3 | Revision Target routing | dev-cycle-spec.md | Critic sets target but routing doesn't fully respect it |
| M4 | Scout Custom Rules interpreter | dev-cycle-spec.md | Dashboard UI exists, Scout logic missing |
| M5 | WhatsApp Business API channel | pipeline-b-spec.md | P2, not implemented |
| M6 | LinkedIn Outreach channel | pipeline-b-spec.md | P4, not implemented |
| M7 | Email Templates personalization | enrichment-spec.md | Generic template, no {{first_name}}/{{industry}} |
| M8 | Critic Revision Limit escalation | agents-spec.md | No HITL creation when revision count > 3 |
| M9 | Circuit Breaker success reset | api-spec.md | Time-based only, no success-based HALF_OPEN→CLOSED |
| M10 | Dashboard Bid Kanban | интерфейс.md | Partial — not full 5-column Kanban |
| M11 | Dashboard Design Review UI | интерфейс.md | Missing entirely |
| M12 | Dashboard Geo Map | интерфейс.md | Leaflet + H3 hexagons incomplete |
| M13 | Per-User JWT Rate Limiting | deploy-spec.md | IP-based workaround, JWT-based not done |
| M14 | Secret Rotation Automation | deploy-spec.md | Policy exists, no scripts |
| M15 | Alembic Check in CI | deploy-spec.md | Not in GitHub Actions workflow |
| M16 | Enrichment Waterfall API keys | outreach-spec.md | Hunter/Apollo/Clearbit stubs only |

---

## LOW (11)

| # | Feature | Spec |
|---|---------|------|
| L1 | Dashboard Pipeline Progress Bar | dev-cycle-spec.md |
| L2 | LLM Request Queue Priority | dev-cycle-spec.md |
| L3 | Partial Failure Recovery HITL | dev-cycle-spec.md |
| L4 | RAG-aware Planner (skip re-design) | dev-cycle-spec.md |
| L5 | Scout Category Pre-Filter | dev-cycle-spec.md |
| L6 | Telegram /logs Pagination | telegram-bot-spec.md |
| L7 | Admin User Seeding CLI | api-spec.md |
| L8 | Per-Agent Execution Metrics | api-spec.md |
| L9 | Unsubscribe Link Verification | enrichment-spec.md |
| L10 | Google Dorks Web Search | pipeline-b-spec.md |
| L11 | Pipeline C (Client Growth/Upselling) | ideas/ (future) |

---

## Previously Missed in All Reports (New Findings)

These features were NEVER mentioned in any of the ~8 previous status reports:

1. **Pencil.dev MCP** (C1) — Described in 10 files, MCP server connected, never reported
2. **Design HITL Gates** (C2) — Entire design review workflow missing
3. **Notification System** (C6) — ~20 lines in agents-spec, infrastructure component
4. **WebScoutAgent** (H1) — Entire agent for web-based lead search
5. **TelegramProfileAggregator** (H2) — Batch processing missing
6. **Execution Cloaking** (H7) — Double estimation + delivery throttling
7. **Pipeline B → A Transition** (C5) — Two pipelines disconnected
8. **HITL Viewing Locks** (C9) — Concurrent access protection
9. **Telegram Webhook** (C8) — Only polling mode
10. **Capability Registry** (H21) — Infrastructure component

---

## Recommended Priority Order

### Sprint 1: Pipeline Integration (wire existing code)
- C4: Wire SalesAgent into Pipeline B graph (~8h)
- C5: Pipeline B → A transition endpoint (~10h)
- H4: Integrate Lead Scorer into pipeline (~6h)
- H5: Integrate Touch Sequence into Outreach (~8h)
- H6: Trigger Experience Store save from Packager (~4h)
- H16: Add /metrics endpoint (~3h)
- H17: Add Goals DELETE (~2h)

### Sprint 2: Design Workflow
- C1: Pencil.dev MCP integration (~20h)
- C2: Design HITL gates in graph (~15h)
- M11: Dashboard Design Review UI

### Sprint 3: Production Safety
- C7: Email Warmup Phase (~15h)
- C8: Telegram Webhook (~8h)
- C9: HITL Viewing Locks (~6h)
- H11: Email Bounce Detection (~6h)
- H12: Telegram Rate Limiting (~4h)
- H13: Telegram Quiet Hours (~3h)

### Sprint 4: Infrastructure
- C6: Notification System (~20h)
- H18: Loki + Promtail (~8h)
- H19: Grafana Alerts (~8h)
- H20: Disaster Recovery (~10h)

### Sprint 5: Agents & Adapters
- C3: Portfolio Agent (~30h)
- H1: WebScoutAgent (~25h)
- H8: YouDo/Fiverr/Profi.ru adapters (~30h)

### Sprint 6: Compliance & Quality
- H10: GDPR features (~20h)
- H22: Property-Based Testing (~12h)
- H3: Business Analyzer tiers (~20h)

### Backlog
- All MEDIUM and LOW items
