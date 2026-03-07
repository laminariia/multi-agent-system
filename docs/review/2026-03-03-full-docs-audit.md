# Аудит документации MAS — 2026-03-03

> Глубокий анализ всех .md файлов проекта. Файлы разбиты по группам.
> Для каждого: назначение, актуальность, расхождения, ключевое содержание.
> Ничего не изменено — только анализ.

---

## Группа 1: Корневые файлы и архитектура (8 файлов)

### CLAUDE.md
- **Назначение:** Главный инструкционный файл для Claude Code. Первое что читает агент. Содержит tech stack, pipelines, project structure, правила, ссылки на всю документацию.
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ
  - Количество тестов "2087+" — фактически **2483**
  - Платформы: перечислены 4, но в коде уже есть Fiverr (в settings.py)
  - Ссылка на `docs/email_warmup.md` — **файл не существует**
  - `mas_architecture_v4.2.md` и `technical_implementation_guide.md` указаны как в docs/, но лежат в корне
- **Расхождения:** Test count расходится с MEMORY.md (2330) и реальностью (2483). Отсутствуют 4 API-роута в Documentation Index (settings, orchestrator, campaigns, telegram-channels)
- **Ключевое содержание:** Единая точка входа для AI-агентов. Описывает весь стек, оба пайплайна, структуру проекта и ключевые правила (Litestar не FastAPI, Valkey не Redis, HITL mandatory).

### RULES.md
- **Назначение:** Конвенции кодирования — стиль, именование, структура, тестирование, безопасность.
- **Актуальность:** АКТУАЛЕН. Правила соответствуют фактическому коду (structlog, ruff, pytest-asyncio, SQLAlchemy 2.0 style).
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** Python-стиль (Google docstrings, type hints обязательны), ruff для линтинга, все async-операции через asyncio, no raw SQL без параметризации.

### TECH_STACK.md
- **Назначение:** **Единый источник истины** для технологических решений. Явно объявлен как авторитетный — "если другие документы конфликтуют, этот файл правильный".
- **Актуальность:** ПОЧТИ АКТУАЛЕН
  - Stealth API пример **неверный**: показывает `stealth_async(page)`, фактически `Stealth().apply_stealth_async(page)`
  - LLM-модели актуальны (DeepSeek V3.2 для 6 агентов, Claude для Planner/Dev/Critic, NanoBanana Pro для Design)
- **Расхождения:** Stealth API пример расходится с кодом и `.claude/rules/debugging.md`
- **Ключевое содержание:** Полный стек с версиями, rationale для каждого выбора, anti-patterns ("NOT FastAPI", "NOT Redis"), стоимость LLM-моделей, матрица совместимости.

### mas_architecture_v4.2.md
- **Назначение:** Архитектурное описание всей системы v4.2. Executive summary, оба пайплайна, state management, security layers.
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ (датирован январь 2026)
  - Использует эмодзи (стилистическое расхождение с RULES.md)
  - Некоторые code samples могут использовать `redis`/`FastAPI` синтаксис (disclaimer есть)
  - Feature list v4.2 (Role Constraints, Loop Detection) — реализованы
- **Расхождения:** Disclaimer о redis/FastAPI синтаксисе — допустимо, но может путать новичков.
- **Ключевое содержание:** 10-агентная система, Pipeline A (freelance) + Pipeline B (outreach), HybridCheckpointSaver, MAST Taxonomy для отказоустойчивости, competitor analysis интегрирован.

### technical_implementation_guide.md
- **Назначение:** Практическое руководство по реализации — код-уровневые детали каждого компонента.
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ — дублирует mas_architecture_v4.2.md с code-level деталями. Некоторые примеры могут использовать устаревший API.
- **Расхождения:** Потенциальные расхождения в code samples с текущей кодовой базой.
- **Ключевое содержание:** Детальная реализация каждого агента, graph routing, HITL integration, deployment конфигурации.

### competitors-analysis.md
- **Назначение:** Анализ конкурентов — GetMany, TopDev, AutoFreelance. Их сильные/слабые стороны.
- **Актуальность:** АКТУАЛЕН как исследование. Одноразовый документ (snapshot), не требует обновлений.
- **Расхождения:** Нет — это исследовательский артефакт.
- **Ключевое содержание:** GetMany — timezone optimization (+34% view rate), A/B testing proposals (+15% response). TopDev — enterprise focus. Выводы интегрированы в architecture v4.2.

