# Production Readiness Plan — Multi-Agent Service v4.2

> **Дата анализа:** 2026-03-16
> **Источник истины:** `docs/Full_work/` (новейшая документация) + `TECH_STACK.md`
> **Методология:** 7 параллельных агентов, полный обход 150 Python модулей, 75+ TSX файлов, 119 .md файлов, 14 миграций, 5 CI/CD workflows

---

## Оценка текущего состояния

| Компонент | Готовность | Оценка |
|-----------|-----------|--------|
| Backend Python (src/) | 95% | A+ |
| Тесты (tests/) | 85% | A- |
| Инфраструктура (Docker/CI) | 90% | A- |
| Dashboard (Remix) | 65% | C+ |
| LLM интеграция (llm_client) | 80% | B |
| Безопасность | 75% | B- |
| Мониторинг | 80% | B+ |
| Документация | 85% | A- |
| **Деплой** | **0%** | **F** (Railway expired) |
| **Production verification** | **15%** | **F** (все тесты на моках) |

**Общая готовность к production: ~60%**

---

## PHASE 0: КРИТИЧЕСКИЕ РАСХОЖДЕНИЯ ДОКУМЕНТАЦИЯ ↔ КОД

### 0.1 LLM Model Assignment Mismatches (CRITICAL)

**TECH_STACK.md** определяет 6-tier систему. `llm_client.py` AGENT_MODEL_REGISTRY корректен, но отдельные файлы агентов содержат захардкоженные НЕПРАВИЛЬНЫЕ модели в логах/docstrings:

| Файл | Строка | Текущее значение | Должно быть (TECH_STACK.md) | Серьёзность |
|------|--------|-----------------|---------------------------|-------------|
| `src/agents/bid.py` | ~443, 532 | `gemini-3-flash` | `gemini-3.1-pro` (Tier 2) | **CRITICAL** — аудит логи врут |
| `src/agents/critic.py` | ~661 | `gpt-4o` | `claude-sonnet-4-6` (Tier 3) | **CRITICAL** — cost tracking врёт |
| `src/agents/content.py` | ~12, 51 | docstring: `Haiku 4.5` | `Claude Sonnet 4.6` (Tier 3) | HIGH — вводит в заблуждение |
| `src/agents/design.py` | ~280 | `claude-sonnet-4-5` | `claude-sonnet-4-6` | HIGH — версия устарела |
| `src/agents/planner.py` | ~12 | fallback: `Sonnet 4.5` | `Sonnet 4.6` | LOW — docstring only |
| `src/agents/dev.py` | ~13-14 | нет routing by complexity | complex=Opus, standard=Sonnet 4.6 | MEDIUM — неполная документация |

**Действие:** Пройтись по ВСЕМ файлам `src/agents/*.py`, привести логируемые модели и docstrings в соответствие с TECH_STACK.md 6-tier системой.

### 0.2 MEMORY.md Outdated (HIGH)

`~/.claude/projects/C--Users-user/memory/MEMORY.md` содержит устаревшие назначения моделей:
- Написано: `Scout/Bid/Content/Packager/GeoScout/Outreach: DeepSeek V3.2`
- Должно быть: 6-tier система (Flash для Scout, Gemini 3.1 Pro для Bid, и т.д.)
- Написано: `Embeddings: text-embedding-3-large (3072 dim)` ✓ корректно
- Написано: `Monthly cost estimate: ~$391/mo` — нужен пересчёт под 6-tier

**Действие:** Обновить MEMORY.md секцию "LLM Model Assignments" до 6-tier системы.

### 0.3 CLAUDE.md Stale References (MEDIUM)

`CLAUDE.md` в проекте ссылается на несуществующие файлы в `docs/`. Документация была консолидирована в `docs/Full_work/`, но CLAUDE.md не обновлён.

**Действие:** Обновить CLAUDE.md, заменить старые пути на `docs/Full_work/specs/` и `docs/Full_work/`.

---

## PHASE 1: ENVIRONMENT & LOCAL DEV (30 min)

### 1.1 Fix .env Configuration

**Текущие проблемы (из findings.md):**

