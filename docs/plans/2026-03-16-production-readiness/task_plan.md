# Task Plan: MAS — Full Production Deployment (v2 — corrected)

## Goal
Довести Multi-Agent Service от состояния "код написан, тесты моки" до полностью рабочего production-сервиса с реальными интеграциями, мониторингом, безопасностью и деплоем.

## Current Phase
Phase 0 (re-analysis complete)

## Corrected Baseline (после глубокого анализа 2026-03-16)

### Код — 95%+ COMPLETE
- **~33,000 LOC** Python в src/ (150 файлов)
- **11 агентов** — ВСЕ полностью реализованы (0 stubs, 0 TODO)
- **30 core модулей** — graph.py (2,221 LOC), все production safety, все state management
- **16 API controllers** — полный REST API, 50+ endpoints
- **26 dashboard routes** — ВСЕ полностью реализованы (10,643 LOC, 0 stubs)
- **28 custom компонентов** + shadcn/ui + recharts + react-leaflet + dnd-kit + framer-motion
- **WebSocket** — полная интеграция (10 каналов, reconnect, per-route subscriptions)
- **21 ORM модель** + 14 Alembic миграций
- **0 broken imports**, 0 compilation errors, 0 orphaned code

### Тесты — 85% COMPLETE (unit excellent, integration/e2e need real services)
- **3,207+ test functions** в 180 файлах (132 unit, 21 integration, 4 e2e)
- **2,972 passed**, 0 failed, 302 warnings
- **70% coverage floor** enforced в CI
- **Golden set** — 10 файлов LLM regression tests
- **Load tests** — Locust suite (3 user classes, distributed workers)
- **ВСЕ тесты используют моки** — 0 тестов с реальными DB/Valkey/LLM

### Инфраструктура — 90% COMPLETE (всё существует, нужна верификация)
- **4 docker-compose файла**: dev, prod, test, loadtest
- **docker-compose.prod.yml**: ПОЛНЫЙ stack (10 сервисов) — postgres, valkey, api, worker, bot, dashboard, nginx, prometheus, grafana, exporters
- **nginx.conf**: EXISTS — security headers (CSP, HSTS, X-Frame), rate limiting, WebSocket proxy
- **Prometheus**: EXISTS — 3 scrape targets, 6 alert rules
- **Grafana**: EXISTS — provisioned datasource + mas-overview.json dashboard
- **5 GitHub workflows**: ci.yml (6-stage), deploy.yml, deploy-notify.yml, pr-checks.yml, setup-services.yml
- **8 helper scripts** в scripts/
- **2 railway.toml** (API + Dashboard)
- **Security scanning**: Semgrep + Trivy + Gitleaks в CI

### .env — Частично настроен
- SET: DATABASE_URL, VALKEY_URL, OPENROUTER_API_KEY, JWT_SECRET, ENCRYPTION_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
- EMPTY: OPENAI_API_KEY, APOLLO_API_KEY, ADMIN_EMAIL/PASSWORD, POSTGRES_PASSWORD
- ISSUE: DATABASE_URL/VALKEY_URL используют localhost (не работает в Docker)

### Деплой — BLOCKED
- Railway: подписка EXPIRED
- VPS: docker-compose.prod.yml готов, но нет сервера
- CI/CD: полностью настроен (GitHub Actions → Railway), но Railway недоступен

### Незакрытые фичи из спеков (34 NOT IMPLEMENTED)
**Критичные:**
- SalesAgent: off-graph, не интегрирован в pipeline
- NegotiationEngine: off-graph singleton
- Email warmup: стратегия есть, автоматизации нет
- telegram_notifications table: схема есть, миграция нет
- Business Analyzer medium/deep: stubs

**Telegram Bot (9 фич):**
- Edit flow, group chats, webhook mode, multi-user notifications, notification preferences, rate limiting commands, timeout/expiry alerts, quiet hours, audit table

**Legal/GDPR (5 фич):**
- Automated data deletion, consent management UI, data export, multi-language templates, right to be forgotten

**Channels (2):**
- WhatsApp Business API, LinkedIn outreach

**Другое:**
- Fiverr adapter, Dynamic Capability Registry, client_context table, data retention jobs

---

## Revised Phases

### Phase 1: Local Dev Environment (QUICK — 30 min)
Починить docker-compose.yml (dev) и .env для локального запуска.