### multi-agent-systems.md
- **Назначение:** Теоретическое исследование MAS-паттернов — MAST taxonomy, failure modes, best practices из академических источников.
- **Актуальность:** АКТУАЛЕН как reference. Одноразовый документ.
- **Расхождения:** Нет.
- **Ключевое содержание:** 6 типов MAS-ошибок, parabolic failure severity, role constraints как решение. Послужил основой для v4.2 improvements.

### CODE_REVIEW_2026_02_23.md
- **Назначение:** Сводка code review от 23 февраля. Результаты аудита безопасности, архитектуры, производительности.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT. Рекомендации уже частично применены (SQL injection fix, exception narrowing, structured logging).
- **Расхождения:** Ссылается на отдельные review-файлы в docs/ (security_review, architecture_review и т.д.).
- **Ключевое содержание:** 5 направлений ревью, найденные проблемы, рекомендации. Grade A+ (99 из 100).

---

## Группа 2: Агенты, API и Core-спецификации (11 файлов)

### docs/MAS_OVERVIEW.md
- **Назначение:** Обзор системы на русском языке для пользователя. User-facing документ с описанием обоих пайплайнов, Dashboard, Telegram бота.
- **Актуальность:** АКТУАЛЕН. Хорошо написан, отражает текущее состояние системы.
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** 10 агентов, 2 пайплайна, 15+ страниц Dashboard, Telegram бот с inline-кнопками, Orchestrator для автономной работы. Практический тон.

### docs/ONBOARDING.md
- **Назначение:** Быстрый старт для нового разработчика/AI-агента. Шаги установки, конфигурация, первый запуск.
- **Актуальность:** ЧАСТИЧНО АКТУАЛЕН — нужно проверить соответствие шагов текущему окружению (macOS vs Windows).
- **Расхождения:** Возможны расхождения в командах установки.
- **Ключевое содержание:** Git clone → venv → pip install → .env → docker-compose → alembic → run.

### docs/agent_specifications.md
- **Назначение:** Спецификации Content Agent и Design Agent (2 из 10).
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ
- **Расхождения:** Находится отдельно от core/support — непоследовательное разделение. Content и Design логически относятся к "creative" агентам, но файл не имеет суффикса (_creative).
- **Ключевое содержание:** Content: тексты, README, UI-тексты; Design: JSON-спецификации дизайна (не визуальные макеты).

### docs/agent_specifications_core.md
- **Назначение:** Спецификации Scout, Bid, Planner, Dev — 4 основных агента Pipeline A.
- **Актуальность:** АКТУАЛЕН по функциональности, но содержит устаревшие LLM-ссылки.
- **Расхождения:** Нет критических по поведению агентов.
- **Ключевое содержание:** Scout (5 платформ, LLM scoring 0.7/0.5 thresholds), Bid (RAG + 5-частная структура), Planner (декомпозиция ≤4ч), Dev (Docker sandbox + Semgrep).

### docs/agent_specifications_support.md
- **Назначение:** Спецификации Critic, Packager, GeoScout, Outreach — support агенты.
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ
- **Расхождения:** **КРИТИЧЕСКОЕ:** Agent Summary Matrix (строки 625-634) указывает "Gemini 3 Flash" для Scout, Bid, Content, Packager, GeoScout, Outreach. TECH_STACK.md (авторитетный источник) говорит **"DeepSeek V3.2"**. Это прямое противоречие.
- **Ключевое содержание:** Critic (score ≥0.85 approve, Semgrep gate), Packager (always HITL), GeoScout (H3 + Overpass), Outreach ("сосед, не продавец").

### docs/api_specification.md
- **Назначение:** Спецификация REST API — эндпоинты, контракты, auth.
- **Актуальность:** УСТАРЕЛ — отсутствуют 4 реализованных route-группы:
  - `/api/v1/settings` (SettingsController) — управление credentials
  - `/api/v1/orchestrator` (OrchestratorController) — старт/стоп/goals
  - `/api/v1/campaigns` (CampaignController) — outreach кампании
  - `/api/v1/telegram-channels` (TelegramChannelController) — CRUD каналов
- **Расхождения:** Помечает campaigns как "Phase 3+ not yet implemented", но CampaignController уже работает в коде.
- **Ключевое содержание:** Jobs, HITL, Agents, Pipeline B, Auth, Users, Health, WebSocket, Metrics endpoints.