| Переменная | Статус | Действие |
|-----------|--------|---------|
| `DATABASE_URL` | `localhost` — ломается в Docker | Сделать 2 варианта: `.env` (local) и `.env.docker` (postgres:5432) |
| `VALKEY_URL` | `localhost` — ломается в Docker | Аналогично: `valkey:6379` для Docker |
| `OPENAI_API_KEY` | **ПУСТО** | **КРИТИЧНО** — нужен для embeddings (text-embedding-3-large). Без него RAG, semantic cache, knowledge base НЕ РАБОТАЮТ |
| `APOLLO_API_KEY` | Пусто | Нужен для enrichment waterfall (3-й уровень) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Пусто | Нужен для seed admin user |
| `POSTGRES_PASSWORD` | Пусто | docker-compose.prod.yml ожидает |

### 1.2 Fix docker-compose.yml

- [ ] Добавить `dashboard` сервис (отсутствует в dev compose)
- [ ] Проверить корректность hostname resolution между контейнерами
- [ ] Добавить `BROADCAST_URL` для Litestar ChannelsPlugin (WebSocket)

### 1.3 Verify Docker Startup

```bash
docker compose up -d postgres valkey
docker compose run --rm api alembic upgrade head
docker compose up -d
```

**Критерии успеха:** Все сервисы healthy, `/health` возвращает 200.

---

## PHASE 2: DATABASE VERIFICATION (1 hr)

### 2.1 Alembic Migrations (14 штук)

Все 14 миграций существуют, но **НИ РАЗУ не проверены на реальной БД** (все тесты мокают).

- [ ] Прогнать `alembic upgrade head` на чистой PostgreSQL 16
- [ ] Проверить наличие всех таблиц (22 ожидаемых)
- [ ] Проверить pgvector extension и HNSW индексы
- [ ] Проверить pgcrypto и pg_trgm extensions
- [ ] Seed admin user через CLI: `python -m src.cli create_user`

### 2.2 Missing Migration: telegram_notifications

**Из findings.md (P0):** Отсутствует таблица `telegram_notifications` для push уведомлений.

- [ ] Создать миграцию `add_telegram_notifications.py`
- [ ] Поля: id, user_id, event_type, message, sent_at, read_at

### 2.3 Database Connection Pool

**Проблема:** `.env.production.example` не содержит `DATABASE_POOL_MIN` / `DATABASE_POOL_MAX`.

- [ ] Добавить конфигурацию пула: min=5, max=20 (для production)
- [ ] Проверить что `src/core/database.py` использует эти параметры

---

## PHASE 3: LLM VERIFICATION (2-3 hrs)

### 3.1 OpenRouter Connectivity

- [ ] Проверить `OPENROUTER_API_KEY` работает
- [ ] Тест каждой модели из 6-tier системы:
  - Tier 1: `anthropic/claude-opus-4-6` (Planner)
  - Tier 2: `google/gemini-3.1-pro` (Bid, Outreach)
  - Tier 3: `anthropic/claude-sonnet-4-6` (Content, Dev standard, Critic)
  - Tier 4: `google/gemini-3-pro-image-preview` (Design)
  - Tier 5: `google/gemini-2.5-flash` (Scout, GeoScout)
  - Tier 6: `deepseek/deepseek-v3.2` (Packager)
- [ ] Проверить fallback chains (Tier 1 fail → Tier 3 → GPT-4o)

### 3.2 OpenAI Embeddings

- [ ] Проверить `OPENAI_API_KEY` для `text-embedding-3-large`
- [ ] Тест: сгенерировать embedding (3072 dim), записать в pgvector
- [ ] Тест: semantic similarity search с threshold 0.92

### 3.3 Fix Agent Model Hardcodes

- [ ] `bid.py:443,532` — заменить `gemini-3-flash` на `gemini-3.1-pro`
- [ ] `critic.py:661` — заменить `gpt-4o` на `claude-sonnet-4-6`
- [ ] `content.py:12,51` — обновить docstring: Haiku → Sonnet 4.6
- [ ] `design.py:280` — заменить `claude-sonnet-4-5` на `claude-sonnet-4-6`
- [ ] `planner.py:12` — обновить fallback docstring
- [ ] `dev.py:13-14` — добавить complexity routing в docstring

