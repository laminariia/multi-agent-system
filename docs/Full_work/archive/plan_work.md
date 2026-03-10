План: Полная спецификация MAS — все оставшиеся блоки
                                                                                                                     
 Контекст                                                                                                          
                                                                                                                   
 Строим полную спецификацию MAS по master-plan.md (18 блоков). Уже готово:
 - #1 Pipeline A (pipeline-a-spec.md)
 - #2 Pipeline B (pipeline-b-spec.md)
 - #6 Dev Cycle Engine (dev-cycle-spec.md)

 Осталось 15 блоков (#3-5, #7-18). У большинства уже есть обширная документация в docs/ — задача не писать с нуля, а
  консолидировать существующие доки + видение + код в единый формат.

 Подход

 Для каждой спеки:
 1. Читаем существующие доки (primary + secondary sources)
 2. Анализируем текущий код (что реально работает)
 3. Описываем видение (что должно быть)
 4. Собираем в unified spec (source of truth)

 Формат каждой спеки: Как dev-cycle-spec.md — source of truth с секциями: назначение, data flow, ключевые функции,
 таблицы БД, зависимости, конфигурация, ограничения, файлы, приоритеты.

 Порядок работы (5 батчей)

 Батч 1: Агенты (#3-5)

 Файлы:
 - docs/Awon/vision/agents/pipeline-a-agents-spec.md — Scout, Bid, Planner, Dev, Content, Design, Critic, Packager
 (8 агентов Pipeline A)
 - docs/Awon/vision/agents/geo-scout-spec.md — GeoScout + H3 + Overpass + geocoding
 - docs/Awon/vision/agents/outreach-spec.md — Outreach + SalesAgent + channels + dispatch

 Источники:
 - docs/agent_specifications_core.md (Scout, Bid, Planner, Dev)
 - docs/agent_specifications_support.md (Critic, Packager, GeoScout, Outreach)
 - docs/agent_specifications.md (Content, Design)
 - docs/plans/2026-02-25-design-workflow.md (Design workflow)
 - docs/plans/2026-02-24-personalized-outreach-design.md (Multi-channel outreach)
 - docs/negotiation_flows.md (переговоры)
 - src/agents/*.py (текущий код каждого агента)
 - pipeline-a-spec.md, pipeline-b-spec.md, dev-cycle-spec.md (контекст пайплайнов)

 Ключевые изменения от текущего кода:
 - Dynamic agent_sequence (из dev-cycle-spec) вместо хардкод цепочки
 - Targeted revision (Critic -> конкретный агент)
 - SalesAgent — новый агент (из pipeline-b-spec)
 - Design перед Dev (из design-workflow.md)
 - Multi-channel outreach (email + telegram + whatsapp)
 - Execution Cloaking (dual estimation, delivery throttling)

 Батч 2: Ядро (#7) + Инфраструктура (#8-10)

 Файлы:
 - docs/Awon/vision/core/llm-spec.md — LLM Client, модели, промпты, costs, semantic cache
 - docs/Awon/vision/infrastructure/database-spec.md — Schema, pgvector, миграции
 - docs/Awon/vision/infrastructure/api-spec.md — Litestar, routes, WebSocket, auth
 - docs/Awon/vision/infrastructure/deploy-spec.md — Railway, Docker, CI/CD, monitoring

 Источники:
 - TECH_STACK.md (LLM models, stack — authoritative)
 - docs/semantic_cache.md, docs/knowledge_base.md (LLM caching + RAG)
 - docs/database_schema.md, docs/langgraph_state.md (DB)
 - docs/api_specification.md, docs/auth_specification.md (API)
 - docs/deployment.md, docs/ci_cd.md, docs/backup_recovery.md (Deploy)
 - src/core/llm_client.py, src/core/semantic_cache.py (код)
 - src/api/main.py, src/api/routes/*.py (API код)

 Ключевые изменения:
 - LLM: fallback models, cost budgets, request queue
 - DB: новые таблицы для Pipeline B (deals, lead_scores, sales_conversations), delivery_type
 - API: новые endpoints для Pipeline B + Settings расширение
 - Deploy: multi-service Railway (API + Dashboard + Worker)

 Батч 3: Интерфейсы (#11-12)

 Файлы:
 - docs/Awon/vision/interfaces/dashboard-spec.md — все страницы, компоненты, stores, auth
 - docs/Awon/vision/interfaces/telegram-bot-spec.md — commands, HITL, notifications

 Источники:
 - docs/gui/GUI_SPECIFICATION.md, docs/gui/UX_USER_FLOWS.md (Dashboard)
 - docs/telegram_bot.md (Bot)
 - docs/plans/2026-02-24-telegram-strategy.md (Telegram integration)
 - dashboard/app/routes/*.tsx, dashboard/app/components/*.tsx (код)
 - src/bot/*.py (код бота)

 Ключевые изменения:
 - Dashboard: pipeline progress bar, agent load view, Scout категории в Settings
 - Dashboard: Pipeline B pages (Leads, Campaigns, Deals)
 - Telegram: dual pipeline (freelance orders + business profiles)

 Батч 4: Подсистемы (#13-17)

 Файлы:
 - docs/Awon/vision/subsystems/hitl-spec.md — ВСЕ HITL точки, UI, audit, expiry, escalation
 - docs/Awon/vision/subsystems/enrichment-spec.md — Waterfall, Hunter, Apollo, OSINT, senders
 - docs/Awon/vision/subsystems/platform-adapters-spec.md — 5+ платформ, rate limiter, stealth
 - docs/Awon/vision/subsystems/security-spec.md — Semgrep, encryption, policies
 - docs/Awon/vision/subsystems/rag-memory-spec.md — Knowledge base, embeddings, retrieval

 Источники:
 - HITL: pipeline-a-spec, pipeline-b-spec, dev-cycle-spec (все HITL точки), docs/edge_cases.md
 - Enrichment: docs/plans/2026-02-24-personalized-outreach-design.md, src/enrichment/*.py
 - Adapters: docs/platform_policies.md, docs/playwright_stealth.md, src/adapters/*.py
 - Security: docs/security_review_2026_02_23.md, src/security/*.py
 - RAG: docs/knowledge_base.md, docs/semantic_cache.md, src/core/semantic_cache.py

 Ключевые изменения:
 - HITL: единая таблица всех точек (Pipeline A + B + Dev Cycle), timeout/expiry логика
 - Enrichment: email warmup (6 недель), Touch Sequence Manager
 - Adapters: Fiverr (новая платформа), Telegram listener как источник
 - RAG: Experience Store (обучение из завершённых проектов)

 Батч 5: Portfolio Agent (#18) + Мегафайл

 Файлы:
 - docs/Awon/vision/portfolio-agent-spec.md — 11-й агент
 - docs/Awon/vision/MAS.md — единая точка входа (мегафайл-индекс)

 Источники:
 - docs/plans/2026-02-25-portfolio-agent-design.md
 - docs/plans/2026-02-24-portfolio-strategy.md
 - docs/portfolio/*.md (10 проектов)
 - Все созданные спеки (для MAS.md)

 MAS.md структура:
 - Общая архитектура (схема, стек, 11 агентов)
 - Каждый блок: 10-20 строк + ссылка на спеку
 - ASCII data flow между блоками
 - HITL-точки сводная таблица
 - Приоритеты P0-P3 глобальные
 - Ключевые файлы (полная карта проекта)

 Финализация

 После всех батчей:
 - Обновить docs/Awon/master-plan.md — все блоки -> статус
 - Проверить консистентность между спеками (LLM модели, таблицы БД, API routes)
 - Исправить известные противоречия (LLM models в agent_specifications vs TECH_STACK.md)

 Execution Strategy

 Каждый батч = 2-3 sub-agent'а параллельно:
 - Батч 1: 3 спеки -> 3 агента параллельно
 - Батч 2: 4 спеки -> 2 агента (core + infrastructure)
 - Батч 3: 2 спеки -> 2 агента параллельно
 - Батч 4: 5 спек -> 3 агента параллельно (hitl+enrichment, adapters+security, rag)
 - Батч 5: 2 файла -> последовательно (MAS.md зависит от всех спек)

 Итого создать: ~18 файлов (15 спек + MAS.md + обновить master-plan + CLAUDE.md)

 Verification

 1. Каждая спека следует единому формату (назначение, flow, функции, БД, зависимости, файлы, приоритеты)
 2. Нет противоречий между спеками (LLM модели, delivery_type, HITL точки)
 3. Все 18 блоков master-plan покрыты
 4. MAS.md ссылается на все спеки и даёт полную картину за 5 минут
 5. master-plan.md — все статусы обновлены