### docs/auth_specification.md
- **Назначение:** Спецификация аутентификации — JWT, RBAC, роли (owner/co_owner/moderator/viewer).
- **Актуальность:** АКТУАЛЕН. JWT + refresh tokens, 4 роли, Fernet encryption для credentials.
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** Registration/login/refresh/logout flow, role-based access, encrypted API keys storage.

### docs/database_schema.md
- **Назначение:** Схема базы данных — все таблицы, поля, индексы.
- **Актуальность:** УСТАРЕЛ — заявляет 18 таблиц, документирует 17, фактически в models.py **21 модель**.
- **Расхождения:**
  - Отсутствуют таблицы: `orchestrator_goals`, `telegram_channels`, `langgraph_checkpoint_history`
  - Отсутствуют колонки: `users.status`, `bids.platform_bid_id`, `campaign_leads.channel_type`, `leads.telegram_username`, `leads.enrichment_source`
- **Ключевое содержание:** Users, Jobs, Bids, Projects, Tasks, Artifacts, HITLQueue, Revisions, Leads, EmailCampaigns, KnowledgeBase, SemanticCache и др.

### docs/langgraph_state.md
- **Назначение:** Описание LangGraph state management — AgentState TypedDict, checkpoint persistence.
- **Актуальность:** АКТУАЛЕН по концепции, но нужно проверить соответствие полей текущему state.py.
- **Расхождения:** Может не отражать все поля, добавленные для multichannel outreach.
- **Ключевое содержание:** AgentState fields, HybridCheckpointSaver (Valkey + PostgreSQL), thread_id isolation, state reconstruction bug workaround.

### docs/negotiation_flows.md
- **Назначение:** Сценарии переговоров с клиентами после найма — вопросы, контр-офферы, scope change.
- **Актуальность:** ЧАСТИЧНО АКТУАЛЕН для Pipeline A.
- **Расхождения:** Описывает таблицы `client_messages` и `negotiations` — **они НЕ существуют в models.py**. Целиком нереализованный функционал.
- **Ключевое содержание:** 5 типов клиентских взаимодействий, шаблоны ответов, escalation paths. Всё через HITL.

### docs/edge_cases.md
- **Назначение:** Каталог edge cases — revision flow, failure scenarios, alerts, recovery.
- **Актуальность:** АКТУАЛЕН как reference.
- **Расхождения:** MAX_CONCURRENT=5 описан, но **не enforced в коде**.
- **Ключевое содержание:** Revision loops, HITL timeout, heartbeat failure, concurrent access, rate limiting, disk space.

---

## Группа 3: Operations, Security, Testing и Review (22 файла)

### docs/deployment.md
- **Назначение:** Руководство по деплою — Docker, VPS, мониторинг, SSL.
- **Актуальность:** **УСТАРЕЛ ПО TARGET** — описывает "Hetzner/DigitalOcean VPS" с Docker Compose + nginx + certbot. Фактический деплой на **Railway**.
- **Расхождения:** Весь документ описывает альтернативный деплой. Railway-специфика только в `.claude/rules/debugging.md`.
- **Ключевое содержание:** Docker Compose production config, nginx reverse proxy, SSL, monitoring stack. Валидно как альтернативный/self-hosted гайд.

### docs/ci_cd.md
- **Назначение:** CI/CD pipeline — GitHub Actions, тесты, линтинг, деплой.
- **Актуальность:** АКТУАЛЕН если GH Actions настроены.
- **Расхождения:** Нужно проверить наличие `.github/workflows/`.
- **Ключевое содержание:** Stages: lint → test → build → deploy. ruff + pytest + docker build.

### docs/backup_recovery.md
- **Назначение:** Стратегия бэкапов и восстановления — PostgreSQL, Valkey, файлы.
- **Актуальность:** ЧАСТИЧНО АКТУАЛЕН — описывает generic подход, но Railway имеет свою backup-систему.
- **Расхождения:** Не учитывает Railway-специфику.
- **Ключевое содержание:** pg_dump расписание, Valkey RDB/AOF, RPO/RTO targets, disaster recovery plan.

### docs/testing_strategy.md
- **Назначение:** Стратегия тестирования — подходы, инструменты, coverage targets.
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ
- **Расхождения:**
  - Ссылается на `black --check` (строка 766) — фактически используется **ruff**
  - Примеры тестов (`TestScoutAgent`, `TestBidAgent`) используют другой API чем реальные тесты (иллюстративные, не фактические)