### 3.4 Semantic Cache Verification

- [ ] Тест Valkey RediSearch HNSW index creation
- [ ] Тест semantic cache put/get cycle
- [ ] Проверить TTL по типам: proposal=24h, code=1h, content=12h

---

## PHASE 4: E2E PIPELINE TESTING (2-3 hrs)

### 4.1 Pipeline A End-to-End

**Полный путь:** Scout → Bid → [HITL] → Planner → Dev/Content/Design → Critic → [HITL] → Packager

- [ ] Scout: подать RSS фид FL.ru, получить scored jobs
- [ ] Bid: сгенерировать proposal на qualified job
- [ ] HITL #1: approve bid через API / Telegram bot
- [ ] Negotiation: симулировать client response
- [ ] HITL #2 (dev_launch): approve начало работы
- [ ] Planner: проверить task decomposition + agent_sequence
- [ ] Dev: code generation + Semgrep gate
- [ ] Critic: review cycle (approve/revise/reject)
- [ ] Packager: delivery assembly
- [ ] HITL #3 (final_review): approve delivery

### 4.2 Pipeline B End-to-End

**Полный путь:** GeoScout → Enrichment → Outreach → [HITL] → Email/Telegram

- [ ] GeoScout: H3 scan города, получить businesses из Overpass API
- [ ] Enrichment waterfall: OSINT → Hunter → Apollo
- [ ] Outreach: сгенерировать multi-channel messages
- [ ] HITL: approve outreach batch
- [ ] Email sender: тест отправки (sandbox/Mailtrap)
- [ ] Telegram DM: тест отправки (test channel)

### 4.3 Crash Recovery

- [ ] Убить API mid-pipeline → restart → verify resume from checkpoint
- [ ] HybridCheckpointSaver: Valkey hot + PostgreSQL cold

### 4.4 Negotiation Engine

- [ ] APScheduler poller: проверить polling interval
- [ ] Message classifier: question/counter-offer/acceptance
- [ ] Follow-up scheduler: auto-reminder через 3 дня
- [ ] WebSocket: operator override в реальном времени

---

## PHASE 5: PLATFORM ADAPTERS (2-3 hrs)

### 5.1 Freelancer.com (Primary)

- [ ] OAuth2 authentication
- [ ] Job search API (rate limit: 100 req/hr)
- [ ] Bid submission API (rate limit: 10 bids/hr)
- [ ] Account validation endpoint

### 5.2 FL.ru (Primary)

- [ ] RSS feed parsing (rate limit: 10 req/hr)
- [ ] Job data extraction
- [ ] Manual bid submission flow (нет API для auto-submit)

### 5.3 Kwork (Secondary)

- [ ] Playwright stealth scraping
- [ ] Session rotation + anti-ban
- [ ] Rate limit: 30 pages/hr

### 5.4 Upwork (Read-Only)

- [ ] GraphQL read-only client
- [ ] **VERIFY: NO auto-submit** (ToS violation)
- [ ] Job monitoring only

### 5.5 Telegram Channels

- [ ] Telethon MTProto connection
- [ ] Channel monitoring (job feeds)
- [ ] DM outreach (5 DMs/hr, 12 min intervals)

### 5.6 SMTP / Email

- [ ] SMTP connection (STARTTLS)
- [ ] Bounce detection
- [ ] Suppression list enforcement
- [ ] **Email warmup protocol:** 6 недель → 50 emails/day production

---

## PHASE 6: DASHBOARD OVERHAUL (3-5 hrs) ⚠️ САМЫЙ БОЛЬШОЙ ГЭП

### 6.1 HITL Type Synchronization (CRITICAL)

**Проблема:** Dashboard tabs содержат НЕПРАВИЛЬНЫЕ типы, которых нет в API.