- [ ] 1.1 Добавить dashboard сервис в docker-compose.yml
- [ ] 1.2 Добавить environment overrides (DATABASE_URL → postgres, VALKEY_URL → valkey)
- [ ] 1.3 Обновить .env — добавить OPENROUTER_API_KEY (новый), APOLLO_API_KEY, POSTGRES_PASSWORD, ADMIN_EMAIL/PASSWORD
- [ ] 1.4 Запустить Docker Desktop
- [ ] 1.5 `docker compose up` — проверить все сервисы healthy
- [ ] 1.6 Alembic миграции на реальной PostgreSQL
- [ ] 1.7 Smoke test: /health → 200, /schema → OpenAPI UI
- [ ] 1.8 Auth flow: register → login → JWT token
- [ ] 1.9 Dashboard: открывается, login работает
- **Status:** pending
- **Критерий:** Все 6 сервисов (postgres, valkey, api, worker, bot, dashboard) running, /health 200

### Phase 2: Database & Seed Data (1 час)
Верифицировать все 21 таблицу и засеять данные для тестирования.

- [ ] 2.1 Проверить все 14 миграций идемпотентны (upgrade → downgrade → upgrade)
- [ ] 2.2 Проверить pgvector HNSW индексы в реальной БД
- [ ] 2.3 Проверить FK constraints
- [ ] 2.4 Seed: admin user, 5 тестовых jobs, 3 leads, 1 campaign
- [ ] 2.5 CRUD тест через API: создать job → bid → lead → deal
- [ ] 2.6 Valkey: SET/GET, pub/sub тест
- [ ] 2.7 Semantic cache: embed → store → retrieve (нужен OPENAI_API_KEY для embeddings)
- **Status:** pending
- **Критерий:** Все таблицы, индексы, FK на месте; CRUD работает; Valkey отвечает

### Phase 3: LLM & Agent Verification (2-3 часа)
Каждый из 11 агентов вызывает реальный LLM и получает осмысленный ответ.

- [ ] 3.1 Проверить OpenRouter API key — баланс, доступность моделей
- [ ] 3.2 LLMClient: тест каждого из 6 tiers
- [ ] 3.3 Fallback chain: primary unavailable → fallback works
- [ ] 3.4 Budget tracker: реальный Valkey INCR
- [ ] 3.5 Rate limiter: sliding window через Valkey
- [ ] 3.6 OpenAI embeddings: text-embedding-3-large → 3072 dim
- [ ] 3.7 Тест каждого агента поочерёдно (Scout, Bid, Planner, Dev, Content, Design, Critic, Packager, GeoScout, Outreach, SalesAgent)
- **Status:** pending
- **Критерий:** Все 11 агентов парсят LLM ответ без ошибок

### Phase 4: End-to-End Pipeline (2-3 часа)
Полный прогон обоих пайплайнов с реальными LLM.

- [ ] 4.1 Pipeline A happy path: Scout → ... → Packager
- [ ] 4.2 Pipeline A revision: Critic reject → Dev → Critic (max 3)
- [ ] 4.3 Pipeline A HITL: approve / reject / later
- [ ] 4.4 Pipeline B: GeoScout → Lead Scorer → Outreach → Email
- [ ] 4.5 Touch Sequence: Day 1/3/5/10
- [ ] 4.6 Crash recovery: kill mid-pipeline → resume
- [ ] 4.7 Concurrent: 3 Pipeline A одновременно
- [ ] 4.8 Circuit breaker: simulate platform failure
- **Status:** pending
- **Критерий:** Pipeline A и B end-to-end с реальными LLM, данные в DB

### Phase 5: Platform Adapters (2-3 часа)
Подключить реальные внешние API.

- [ ] 5.1 FL.ru RSS — парсинг реального feed
- [ ] 5.2 Telegram Channels — подключить Telethon к тестовому каналу
- [ ] 5.3 Enrichment waterfall: OSINT → Apollo (с реальным ключом)
- [ ] 5.4 Email sender: тестовый SMTP (Mailtrap/Gmail App Password)
- [ ] 5.5 Telegram DM: тестовое сообщение себе
- [ ] 5.6 Kwork scraper: Playwright + stealth на реальном сайте
- [ ] 5.7 Upwork: read-only Playwright (БЕЗ auto-submit!)
- [ ] 5.8 Freelancer API: если есть OAuth credentials
- **Status:** pending
- **Критерий:** Каждый адаптер подключается к реальному API