- **Ключевое содержание:** Пирамида тестов (unit/integration/e2e), mock patterns, coverage targets.

### docs/test_inventory.md
- **Назначение:** Инвентаризация тестов — список всех тестовых файлов и что они покрывают.
- **Актуальность:** ЧАСТИЧНО УСТАРЕЛ — количество тестов не обновлено.
- **Расхождения:** Тестовый count не соответствует текущим 2483.
- **Ключевое содержание:** Каталог тестов по директориям: unit/, integration/, e2e/, golden_set/, load/, property/.

### docs/platform_policies.md
- **Назначение:** Правила и ограничения платформ — Freelancer, Upwork, FL.ru, Kwork ToS compliance.
- **Актуальность:** АКТУАЛЕН. Критически важен — несоблюдение = бан аккаунта.
- **Расхождения:** Не включает Fiverr (которая появилась в settings.py).
- **Ключевое содержание:** Upwork: ТОЛЬКО мониторинг (автобид = бан). Freelancer: API-лимиты. Kwork: stealth scraping необходим. Rate limits по платформам.

### docs/playwright_stealth.md
- **Назначение:** Руководство по browser stealth — anti-detection, proxy rotation, fingerprinting.
- **Актуальность:** АКТУАЛЕН по концепции.
- **Расхождения:** Может содержать устаревший API-пример (stealth_async vs Stealth class).
- **Ключевое содержание:** Playwright Stealth integration, CDP detection avoidance, proxy pool management, session reuse.

### docs/semantic_cache.md
- **Назначение:** Семантический кэш — архитектура, vector similarity, TTL, cache invalidation.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Зависит от `OPENAI_API_KEY` для embeddings — ключ **не заполнен в .env** (noted в MEMORY.md).
- **Ключевое содержание:** pgvectorscale DiskANN indexes (11x vs HNSW), 3072-dim embeddings, similarity threshold, cache hit/miss patterns.

### docs/knowledge_base.md
- **Назначение:** Knowledge Base архитектура — RAG для Bid Agent, хранение выигранных бидов.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** Vector storage, KnowledgeRetriever для Bid Agent, fallback SQL по категории.

### docs/performance.md
- **Назначение:** Performance requirements и benchmarks — latency, throughput, optimization.
- **Актуальность:** АКТУАЛЕН как targets.
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** Target latencies, database query optimization, connection pooling, async patterns.

### docs/rate_limiting.md
- **Назначение:** Rate limiting стратегия — per-endpoint, per-user, platform-specific limits.
- **Актуальность:** АКТУАЛЕН, но с Railway-caveat.
- **Расхождения:** Railway reverse proxy коллапсирует все IP в один (100.64.0.3) — rate limiting по IP неэффективен. Fix описан в debugging.md, но не в этом документе.
- **Ключевое содержание:** 300/min global, 60/min auth. Per-user rate limiting через JWT — long-term fix.

### docs/legal_compliance.md
- **Назначение:** Юридические аспекты — GDPR, 152-ФЗ, email compliance, ToS.
- **Актуальность:** АКТУАЛЕН как reference.
- **Расхождения:**
  - Описывает data retention automation (удаление через 90/365 дней) — **НЕ автоматизировано** в коде
  - Описывает обязательный company name + address в email footer — **НЕ реализовано**
  - Railway серверы **НЕ в России** — конфликт с 152-ФЗ для РФ-лидов
- **Ключевое содержание:** B2B cold email допустим (legitimate interest), unsubscribe обязателен, самозанятый → ИП → ООО.

### docs/deep_research_freelance_market.md
- **Назначение:** Глубокое исследование фриланс-рынка — размеры, тренды, ценообразование.
- **Актуальность:** АКТУАЛЕН как исследование (одноразовый snapshot).
- **Расхождения:** Нет.
- **Ключевое содержание:** Рынок РФ, ценовые сегменты, конкуренция, целевые ниши, прогнозы throughput.

### docs/research_summary.md
- **Назначение:** Сводка исследований — ключевые выводы из market research.
- **Актуальность:** АКТУАЛЕН как summary.
- **Расхождения:** Нет.
- **Ключевое содержание:** Краткая выжимка из deep research — ключевые метрики и рекомендации.

