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
| Database | PostgreSQL 16 + pgvector (HNSW indexes) |
| Cache | **Valkey 8.1** (Redis-compatible, BSD-3) |
| Browser | Playwright + Stealth |
| Frontend | Remix + shadcn/ui |
| Embeddings | Qwen3-Embedding-8B (3072 dim Matryoshka, via OpenRouter) |
| Deploy | Railway (Docker) |

Full stack details: `TECH_STACK.md`

## LLM Strategy (OpenRouter)

All LLM calls via **OpenRouter** (`OPENROUTER_API_KEY`). Embeddings also via OpenRouter (qwen/qwen3-embedding-8b).

**6-Tier System** (canonical source: `MASTER-VISION.md` Section 4):
- **Tier 1 (Reasoning):** Claude Opus 4.6 → Planner, Dev (complex), SalesAgent [PLANNED]
- **Tier 2 (Client-facing):** Gemini 3.1 Pro → Bid, Outreach
- **Tier 3 (Content+Review):** Claude Sonnet 4.6 → Content, Dev (standard), Critic
- **Tier 4 (Design):** NanoBanana Pro → Design
- **Tier 5 (Extraction):** Gemini 2.5 Flash → Scout, GeoScout
- **Tier 6 (Simple):** DeepSeek V3.2 → Packager, Portfolio Agent [PLANNED]

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

## Current Status (Sep 2026)

- **Source of truth**: README «Статус реализации» — what runs in runtime vs. implemented and tested but not wired yet
- **Tests**: ~5,400 unit/integration/property/golden + 44 Playwright E2E; GitHub Actions CI runs all of them, plus Semgrep, Trivy, gitleaks and `alembic check`
- **Pipeline A in runtime**: Scout cycle; Dev Cycle Engine (Planner → Dev/Content/Design → Critic → Packager → final_review HITL). Bid generation and submission are implemented but not wired
- **Pipeline B in runtime**: GeoScout → Outreach → outreach-approval HITL; sending approved emails is not wired
- **Dashboard + Telegram bot**: Remix + shadcn/ui, WebSocket updates; HITL cards in Telegram
- **Deploy**: Railway (API + Dashboard as separate services); the CI deploy job is opt-in via the `RAILWAY_DEPLOY` repo variable

## Documentation Index

**Single Source of Truth:** `docs/Full_work/MASTER-VISION.md`
**Navigation:** `docs/Full_work/MAS.md` (index of all specs)

| Area | File |
|------|------|
| Vision & Architecture | `docs/Full_work/MASTER-VISION.md` |
| Agents (10 impl + 2 planned) | `docs/Full_work/specs/agents-spec.md` |
| Pipeline A (freelance) | `docs/Full_work/pipeline-a-spec.md` |
| Pipeline B (direct sales) | `docs/Full_work/pipeline-b-spec.md` |
| Dev Cycle Engine | `docs/Full_work/dev-cycle-spec.md` |
| API & Auth | `docs/Full_work/specs/api-spec.md` |
| Database | `docs/Full_work/specs/database-spec.md` |
| Orchestration | `docs/Full_work/specs/orchestrator-spec.md` |
| Testing | `docs/Full_work/specs/testing-spec.md` |
| Deploy & CI/CD | `docs/Full_work/specs/deploy-spec.md` |
| Security | `docs/Full_work/specs/security-spec.md` |
| HITL & Approval | `docs/Full_work/specs/hitl-spec.md` |
| Enrichment & OSINT | `docs/Full_work/specs/enrichment-spec.md` |
| Geo Targeting | `docs/Full_work/specs/geo-scout-spec.md` |
| Telegram Bot | `docs/Full_work/specs/telegram-bot-spec.md` |
| Outreach | `docs/Full_work/specs/outreach-spec.md` |
| Sales & Negotiation | `docs/Full_work/specs/sales-agent-spec.md`, `specs/negotiation-spec.md` |
| RAG & Memory | `docs/Full_work/specs/rag-memory-spec.md` |
| LLM Models | `docs/Full_work/specs/llm-spec.md` |
| Platform Adapters | `docs/Full_work/specs/platform-adapters-spec.md` |
| Legal & Compliance | `docs/Full_work/specs/legal-compliance-spec.md` |
| Infrastructure | `docs/Full_work/specs/infrastructure-spec.md` |
| UI/Interface | `docs/Full_work/интерфейс.md` |
| Onboarding | `docs/Full_work/onboarding.md` |
| Env vars | `.env.example` |
| Conventions | `RULES.md` |
| Code patterns | `.claude/rules/patterns.md` |
| Known gotchas | `.claude/rules/debugging.md` |

## Docs-First Development Gate

**STATUS: LOCKED** — Documentation must be completed and approved before any code is written.

Rules while gate is locked:
1. Do NOT write, generate, or suggest implementation code (even in responses)
2. Focus ALL work on `docs/` — Overview.md, TechSpec.md, feature specs
3. When tempted to code, add it to `docs/features/` instead
4. Check gate status: `/docs-status`
5. Use existing skills for heavy lifting:
   - `brainstorming-ideas` for Overview.md
   - `architect` agent for TechSpec.md
   - `research-analysis` for deep research on features
   - `quality-reviewer` via `/docs-audit` for completeness check
6. Approve docs when ready: `/docs-approve all`
7. After unlock: use `writing-plans` skill to create PLANS/, then develop

**Do NOT remove this section manually.** Use `/docs-approve` to unlock the gate.