### Phase 6: Dashboard Verification (2-3 часа)
Проверить все 26 страниц с реальными данными.

- [ ] 6.1 Auth: register → login → protected routes
- [ ] 6.2 Dashboard home: статистика из реальной DB
- [ ] 6.3 Jobs: список, фильтрация, поиск, job detail + pipeline progress
- [ ] 6.4 Bids: Kanban (drag-and-drop), approve/reject
- [ ] 6.5 HITL: 13 типов, countdown, bulk resolve, payload viewer
- [ ] 6.6 Agents: status grid, detail + live log streaming
- [ ] 6.7 Orchestrator: запуск pipeline, прогресс
- [ ] 6.8 Leads + Deals: список, детали, scoring
- [ ] 6.9 Outreach + Campaigns: CRUD, email approval
- [ ] 6.10 Geo: react-leaflet карта
- [ ] 6.11 Analytics: графики, фильтры, CSV export
- [ ] 6.12 Portfolio: CRUD проектов
- [ ] 6.13 Settings: API keys, platform accounts, credential testing
- [ ] 6.14 Users: approve/reject, role management
- [ ] 6.15 Telegram Channels: CRUD
- [ ] 6.16 WebSocket: все 10 каналов в real-time
- [ ] 6.17 Responsive + dark mode
- **Status:** pending
- **Критерий:** Все страницы работают с реальными данными

### Phase 7: Telegram Bot (1-2 часа)
Token уже настроен. Проверить все 15 команд.

- [ ] 7.1 /start, /help, /status — базовые команды
- [ ] 7.2 /pending — HITL карточки с inline buttons
- [ ] 7.3 Approve/skip/later через бот
- [ ] 7.4 /orch, /run, /stop — orchestrator commands
- [ ] 7.5 /goals, /health, /milestones, /logs
- [ ] 7.6 Push notifications при новых HITL items
- **Status:** pending
- **Критерий:** Все 15 команд работают, HITL approve через бот

### Phase 8: Security Audit (1-2 часа)
Большинство security уже на месте — нужна верификация.

- [ ] 8.1 Semgrep scan: src/ + rules/
- [ ] 8.2 `pip audit` — CVE в зависимостях
- [ ] 8.3 nginx security headers проверка (CSP, HSTS, X-Frame)
- [ ] 8.4 JWT guards на всех protected routes
- [ ] 8.5 Fernet encryption: credentials в DB encrypted
- [ ] 8.6 Rate limiting: все public endpoints
- [ ] 8.7 CORS origins проверка
- [ ] 8.8 .env не в git, secrets не в логах
- **Status:** pending
- **Критерий:** Semgrep 0 critical, pip audit 0 critical, headers verified

### Phase 9: Monitoring Verification (1 час)
Prometheus, Grafana, Sentry — всё EXISTS, нужно запустить и проверить.

- [ ] 9.1 `docker compose -f docker-compose.prod.yml up prometheus grafana` — проверить
- [ ] 9.2 Grafana: mas-overview dashboard загружается
- [ ] 9.3 Prometheus: 3 scrape targets active (api, valkey, postgres)
- [ ] 9.4 Alert rules: 6 правил loaded
- [ ] 9.5 Sentry: настроить SENTRY_DSN, проверить error capturing
- [ ] 9.6 Structured logging: correlation IDs в логах
- **Status:** pending
- **Критерий:** Grafana dashboard показывает метрики, alerts работают

### Phase 10: Load Testing (1 час)
Locust уже настроен — запустить и проверить.

- [ ] 10.1 `docker compose -f docker-compose.loadtest.yml up` — запустить Locust
- [ ] 10.2 Locust UI: http://localhost:8089
- [ ] 10.3 Прогнать 50 concurrent users, 5 min
- [ ] 10.4 Проверить: p50 < 200ms, p95 < 500ms, error < 1%
- [ ] 10.5 DB query analysis: EXPLAIN на hot queries
- [ ] 10.6 Исправить bottlenecks если найдены
- **Status:** pending
- **Критерий:** p95 < 500ms, error rate < 1%

### Phase 11: Deploy (зависит от решения по хостингу)
Railway EXPIRED. Варианты: VPS, Hetzner, DigitalOcean, renew Railway.