### docs/sisyphus_orchestration_patterns.md
- **Назначение:** Паттерны оркестрации "Сизиф" — автономная работа с auto-restart, retry, persistence.
- **Актуальность:** АКТУАЛЕН. Описывает orchestrator runner паттерн.
- **Расхождения:** Нет.
- **Ключевое содержание:** Continuous scan → bid → execute loop, heartbeat monitoring, goal-based orchestration, graceful shutdown.

### docs/telegram_bot.md
- **Назначение:** Спецификация Telegram бота — команды, HITL через inline buttons.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** /orch, /run, /stop, /pending, /scan, /status. Inline кнопки для HITL approve/reject. WebSocket bridge.

### docs/security_review_2026_02_23.md
- **Назначение:** Аудит безопасности от 23 февраля — snapshot.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT. Рекомендации частично применены.
- **Расхождения:** Нет (историческая запись).
- **Ключевое содержание:** SQL injection findings, Semgrep coverage, credential encryption, CORS config.

### docs/architecture_review_core_2026_02_23.md
- **Назначение:** Ревью архитектуры core компонентов — snapshot.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT.
- **Расхождения:** Нет.
- **Ключевое содержание:** Agent architecture, state management, graph routing — findings и рекомендации.

### docs/review_performance_async_2026_02_23.md
- **Назначение:** Ревью производительности и async паттернов — snapshot.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT.
- **Расхождения:** Нет.
- **Ключевое содержание:** Async bottlenecks, connection pooling, concurrent access patterns.

### docs/review_testing_quality.md
- **Назначение:** Ревью качества тестов — snapshot.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT.
- **Расхождения:** Нет.
- **Ключевое содержание:** Test coverage, mock quality, assertion patterns, missing test areas.

### docs/review_frontend_dashboard_2026_02_23.md
- **Назначение:** Ревью фронтенда — Remix dashboard — snapshot.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT.
- **Расхождения:** Нет.
- **Ключевое содержание:** Component quality, state management, API integration, UX patterns.

### docs/review_infra_adapters_2026_02_23.md
- **Назначение:** Ревью инфраструктуры и адаптеров — snapshot.
- **Актуальность:** ИСТОРИЧЕСКИЙ SNAPSHOT.
- **Расхождения:** Нет.
- **Ключевое содержание:** Adapter quality, error handling, retry logic, platform compliance.

---

## Группа 4: Дизайн-документы / Plans (9 файлов)

### docs/plans/2026-02-24-portfolio-strategy.md
- **Статус:** 🔶 Частично реализовано
- **Назначение:** Стратегия портфолио при холодном старте — какие проекты показывать, на каких платформах.
- **Что сделано:** 10 описаний проектов в `docs/portfolio/`, платформы выбраны (6 штук), ценовая стратегия (-20-30% для старта).
- **Что НЕ сделано:** Вёрстка для скриншотов не начата. Публикация на платформах не начата. Dogfooding скрипт не запущен.
- **Расхождения:** Нет.
- **Ключевое содержание:** 6 платформ (Kwork, FL.ru, Fiverr, Freelancer, YouDo, Telegram), 15+ отзывов за 3 мес цель.

### docs/plans/2026-02-24-telegram-strategy.md
- **Статус:** 🔶 Частично реализовано
- **Назначение:** Полная стратегия Telegram-интеграции — listener, dual pipeline, DM outreach, anti-ban.
- **Что сделано:** TelegramChannelAdapter, TelegramDMSender, session string, seed migration 22 каналов, listener service код.
- **Что НЕ сделано:** End-to-end тест listener, backfill 30 дней, dual pipeline (freelance vs business каналы), ProfileAggregator, таблица `telegram_user_profiles`, heartbeat для listener, дедупликация постов в Valkey SET, warmup аккаунта.
- **Расхождения:** Секция 7 (Dual Pipeline Design) полностью не реализована.
- **Ключевое содержание:** Telethon MTProto, 22 канала, Pipeline 1 (заказы) + Pipeline 2 (люди), anti-ban стратегия.

### docs/plans/2026-02-25-design-workflow.md
- **Статус:** ⏳ Не реализовано
- **Назначение:** Перестройка pipeline — Design ПЕРЕД Dev (не после). 3-4 HITL-точки вместо 2.
- **Что НЕ сделано:** Вся перестройка graph.py, новые HITL-типы, Pencil.dev/Penpot интеграция.
- **Расхождения:** Текущий pipeline: Dev → Content → Design → Critic. Дизайн предлагает: Design → [HITL] → [Client HITL] → Dev → Content → Critic. Несовместимо без major refactor.
- **Ключевое содержание:** Pencil.dev (MVP) → Penpot → Figma. Planner решает `needs_design: bool`.