**Dashboard tabs (ТЕКУЩИЕ, неверные):**
- `critic_escalation` ❌ → должно быть `code_review` или `revision`
- `partial_failure` ❌ → должно быть `agent_failure`
- `lead_card` ❌ → нет в API
- `concept_review` ❌ → нет в API
- `concept_approved` ❌ → нет в API
- `design_review` ❌ → нет в API
- `portfolio_review` ❌ → нет в API
- `final_delivery` ❌ → должно быть `final_review`

**API поддерживает 14+ типов:**
`bid_approval`, `code_review`, `delivery`, `revision`, `scope_creep`, `plan_review`, `alert`, `email_approval`, `dev_launch`, `final_review`, `job_review`, `agent_failure`, `delivery_hold`, `manual_action`, `outreach_approval`

- [ ] Привести dashboard HITL tabs в полное соответствие с API типами
- [ ] Добавить отсутствующие типы: `code_review`, `delivery`, `revision`, `scope_creep`, `alert`, `job_review`, `agent_failure`, `delivery_hold`, `manual_action`, `outreach_approval`
- [ ] Исправить неправильные имена: `final_delivery` → `final_review`

### 6.2 WebSocket Integration (HIGH)

**Проблема:** Dashboard использует polling (React Query refetchInterval) вместо WebSocket.

Backend полностью поддерживает WebSocket (Litestar ChannelsPlugin, 9 каналов), но dashboard НЕ подключается.

- [ ] Реализовать WebSocket клиент в dashboard (JWT auth через message)
- [ ] Подключить каналы: `agent:heartbeat`, `hitl:new`, `hitl:resolved`, `project:update`, `notification`
- [ ] Заменить polling на real-time updates для: agent status, HITL queue, notifications
- [ ] Сохранить React Query как fallback (staleTime + WebSocket invalidation)

### 6.3 Analytics Page Fix (HIGH)

**Проблема:** `fetchAnalytics({ days: 30 })` не соответствует структуре API.

API имеет 5 отдельных endpoint'ов:
- `GET /api/v1/analytics/overview`
- `GET /api/v1/analytics/pipeline-a`
- `GET /api/v1/analytics/pipeline-b`
- `GET /api/v1/analytics/revenue`
- `GET /api/v1/analytics/agents`

- [ ] Переписать `_app.analytics.tsx` — 5 отдельных `useQuery()` вызовов
- [ ] Убрать mock/stub данные
- [ ] Подключить реальные графики: Pipeline A/B funnels, Revenue, LLM Costs

### 6.4 Portfolio Image Upload (MEDIUM)

- [ ] Добавить компонент загрузки изображений
- [ ] Исправить field name mismatches: `image_url`/`thumbnail_url`, `demo_url`/`url`
- [ ] Добавить `completed_at` в форму создания

### 6.5 Geo Page Fix (MEDIUM)

- [ ] Categories selected но НЕ передаются в API запрос — подключить фильтр
- [ ] Добавить недостающие категории из spec (22 типа бизнесов)

### 6.6 Remove Hardcoded Data (MEDIUM)

Привести к динамической загрузке из API/settings:
- [ ] Geo categories → из API или settings
- [ ] Job statuses → из API
- [ ] Platform options → из API
- [ ] Deal statuses → из API enum
- [ ] Lead categories → из settings
- [ ] Portfolio categories → из API

### 6.7 Missing Dashboard Features

- [ ] Users management page (API есть, UI нет)
- [ ] Agent pause/resume/restart buttons
- [ ] Deal → "Start Development" bridge button (Pipeline B → Pipeline A)
- [ ] Orchestrator logs viewer
- [ ] Pipeline progress on job detail page
- [ ] Manual pipeline execution button

---

## PHASE 7: TELEGRAM BOT VERIFICATION (1-2 hrs)

- [ ] Все 16 handlers работают: /start, /status, /pending, /stats, /approve, /skip, /scan, /run, /stop, /orch, /goals, /health, /milestones, /logs, /add_goal
- [ ] Inline keyboards: `orch:*` callback pattern
- [ ] Push notifications: session_complete, critical_error, all_goals_done
- [ ] HITL quick actions через Telegram (approve/reject bid)

