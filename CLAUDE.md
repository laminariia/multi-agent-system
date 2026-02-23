# Multi-Agent System (MAS)

> Автономная мульти-агентная система из 10 ИИ-агентов для freelance pipeline + business outreach.
> Claude Code: читай этот файл первым. Детали — в docs/.

## Autonomous Mode Protocol

**Default: ACT, Don't ASK.** Execute tasks without confirmation except for TRUE BLOCKERS:
1. Missing secret/credentials not in `.env`
2. Irreversible destructive action (`DROP TABLE`, `force push`, `deploy to production`)
3. Critical ambiguity (>100 lines, 2+ incompatible directions)

When in doubt — match existing codebase patterns.

### Task Classification & Verification

| Type | Signals | Strategy | Verification |
|------|---------|----------|-------------|
| `quick` | 1 file, <20 lines | Handle alone | `ruff check` |
| `standard` | 1-3 files | +1 agent | ruff + `pytest tests/unit/ -x` |
| `complex` | 4+ files, new module | Full team | ruff + full pytest + review |
| `risky` | deploy, delete, creds | ASK user | all above + user report |

### Team Mode (complex tasks)
Spawn agents from `.claude/agents/`: Feature Worker (src/), Quality Worker (tests/), Research Worker (docs/), Infra Worker (docker/), DB Migration Reviewer (read-only). Coordinate via SendMessage + TaskList.

### Git Safety
Work ONLY on `auto/{date}/{slug}` branches. Never touch `main`/`master`. Tag before major changes. Rollback on test failure.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Orchestration | LangGraph 1.0 |
| Backend | **Litestar** + Python 3.12 (NOT FastAPI) |
| Database | PostgreSQL 16 + pgvector + pgvectorscale (`timescale/timescaledb-ha:pg16`) |
| Cache | **Valkey 8.1** (Redis-compatible, BSD-3) |
| Browser | Playwright + Stealth |
| Frontend | Remix + shadcn/ui |
| Embeddings | OpenAI text-embedding-3-large (3072 dim) |
| Deploy | Railway (Docker) |

Full stack details: `TECH_STACK.md`

## LLM Strategy (OpenRouter)

All LLM calls via **OpenRouter** (`OPENROUTER_API_KEY`). Embeddings via OpenAI direct.
- **DeepSeek V3.2**: Scout, Bid, Content, Packager, GeoScout, Outreach
- **Claude Opus 4.6**: Planner, Dev (complex)
- **Claude Sonnet 4.5**: Dev (standard), Critic
- **NanoBanana Pro** (Gemini 3 Pro): Design

Canonical assignments: `TECH_STACK.md` > "LLM Models"

## Pipelines

```
Pipeline A: Scout → Bid → [HITL] → Planner → Dev/Content/Design → Critic → [HITL] → Packager
Pipeline B: GeoScout → Outreach → [HITL] → Email
```

Platforms: Freelancer.com (API), FL.ru (RSS), Kwork (scraper), Upwork (read-only, NO auto-submit!)

## Project Structure

```
src/agents/     — 10 agents (scout, bid, planner, dev, content, design, critic, packager, geo_scout, outreach)
src/core/       — graph.py, state.py, heartbeat.py, semantic_cache.py
src/adapters/   — freelancer.py, upwork.py, fl_ru.py, kwork.py
src/browser/    — stealth.py, session.py, pool.py
src/security/   — semgrep_gate.py + rules/
src/enrichment/ — waterfall.py, osint.py, hunter.py, apollo.py
src/geo/        — h3_scanner.py, overpass.py
src/api/        — main.py (Litestar), websocket.py, routes/
src/bot/        — Telegram bot (HITL interface)
dashboard/      — Remix frontend
tests/          — unit + integration + e2e
docker/         — Dockerfile, docker-compose.yml
```

## Key Rules

- **Litestar** — NOT FastAPI. Launch: `litestar --app src.api.main:app run --reload`
- **Valkey** — NOT Redis. `VALKEY_URL` env var
- **DB**: `DATABASE_URL` (not POSTGRES_URL)
- HITL mandatory for bid submission and final delivery
- Semgrep scan before any code execution
- Heartbeat every 90s, auto-restart at 180s timeout
- Upwork auto-submit is **FORBIDDEN** (ToS violation)
- Email warm-up 6 weeks before production outreach

## Current Status (Feb 2026)

- **2087+ tests** passing, grade A+ (99)
- **Pipeline A**: fully implemented — Scout, Bid, HITL, Planner, Dev, Content, Design, Critic, HITL, Packager
- **Pipeline B**: fully implemented — GeoScout, Outreach, HITL, Email
- **Dashboard**: Remix + shadcn/ui, all CRUD pages, WebSocket real-time, Settings with encrypted credentials
- **Deploy**: Railway (API + Dashboard as separate services)
- **Last major change**: code quality reflex loop — SQL injection prevention, exception narrowing, structured logging

## Documentation Index

| Area | File |
|------|------|
| Architecture | `mas_architecture_v4.2.md`, `technical_implementation_guide.md` |
| Agents | `docs/agent_specifications_core.md`, `docs/agent_specifications_support.md` |
| API & Auth | `docs/api_specification.md`, `docs/auth_specification.md` |
| Database | `docs/database_schema.md`, `docs/langgraph_state.md` |
| Error handling | `docs/edge_cases.md` (revision flow, failure scenarios, alerts) |
| Security | `docs/platform_policies.md`, `docs/playwright_stealth.md` |
| Testing | `docs/testing_strategy.md`, `docs/test_inventory.md` |
| Deploy | `docs/deployment.md`, `docs/ci_cd.md`, `docs/backup_recovery.md` |
| Caching | `docs/semantic_cache.md`, `docs/knowledge_base.md` |
| Business | `docs/deep_research_freelance_market.md`, `docs/legal_compliance.md` |
| Email | `docs/email_warmup.md` |
| GUI | `docs/gui/GUI_SPECIFICATION.md` |
| Env vars | `.env.example` |
| Conventions | `RULES.md` |
| Telegram bot | `docs/telegram_bot.md` |
| Onboarding | `docs/ONBOARDING.md` |
| Code patterns | `.claude/rules/patterns.md` |
| Known gotchas | `.claude/rules/debugging.md` |