### docs/plans/2026-02-25-portfolio-agent-design.md
- **Статус:** ⏳ Не реализовано
- **Назначение:** 11-й агент — автоматическое портфолио после каждого завершённого проекта.
- **Что НЕ сделано:** Класс PortfolioAgent не существует. Git-папка `portfolio/` не интегрирована в pipeline. Нет auto-trigger после Packager.
- **Расхождения:** Нет (дизайн не противоречит текущей архитектуре).
- **Ключевое содержание:** Packager → [HITL] → Portfolio Agent → адаптация под 6 платформ → публикация.

### docs/plans/2026-02-25-portfolio-visuals-research.md
- **Статус:** 💡 Исследование
- **Назначение:** Исследование инструментов для генерации визуалов портфолио.
- **Расхождения:** Нет.
- **Ключевое содержание:** Pencil.dev, v0.dev, screenshot tools — сравнение для Portfolio Agent.

### docs/plans/2026-02-27-scout-extended-coverage.md
- **Статус:** 💡 Идея (бэклог)
- **Назначение:** Расширение покрытия Scout — пагинация Kwork, limit=100 Freelancer, категорийные RSS FL.ru, новые платформы.
- **Что НЕ сделано:** Всё. Это backlog-идея.
- **Расхождения:** Нет.
- **Ключевое содержание:** Увеличение количества найденных заказов, last_fetched_at tracking, YouDo/Profi.ru.

### docs/plans/2026-02-27-self-improvement-agent.md
- **Статус:** 💡 Идея (бэклог)
- **Назначение:** Агент самообучения — Capability Registry, MCP инструменты, Experience Memory.
- **Что НЕ сделано:** Всё. Концептуальный документ.
- **Расхождения:** Нет.
- **Ключевое содержание:** Агент НЕ редактирует код — работает с конфигурацией. Capability Check перед Bid.

### docs/plans/2026-02-24-client-growth-upselling-vision.md
- **Статус:** 💡 Видение (Pipeline C)
- **Назначение:** Видение Pipeline C — мониторинг клиентов, upsell, managed services.
- **Что НЕ сделано:** Всё. Ни модели данных, ни кода.
- **Расхождения:** Нет.
- **Ключевое содержание:** Auto-Monitor → Snapshot Report → Upsell Recommender → [HITL]. 4 уровня эволюции. 300k руб/мес MRR при 20 клиентах.

### docs/plans/2026-02-24-personalized-outreach-design.md
- **Статус:** 🔶 Частично реализовано (MVP)
- **Назначение:** Персонализированный multi-channel outreach — email + Telegram + WhatsApp.
- **Что сделано:** Email и Telegram каналы, `_select_channel()`, channel-specific промпты, `message_dispatch_node`.
- **Что НЕ сделано:** WhatsApp канал, Business Analyzer, Lead Scorer, Touch Sequence Manager, A/B тестирование сообщений.
- **Расхождения:** Нет.
- **Ключевое содержание:** Тон "сосед, не продавец", 7 принципов, MVP email+TG, Touch Sequence (День 1→3→5→Стоп).

---

## Группа 5: Портфолио (10 файлов)

### docs/portfolio/01-restaurant-landing.md — docs/portfolio/10-email-automation-bot.md
- **Назначение:** 10 описаний фиктивных проектов для холодного старта портфолио. Каждый файл содержит RU+EN тексты для платформ.
- **Актуальность:** АКТУАЛЬНЫ. Готовы к использованию при вёрстке скриншотов.
- **Расхождения:** Нет.
- **Ключевое содержание:** По каждому проекту: название, описание, стек, функционал, тексты для Kwork/FL.ru/Fiverr/Freelancer/Telegram. Покрытие: ресторан, салон красоты, интернет-магазин одежды, стоматология, CRM автосервиса, AI-чатбот, аналитический дашборд, PWA доставки еды, корпоративный сайт, email-бот.

---

## Группа 6: GUI спецификации (2 файла)