- [ ] 11.1 Определить хостинг — обсудить с пользователем
- [ ] 11.2 Provision: PostgreSQL 16 + pgvector, Valkey 8.1
- [ ] 11.3 Deploy API + Dashboard + Worker + Bot
- [ ] 11.4 Настроить SSL/TLS (certbot profile в prod compose)
- [ ] 11.5 Custom domain
- [ ] 11.6 CI/CD: GitHub Actions → deploy target
- [ ] 11.7 Backup strategy: scripts/backup_postgres.sh
- [ ] 11.8 Health checks: post-deploy verification
- **Status:** blocked (нужен хостинг)
- **Критерий:** Все 4 сервиса running, SSL, health check passing

### Phase 12: Missing Features (from specs — 34 items)
Приоритезированный список незакрытых фич из спецификаций.

#### P0 — Критично для production
- [ ] 12.1 telegram_notifications table — миграция + model
- [ ] 12.2 Email warmup automation — scheduled ramp-up
- [ ] 12.3 Data retention jobs — cleanup старых logs/checkpoints
- [ ] 12.4 ADMIN_EMAIL/PASSWORD seed — auto-create admin на старте

#### P1 — Важно
- [ ] 12.5 Business Analyzer medium/deep — Google Places API, social media
- [ ] 12.6 NegotiationEngine graph integration — сейчас off-graph
- [ ] 12.7 Telegram bot: rate limiting commands (30/min, 10/min HITL)
- [ ] 12.8 Telegram bot: timeout/expiry alerts
- [ ] 12.9 GDPR: right to be forgotten handler (API endpoint)
- [ ] 12.10 Deal Memory DB persistence (сейчас in-memory)

#### P2 — Nice to have
- [ ] 12.11 Portfolio Agent
- [ ] 12.12 Fiverr adapter
- [ ] 12.13 Telegram bot: edit flow, quiet hours, multi-user
- [ ] 12.14 Telegram bot: webhook mode (вместо polling)
- [ ] 12.15 client_context separate table
- [ ] 12.16 WhatsApp Business API
- [ ] 12.17 LinkedIn outreach
- [ ] 12.18 NotificationCenter dropdown в dashboard
- [ ] 12.19 Email bounce analysis dashboard
- [ ] 12.20 Consent management UI

- **Status:** pending
- **Критерий:** P0 done, P1 done, P2 по потребности

### Phase 13: Final QA (2-3 часа)
- [ ] 13.1 Full regression — все 2972+ тестов
- [ ] 13.2 302 pytest warnings fix
- [ ] 13.3 Integration tests с реальными сервисами
- [ ] 13.4 E2E: docker-compose.test.yml + Playwright
- [ ] 13.5 Security re-scan
- [ ] 13.6 Documentation review
- [ ] 13.7 Runbook creation
- **Status:** pending
- **Критерий:** Все тесты зелёные, документация полная

---

## Key Questions (updated)

1. ~~Какие API ключи?~~ → OpenRouter ✓, Apollo ✓, OpenAI НУЖЕН
2. ~~Telegram bot?~~ → Уже создан, token в .env ✓
3. **Railway expired** — какой хостинг? VPS/Hetzner/DigitalOcean/renew?
4. **SMTP сервер** — Gmail App Password? Mailtrap? Custom?
5. **OpenAI API key** — нужен для embeddings (text-embedding-3-large)
6. **Pipeline C (Telegram mining)** — в scope?
7. **Proxy (BrightData)** — нужен для Upwork/Kwork scraping?
8. **GDPR/Legal** — нужен compliance для target market?

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Full production, не MVP | Требование пользователя |
| 13 фаз (revised) | Скорректировано: многое уже существует, фазы стали про верификацию |
| Инфраструктура 90% ready | nginx, prometheus, grafana, alerting rules — всё EXISTS |
| Deploy BLOCKED | Railway expired, нужно решение по хостингу |
| 34 missing features → P0/P1/P2 | Приоритизация по impact на production readiness |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Docker Desktop not running | 1 | Нужен ручной запуск пользователем |
| Initial analysis missed 4 compose files | 1 | Deep re-analysis выявил test + loadtest compose |
| Initial analysis missed monitoring stack | 1 | Все Grafana/Prometheus configs существуют |
| Initial analysis missed 5 GH workflows | 1 | Полный CI/CD pipeline уже настроен |

## Notes
- Проект на уровне версии **4.2.0** — это зрелый codebase
- Docker Desktop нужно запустить вручную (Windows)
- OpenAI API key критичен для embeddings — без него semantic cache и RAG не работают
- Railway expired — ключевой blocker для deploy, нужен альтернативный хостинг
