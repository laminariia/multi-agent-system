# Gap Analysis: Specs vs Implementation (2026-03-11)

## Summary
Full audit of all 14 spec files against codebase. 2515 tests passing, 22 dashboard pages, 60+ API endpoints.

## DONE (fully implemented)
- Pipeline A (10 agents, graph, dynamic routing, execution cloaking, delivery throttling)
- Pipeline B Phase 0-1 (GeoScout + Outreach + HITL)
- Dev Cycle Engine P0-P3 (14 tasks)
- HITL: 8 types + resume + bulk-resolve
- 6-Tier LLM + fallback chains
- API: 60+ endpoints
- Database: 21 tables
- Dashboard: 22 pages
- Deploy: Docker + Railway + CI/CD
- Enrichment waterfall (OSINT -> Hunter -> Apollo)
- Telegram bot (15 commands)
- Platform adapters (5 platforms + rate limiter)
- Security (Semgrep, Fernet, JWT, RBAC, sandbox)
- Semantic cache + checkpoints
- 2515 tests, golden sets, load tests

---

## P1 Gaps (Critical)

### Phase 1: Production Safety (LOW effort, HIGH impact)
| # | Gap | Files Needed | Effort |
|---|-----|-------------|--------|
| 1 | Circuit Breaker (5-failure window, 300s cooldown, half-open) | `src/adapters/circuit_breaker.py` | LOW |
| 2 | LLM Budget Control ($50/day, $500/month, 80% alerts) | `src/core/budget_tracker.py` | LOW |
| 3 | Concurrency Locks (NegotiationLock, ThreadLock) | `src/core/locks.py` | LOW |
| 4 | Email Suppression List (CAN-SPAM/GDPR check before send) | migration + check in email_sender | LOW |
| 5 | HITL Capacity Management (MAX_PENDING=100) | guard in HITL creation | LOW |
| 6 | LLM per-provider rate limiting | intercept in LLMClient | LOW |

### Phase 2: Pipeline A Completion (MEDIUM effort)
| # | Gap | Files Needed | Effort |
|---|-----|-------------|--------|
| 7 | NegotiationEngine (9-state machine) | `src/negotiations/` (6 files) | HIGH |
| 8 | Experience Store (save/retrieve for RAG) | `src/knowledge/experience_store.py` | MEDIUM |
| 9 | Crash Recovery (resume stale threads) | `src/api/main.py` lifespan | MEDIUM |
| 10 | Lead model columns (lead_score, temperature, etc.) | Alembic migration | LOW |

### Phase 3: Pipeline B Phases 2-8 (HIGH effort)
| # | Gap | Files Needed | Effort |
|---|-----|-------------|--------|
| 11 | Lead Scorer (point-based, temperature) | `src/core/lead_scorer.py` | MEDIUM |
| 12 | Business Analyzer (3 tiers) | `src/core/business_analyzer.py` | MEDIUM |
| 13 | SalesAgent (multi-turn negotiations) | `src/agents/sales_agent.py` + prompts | HIGH |
| 14 | Touch Sequence (Day 1/3/5/10 follow-ups) | `src/core/touch_sequence.py` | MEDIUM |
| 15 | Deal Memory CRUD | `src/core/deal_memory.py` | MEDIUM |

### Phase 4: Dashboard & UX
| # | Gap | Files Needed | Effort |
|---|-----|-------------|--------|
| 16 | /bids Kanban page | `_app.bids.tsx` | MEDIUM |
| 17 | /analytics page | `_app.analytics.tsx` | MEDIUM |
| 18 | /projects page | `_app.projects.tsx` | MEDIUM |
| 19 | Leaflet map in Geo/Lead detail | component + react-leaflet | LOW |

---

## P2 Gaps (Nice-to-have)
- Pipeline C (Telegram mining)
- Portfolio Agent [PLANNED]
- WebScoutAgent
- client_context as separate table
- Yandex Maps / 2GIS in GeoScout
- Property-based tests (hypothesis)
- Grafana alert provisioning
- Data retention / cleanup jobs
- ws_events.py dataclasses
- NotificationCenter dropdown
- Email warm-up automation
- agent_logs partitioning
- Orchestrator Hook System, Todo Continuation Enforcer
- Dashboard keyboard shortcuts, Framer Motion animations

---

## Implementation Order
Phase 1 -> Phase 2 -> Phase 3 -> Phase 4

Each task follows: Plan -> Spec -> Save -> Tests (RED) -> Implement (GREEN) -> Refactor -> Review -> Commit