---

## PHASE 8: SECURITY HARDENING (2-3 hrs)

### 8.1 HTTPS Configuration (CRITICAL)

**Проблема:** `nginx.conf` имеет HTTPS блок, но он **ЗАКОММЕНТИРОВАН**.

- [ ] Получить SSL сертификат (Let's Encrypt / Cloudflare)
- [ ] Раскомментировать HTTPS блок в nginx.conf
- [ ] Настроить auto-renewal
- [ ] HTTP → HTTPS redirect

### 8.2 Rate Limiting Fix (CRITICAL)

**Проблема:** На Railway все клиенты коллапсируют на IP `100.64.0.3`. `$binary_remote_addr` в nginx бесполезен.

- [ ] Реализовать per-user rate limiting через JWT claims в Litestar
- [ ] Или использовать `X-Forwarded-For` header (если Railway передаёт)
- [ ] Или перейти на VPS где клиентские IP реальные

### 8.3 Security Audit

- [ ] `semgrep --config p/python --config p/owasp-top-ten src/`
- [ ] `pip audit` — vulnerability scan зависимостей
- [ ] `gitleaks detect` — поиск утечек секретов
- [ ] Проверить CSP headers (текущий `unsafe-inline` для стилей)
- [ ] Проверить CORS_ALLOWED_ORIGINS в production
- [ ] Проверить rejection of default JWT/ENCRYPTION keys when DEBUG=False

### 8.4 Production Validators

- [ ] `JWT_SECRET_KEY` ≠ default → block startup
- [ ] `ENCRYPTION_KEY` ≠ default → block startup
- [ ] `ADMIN_PASSWORD` strength check
- [ ] No stack traces in error responses (production mode)

---

## PHASE 9: MONITORING & ALERTING (1-2 hrs)

### 9.1 Metrics Audit

**Проблема:** Alerting rules ссылаются на метрики, которые ВОЗМОЖНО не экспортируются.

- [ ] Проверить что `hitl_queue_size` метрика реально экспортируется
- [ ] Проверить что `db_pool_checked_out` / `db_pool_size` метрики экспортируются
- [ ] Добавить недостающие метрики или убрать алерты

### 9.2 Alert Routing

**Проблема:** Alerts определены, но **нет destination** (Prometheus fires → никуда не отправляет).

- [ ] Настроить Alertmanager → Telegram bot notifications
- [ ] Или Alertmanager → email
- [ ] Тест: вызвать alert, проверить доставку

### 9.3 Missing Alerts

- [ ] Container OOM approaching (memory usage > 85%)
- [ ] PostgreSQL slow queries (> 5s)
- [ ] SSL Certificate expiry (< 14 days)
- [ ] LLM API errors spike
- [ ] Pipeline completion rate drop

### 9.4 Grafana Dashboards

- [ ] Проверить что все dashboards в `docker/monitoring/grafana/` загружаются
- [ ] Добавить dashboard для Pipeline A/B throughput
- [ ] Agent performance dashboard (latency, success rate per agent)

### 9.5 Sentry Integration

- [ ] Проверить `SENTRY_DSN` в .env
- [ ] Настроить error grouping
- [ ] Performance monitoring (traces)
- [ ] Release tracking

---

## PHASE 10: LOAD TESTING (1-2 hrs)

- [ ] Locust: 10 concurrent users → all endpoints stable
- [ ] Locust: 50 concurrent users → identify bottlenecks
- [ ] Locust: 100 concurrent users → verify rate limiting + graceful degradation
- [ ] PostgreSQL: connection pool не исчерпывается
- [ ] Valkey: memory не превышает 80%
- [ ] LLM: rate limits OpenRouter не блокируют pipeline

---

## PHASE 11: DEPLOY SOLUTION (Variable) ⚠️ БЛОКЕР

### 11.1 Hosting Decision

**Railway expired.** Варианты:

| Вариант | Стоимость | Плюсы | Минусы |
|---------|----------|-------|--------|
| **Hetzner VPS** | ~€20-40/mo | Full control, Docker native, static IP | Manual ops |
| **DigitalOcean** | ~$30-60/mo | Managed DB, App Platform | Higher cost |
| **Railway (renew)** | ~$20/mo | Existing config ready | IP collapse rate limiting issue |
| **Fly.io** | ~$30/mo | Edge deployment | Less familiar |

**Рекомендация:** Hetzner CX32 (4 vCPU, 8GB RAM, ~€15/mo) — лучший cost/performance для self-hosted Docker setup. Реальные клиентские IP (rate limiting работает). Full control.

### 11.2 Domain & DNS

- [ ] Зарегистрировать домен
- [ ] Настроить DNS A-record
- [ ] SPF/DKIM/DMARC для email outreach (ОБЯЗАТЕЛЬНО для deliverability)

### 11.3 Email Infrastructure

- [ ] 3 домена: primary brand + 2 outreach (per spec)
- [ ] SMTP server (Resend / Postmark / собственный)
- [ ] **Email warmup: 6 НЕДЕЛЬ** до production email outreach
  - Week 1-2: 5-10 emails/day
  - Week 3: 20/day
  - Week 4: 30/day
  - Week 5-6: 50/day (production ready)

### 11.4 Backup Strategy

- [ ] PostgreSQL: daily pg_dump → S3/object storage
- [ ] Valkey: RDB snapshots
- [ ] Verify restore procedure
- [ ] Automated backup testing (monthly)

---

## PHASE 12: MISSING FEATURES (4-8 hrs)

### P0 — Critical (Без них система не работает полноценно)

| # | Feature | Файлы | Оценка |
|---|---------|-------|--------|
| 1 | **OPENAI_API_KEY** setup | `.env` | 5 min |
| 2 | **telegram_notifications** migration | `alembic/versions/` | 30 min |
| 3 | **Admin seed** command | `src/cli/`, `.env` | 15 min |
| 4 | **Email warmup automation** | `src/enrichment/` | 2 hrs |
| 5 | **Data retention jobs** (90d leads, 12m profiles) | `src/worker/tasks.py` | 1 hr |
| 6 | **Dashboard ↔ API field alignment** | `dashboard/app/routes/` | 2 hrs |

### P1 — Important (Нужно для полноценной работы)

| # | Feature | Файлы | Оценка |
|---|---------|-------|--------|
| 7 | **BusinessAnalyzer** medium/deep tiers | `src/core/business_analyzer.py` | 3 hrs |
| 8 | **NegotiationEngine** → Pipeline A bridge | `src/negotiations/`, `src/core/graph.py` | 2 hrs |
| 9 | **GDPR handlers** (right to be forgotten, consent) | `src/api/routes/`, `src/core/` | 3 hrs |
| 10 | **Telegram bot rate limiting** | `src/bot/` | 1 hr |
| 11 | **WebSocket type generation** (`generate_ws_types.py`) | `scripts/` | 1 hr |
| 12 | **Concurrency locks** (bid-level, thread-level) | `src/core/` | 2 hrs |
| 13 | **Dynamic routing validation** | `src/core/graph.py` | 1 hr |
| 14 | **Touch sequence scheduler** | `src/core/touch_sequence.py` | 2 hrs |

### P2 — Nice to Have (Production работает без них)

| # | Feature | Файлы | Оценка |
|---|---------|-------|--------|
| 15 | Portfolio Agent (planned) | `src/agents/` | 4 hrs |
| 16 | Fiverr adapter | `src/adapters/` | 3 hrs |
| 17 | WhatsApp integration | `src/enrichment/` | 4 hrs |
| 18 | Dynamic capability registry | `src/core/` | 3 hrs |
| 19 | A/B testing proposals | `src/agents/bid.py` | 2 hrs |
| 20 | Timezone-optimized delivery | `src/core/delivery_scheduler.py` | 1 hr |

---

## PHASE 13: FINAL QA & DOCUMENTATION (2-3 hrs)

### 13.1 Integration Tests with Real Services

**Проблема:** Все 3,207 тестов используют моки. Ни один не проверен на реальных сервисах.

- [ ] Integration tests: PostgreSQL + Valkey (docker-compose.test.yml)
- [ ] LLM integration: реальные вызовы к OpenRouter (хотя бы golden set)
- [ ] Platform adapter tests: FL.ru RSS real feed
- [ ] Email: тест через Mailtrap или sandbox

### 13.2 E2E Tests Alignment

**Проблема:** E2E tests в CI запускают `litestar run` вручную, а не через docker-compose.

- [ ] Переписать CI E2E job на docker-compose
- [ ] Добавить E2E тесты для ВСЕХ HITL flows

### 13.3 Documentation Updates

- [ ] Обновить CLAUDE.md (убрать stale doc references)
- [ ] Обновить MEMORY.md (6-tier LLM system)
- [ ] Создать deployment runbook
- [ ] Создать incident response playbook
- [ ] Обновить onboarding.md с актуальными путями

### 13.4 Smoke Test Checklist

- [ ] `/health` → 200
- [ ] Login → JWT token
- [ ] Create job → visible in dashboard
- [ ] Run Scout → jobs discovered
- [ ] Approve bid → HITL resolved
- [ ] WebSocket → events received
- [ ] Telegram bot → responds to /status
- [ ] Prometheus → metrics scraped
- [ ] Grafana → dashboards loaded

---

## TIMELINE ESTIMATE

| Phase | Название | Часы | Зависимости |
|-------|---------|------|-------------|
| 0 | Doc ↔ Code fixes | 1-2 | — |
| 1 | Environment & Local Dev | 0.5 | — |
| 2 | Database Verification | 1 | Phase 1 |
| 3 | LLM Verification | 2-3 | Phase 1, OPENAI_API_KEY |
| 4 | E2E Pipeline Testing | 2-3 | Phase 2, 3 |
| 5 | Platform Adapters | 2-3 | Phase 1, реальные credentials |
| 6 | Dashboard Overhaul | 3-5 | Phase 4 (HITL types) |
| 7 | Telegram Bot | 1-2 | Phase 2 |
| 8 | Security Hardening | 2-3 | Phase 11 (HTTPS нужен домен) |
| 9 | Monitoring & Alerting | 1-2 | Phase 1 |
| 10 | Load Testing | 1-2 | Phase 4 |
| 11 | Deploy Solution | 2-4 | **РЕШЕНИЕ ПО ХОСТИНГУ** |
| 12 | Missing Features (P0+P1) | 4-8 | Phase 4 |
| 13 | Final QA | 2-3 | ВСЕ предыдущие |

**TOTAL: ~25-45 часов** (зависит от выбора хостинга и scope P1/P2 фич)

---

## КРИТИЧЕСКИЙ ПУТЬ (Минимум для production)

```
Phase 0 (fixes) → Phase 1 (.env) → Phase 2 (DB) → Phase 3 (LLM)
                                                        ↓
Phase 11 (hosting) → Phase 8 (security) ──────→ Phase 4 (E2E)
                                                        ↓
                                          Phase 6 (dashboard) + Phase 12 (P0 features)
                                                        ↓
                                                   Phase 13 (QA)
```

**Минимальное время до production: ~20-25 часов** (если параллелить Phase 5-7-9-10)

---

## РЕШЕНИЯ, ТРЕБУЮЩИЕ ВАШЕ ВНИМАНИЕ

1. **Хостинг:** Hetzner VPS / DigitalOcean / Railway renew / другое?
2. **SMTP:** Gmail App Password / Resend / Postmark / собственный?
3. **OPENAI_API_KEY:** Нужен для embeddings. Есть ли ключ?
4. **Домен:** Какой домен для production?
5. **Email warmup:** Готовы ли к 6-недельному warmup перед production outreach?
6. **Pipeline C (Telegram mining):** В scope или отложить?
7. **BrightData proxy:** Нужен для Kwork/Upwork scraping. Есть ли аккаунт?
8. **GDPR:** Целевой рынок — Россия/EU/Global? Определяет compliance scope.
9. **SalesAgent tool stubs:** Реализовать Phase 3 integrations (BusinessAnalyzer, Google Places) сейчас или позже?
