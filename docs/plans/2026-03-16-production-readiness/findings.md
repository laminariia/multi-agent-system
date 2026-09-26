# Findings & Decisions — MAS Production Readiness

## Requirements
**Цель:** Полноценный production-ready сервис, не MVP.

### Функциональные
- 11 агентов работают с реальными LLM через OpenRouter
- Pipeline A (freelance) и Pipeline B (direct sales) проходят end-to-end
- Dashboard показывает реальные данные, WebSocket real-time
- Telegram bot для HITL approvals и notifications
- 5 platform adapters подключены к реальным API
- Enrichment waterfall (OSINT → Hunter → Apollo) с реальными ключами

### Нефункциональные
- Security: OWASP Top 10, Semgrep, encrypted credentials
- Performance: p95 < 500ms API, 10 concurrent pipelines
- Reliability: Circuit breaker, crash recovery, heartbeat monitoring
- Observability: Grafana dashboards, Sentry alerts, structured logging
- Deploy: Railway CI/CD, zero-downtime, automated backups

---

## Research Findings

### Codebase Inventory (2026-03-16)
- **src/agents/**: 11 файлов, ~6,600 LOC — все агенты реализованы
- **src/core/**: 30 файлов — graph.py (2,221 LOC), state, models, all production safety
- **src/api/routes/**: 18 файлов, 16 controllers — полный REST API
- **src/adapters/**: 5 платформ + rate_limiter + telegram_channels
- **src/enrichment/**: waterfall + osint + hunter + apollo + email_sender + telegram_sender
- **src/browser/**: stealth + session + pool (Playwright)
- **src/security/**: semgrep_gate + encryption
- **src/bot/**: handler + commands + orchestrator_commands + keyboards + notifications (~7,000 LOC total)
- **src/core/models.py**: 21 ORM models (SQLAlchemy 2.0 Mapped style)
- **dashboard/**: 26 Remix routes + components + types

### Test Inventory
- **3,207 test functions** across 162 files
- **132 unit**, 22 integration, 8 e2e test files
- **2,972 passed**, 0 failed, 302 warnings (12:55 runtime)
- **1,539 async tests** (pytest-asyncio)
- All tests use mocks — no real DB, Valkey, or LLM calls

### Stubs & Incomplete Code
- `src/core/checkpoints.py`: NotImplementedError on sync methods — **intentional** (async-only)
- `src/core/business_analyzer.py`: medium/deep tiers are stubs for external APIs
- No other NotImplementedError/TODO/FIXME found in src/

### Docker Setup
- **Dockerfile** (API): Python 3.12-slim, multi-stage build, Playwright + Chromium, non-root user, healthcheck
- **dashboard/Dockerfile**: Node 20-slim, multi-stage, remix-serve
- **docker-compose.yml** (dev): PostgreSQL, Valkey, API, Worker, Bot — **needs dashboard service + env fixes**
- **docker-compose.prod.yml**: FULL stack — all of dev + dashboard + nginx + certbot + prometheus + grafana + valkey-exporter + postgres-exporter. Networks (frontend/backend), resource limits, read_only, security_opt. Properly overrides DATABASE_URL/VALKEY_URL with container hostnames.
- **init.sql**: vector + vectorscale + pgcrypto + pg_trgm extensions
- **railway.toml**: Dockerfile builder, Alembic retry loop, healthcheck at /health

### Environment (.env) — Current State
- DATABASE_URL: set (localhost — needs Docker override)
- VALKEY_URL: set (localhost — needs Docker override)
- OPENROUTER_API_KEY: set (old key exists, user provided new one)
- JWT_SECRET_KEY: set (generated)
- ENCRYPTION_KEY: set (Fernet key)
- TELEGRAM_BOT_TOKEN: set (bot already created!)
- TELEGRAM_CHAT_ID: set
- OPENAI_API_KEY: EMPTY — needed for embeddings
- APOLLO_API_KEY: not set — user provided key
- ADMIN_EMAIL/PASSWORD: not set — needed for seed user
- POSTGRES_PASSWORD: not in .env — needed for docker-compose

### Deploy Status
- **Railway**: subscription EXPIRED — not available as deploy target
- **VPS deploy**: supported via docker-compose.prod.yml + SSH action
- **Local Docker**: primary dev path, docker-compose.yml
- **CI/CD**: .github/workflows/ci.yml exists

### Database
- **14 Alembic migrations** (2026-02-07 → 2026-03-13)
- **init.sql**: referenced in docker-compose for initial schema + pgvector extension
- **pgvector**: HNSW indexes (migrated from DiskANN)
- **21 tables**: users, jobs, bids, projects, deliverables, agent_logs, hitl_queue, checkpoints, knowledge_base, orchestrator_goals, telegram_channels, campaigns, outreach_messages, leads, deals, scheduled_messages, portfolio_projects, + more

### Environment Variables
- **.env.example**: 89 lines, well-documented
- **Required**: DATABASE_URL, VALKEY_URL, OPENROUTER_API_KEY, JWT_SECRET_KEY, ENCRYPTION_KEY
- **Optional**: OPENAI_API_KEY, HUNTER_API_KEY, APOLLO_API_KEY, TELEGRAM_BOT_TOKEN, SMTP_*, SENTRY_DSN
- **Docker issue**: DATABASE_URL uses `localhost` — needs `postgres` hostname inside Docker

### LLM Configuration
- **6-tier system** via OpenRouter (single API key):
  - Tier 1: Claude Opus 4.6 (Planner, Dev complex)
  - Tier 2: Gemini 3.1 Pro (Bid, Outreach)
  - Tier 3: Claude Sonnet 4.6 (Content, Dev standard, Critic)
  - Tier 4: NanoBanana Pro (Design)
  - Tier 5: Gemini 2.5 Flash (Scout, GeoScout)
  - Tier 6: DeepSeek V3.2 (Packager)
- Fallback chains configured in `src/core/llm_client.py`

### Known Issues
- 302 pytest warnings — `@pytest.mark.asyncio` on sync test functions
- 1 flaky test: `test_loop_survives_check_error` (timing-dependent)
- docker-compose missing dashboard service
- DATABASE_URL/VALKEY_URL hostnames wrong for Docker networking

---

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Litestar (not FastAPI) | Project convention, already implemented throughout |
| Valkey (not Redis) | BSD-3 license, Redis-compatible, project convention |
| OpenRouter single key | One API key for all 6 LLM tiers, simpler key management |
| pgvector HNSW (not DiskANN) | HNSW is stable in pgvector, DiskANN experimental |
| Railway deploy | Already configured, Dockerfiles ready, separate API + Dashboard services |
| timescaledb-ha:pg16 | Includes pgvector extension out of the box |
| Multi-stage Docker | Smaller production images, build deps not in runtime |
| Non-root user in Docker | Security best practice |
| Playwright + Stealth | Anti-detection for scraping platforms |
| Fernet encryption | Symmetric encryption for stored credentials in DB |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| (пока пусто) | |

## Resources

### Documentation
- MASTER-VISION: `docs/Full_work/MASTER-VISION.md` — Single Source of Truth
- Gap Analysis: `docs/plans/2026-03-11-gap-analysis.md`
- All specs: `docs/Full_work/specs/` (14 spec files)
- Debugging notes: `.claude/rules/debugging.md`
- Code patterns: `.claude/rules/patterns.md`

### External Services
- OpenRouter: https://openrouter.ai/keys
- Railway: https://railway.app
- BotFather: https://t.me/BotFather
- Hunter.io: https://hunter.io/api
- Apollo.io: https://www.apollo.io/api

---

*Update this file after every 2 view/browser/search operations*