### docs/gui/GUI_SPECIFICATION.md
- **Назначение:** Полная спецификация GUI Dashboard — все страницы, компоненты, layout, responsive design.
- **Актуальность:** АКТУАЛЕН. Dashboard реализован на Remix + shadcn/ui.
- **Расхождения:** Нужно проверить соответствие текущим компонентам в `dashboard/app/`.
- **Ключевое содержание:** 15+ страниц, sidebar navigation, dark/light mode, WebSocket real-time updates, responsive breakpoints.

### docs/gui/UX_USER_FLOWS.md
- **Назначение:** UX-флоу — пользовательские сценарии от логина до завершения проекта.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет существенных.
- **Ключевое содержание:** Login → Dashboard → Jobs → HITL → Projects → Delivery. Также: Outreach flow, Settings flow, Admin flow.

---

## Группа 7: .claude/ конфигурации (8 файлов)

### .claude/rules/patterns.md
- **Назначение:** Каталог кодовых паттернов для AI-агента — как писать агенты, тесты, SQL.
- **Актуальность:** АКТУАЛЕН. Паттерны соответствуют реальному коду.
- **Расхождения:** Нет.
- **Ключевое содержание:** ConstrainedAgent pattern, State Transition, HITL pattern, LLM Response Parsing, Graph Routing, Test patterns, SQL wildcard escape, WebSocket publish guard, AsyncMock.

### .claude/rules/debugging.md
- **Назначение:** Каталог debugging gotchas — критичные знания для разработки.
- **Актуальность:** **ОЧЕНЬ АКТУАЛЕН**. Спасает часы дебага.
- **Расхождения:** Нет — это living document, обновляется при каждом новом gotcha.
- **Ключевое содержание:** LangGraph reserved channels, StateGraph(dict) workaround, routing annotation bug, ainvoke vs astream, Railway gotchas (rate limiting, alembic race, --path-as-root).

### .claude/agents/feature-worker.md
- **Назначение:** Инструкции для Feature Worker агента — работает ТОЛЬКО с src/.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет.
- **Ключевое содержание:** Работает с src/, никогда не трогает tests/. File-scoped isolation.

### .claude/agents/quality-worker.md
- **Назначение:** Инструкции для Quality Worker — работает ТОЛЬКО с tests/.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет.
- **Ключевое содержание:** Пишет тесты, запускает lint, code review. Читает src/ но не модифицирует.

### .claude/agents/research-worker.md
- **Назначение:** Инструкции для Research Worker — read-only для кода, write для docs/.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет.
- **Ключевое содержание:** Исследования, анализ архитектуры, обновление документации.

### .claude/agents/infra-worker.md
- **Назначение:** Инструкции для Infra Worker — Docker, CI/CD, миграции.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет.
- **Ключевое содержание:** Docker, docker-compose, alembic migrations, CI configs. Не трогает src/ и tests/.

### .claude/agents/db-migration-reviewer.md
- **Назначение:** Read-only ревьюер миграций — проверяет alembic миграции без изменений.
- **Актуальность:** АКТУАЛЕН.
- **Расхождения:** Нет.
- **Ключевое содержание:** Проверяет backwards compatibility, index strategy, data migration safety.

### .claude/commands/code-review.md, dashboard-sync.md, deploy.md
- **Назначение:** Кастомные slash-команды для Claude Code — /code-review, /dashboard-sync, /deploy.
- **Актуальность:** АКТУАЛЬНЫ.
- **Расхождения:** deploy.md — проверить соответствие Railway workflow.
- **Ключевое содержание:** code-review: ruff + pytest + security check. dashboard-sync: синхронизация API types. deploy: Railway deploy двух сервисов.

---

## Группа 8: Docs-First файлы (3 файла)

### docs/Overview.md
- **Назначение:** Шаблон продуктового видения для docs-first gate.
- **Актуальность:** ПУСТОЙ ШАБЛОН. 0/6 секций заполнены.
- **Расхождения:** Контент уже существует в docs/MAS_OVERVIEW.md — нужен перенос.
- **Ключевое содержание:** Placeholder-текст.

### docs/TechSpec.md
- **Назначение:** Шаблон техспецификации для docs-first gate.
- **Актуальность:** ПУСТОЙ ШАБЛОН. 0/7 секций заполнены.
- **Расхождения:** Контент уже существует в TECH_STACK.md — нужен перенос.
- **Ключевое содержание:** Placeholder-текст с ссылкой на TECH_STACK.md.

### docs/.docs-status.json
- **Назначение:** Трекер статуса docs-first gate.
- **Актуальность:** АКТУАЛЕН. Gate: locked. Оба документа: draft.
- **Расхождения:** Нет — корректно отражает пустые шаблоны.
- **Ключевое содержание:** `{"gate": "locked", "overview": {"status": "draft"}, "tech_spec": {"status": "draft"}}`.

---

## Сводная таблица: Статус реализации

### Планы и дизайны

| Документ | Статус | Прогресс |
|----------|--------|----------|
| Personalized Outreach | 🔶 Частично | MVP email+TG готов, нет WhatsApp/Touch Sequence/Lead Scorer |
| Telegram Strategy | 🔶 Частично | Listener+DM sender код, нет dual pipeline/backfill/ProfileAggregator |
| Portfolio Strategy | 🔶 Частично | 10 описаний готовы, нет вёрстки/скриншотов/публикации |
| Design Workflow | ⏳ Бэклог | 0% — требует major refactor graph.py |
| Portfolio Agent | ⏳ Бэклог | 0% — класс не создан |
| Scout Extended | 💡 Идея | 0% — backlog |
| Self-Improvement Agent | 💡 Идея | 0% — концепт |
| Client Growth (Pipeline C) | 💡 Видение | 0% — ни модели ни кода |
| Portfolio Visuals Research | 💡 Исследование | Инструменты изучены, применение отложено |

### Фичи из дизайнов — реализация

| Фича | Описана в | Реализована |
|------|-----------|-------------|
| Email outreach | personalized-outreach | ✅ Да |
| Telegram DM outreach | personalized-outreach + telegram-strategy | ✅ Да (код) |
| Channel selection (_select_channel) | personalized-outreach | ✅ Да |
| TG Listener service | telegram-strategy | ✅ Да (код, не тестирован e2e) |
| Seed migration 22 каналов | telegram-strategy | ✅ Да |
| WhatsApp канал | personalized-outreach | ❌ Нет |
| Business Analyzer | personalized-outreach | ❌ Нет |
| Lead Scorer | personalized-outreach | ❌ Нет |
| Touch Sequence Manager | personalized-outreach | ❌ Нет |
| Dual Pipeline (freelance vs business TG) | telegram-strategy §7 | ❌ Нет |
| TG ProfileAggregator | telegram-strategy | ❌ Нет |
| TG Backfill 30 дней | telegram-strategy | ❌ Нет |
| TG Post dedup (Valkey SET) | telegram-strategy | ❌ Нет |
| Progressive email warm-up | agent specs | ❌ Нет (лимит статичный) |
| Data retention cron | legal-compliance | ❌ Нет |
| Negotiation tables | negotiation-flows | ❌ Нет |
| Outreach response handling | — | ❌ Нет (КРИТИЧЕСКИЙ ПРОБЕЛ) |
| Pipeline C (Auto-Monitor) | client-growth | ❌ Нет |
| Portfolio Agent | portfolio-agent-design | ❌ Нет |
| Design перед Dev | design-workflow | ❌ Нет |

---

## Сводка по актуальности

| Категория | Актуальных | Частично устаревших | Устаревших | Исторических snapshots |
|-----------|-----------|-------------------|-----------|----------------------|
| Корневые + архитектура | 3 | 3 | 0 | 2 |
| Агенты + API + Core | 4 | 3 | 2 | 0 |
| Operations + Testing + Reviews | 6 | 5 | 1 | 6 |
| Plans | 0 | 3 | 0 | 6 (идеи/бэклог) |
| Portfolio | 10 | 0 | 0 | 0 |
| GUI | 2 | 0 | 0 | 0 |
| .claude/ configs | 8 | 0 | 0 | 0 |
| Docs-first | 1 | 0 | 2 (пустые) | 0 |
| **ИТОГО** | **34** | **14** | **5** | **14** |

### Топ-5 документов требующих обновления

1. **docs/database_schema.md** — 3 отсутствующих таблицы, 5 отсутствующих колонок
2. **docs/api_specification.md** — 4 недокументированных route-группы
3. **docs/agent_specifications_support.md** — неправильные LLM-модели (Gemini → DeepSeek)
4. **docs/deployment.md** — target Hetzner/DO, фактически Railway
5. **CLAUDE.md** — устаревший test count, отсутствующие API-роуты в индексе
