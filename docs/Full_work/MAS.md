# MAS: Мульти-Агентная Система

> Автономное цифровое агентство из 10 реализованных + 2 проектируемых ИИ-агентов. Находит заказы, ведёт переговоры, выполняет работу, сдаёт результат.
> Этот файл — единый индекс. Source of truth — детальные спеки по ссылкам ниже.
>
> **Новый разработчик?** Начните с [onboarding.md](onboarding.md).

---

## Общая схема

```
┌─────────────────────────────────────────────────────────────────┐
│  PIPELINE A (биржи фриланса)                                    │
│  Scout → Bid → [HITL] → Клиент → [HITL] → Dev Cycle            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────┐
│  PIPELINE B (прямые продажи)                                    │
│  GeoScout/WebScout/Telegram → Analyzer → Scorer →              │
│  [HITL] → SalesAgent → [HITL] → Dev Cycle                      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  DEV CYCLE ENGINE (фабрика исполнения)                          │
│  Planner → Dev / Content / Design → Critic → [HITL] →          │
│  Packager → [HITL] → Delivery → RAG Experience Store           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Стек

| Слой | Технология |
|------|------------|
| Оркестрация | LangGraph 1.0 |
| Backend | Litestar + Python 3.12 |
| Database | PostgreSQL 16 + pgvector |
| Cache | Valkey 8.1 |
| Browser | Playwright + Stealth |
| Frontend | Remix + shadcn/ui |
| LLM Router | OpenRouter |
| Embeddings | OpenAI text-embedding-3-large (3072 dim) |
| Deploy | Railway (Docker) |

---

## 11 агентов

| # | Агент | Pipeline | LLM (Tier) | Статус | Роль |
|---|-------|----------|------------|:------:|------|
| 1 | Scout | A | Gemini 2.5 Flash (T5) | [IMPL] | Поиск заказов на 5 платформах, LLM-скоринг |
| 2 | Bid | A | Gemini 3.1 Pro (T2) | [IMPL] | Генерация заявки, RAG, HITL перед отправкой |
| 3 | Planner | Dev Cycle | Claude Opus 4.6 (T1) | [IMPL] | Декомпозиция задач ≤4ч, HITL при >20ч |
| 4 | Dev | Dev Cycle | Opus 4.6 / Sonnet 4.6 (T1/T3) | [IMPL] | Генерация кода, семантический кэш |
| 5 | Content | Dev Cycle | Claude Sonnet 4.6 (T3) | [IMPL] | Тексты, документация, переводы |
| 6 | Design | Dev Cycle | NanoBanana Pro (T4) | [IMPL] | Макеты, UI-спеки |
| 7 | Critic | Dev Cycle | Claude Sonnet 4.6 (T3) | [IMPL] | Semgrep + LLM-ревью, блокировка |
| 8 | Packager | Dev Cycle | DeepSeek V3.2 (T6) | [IMPL] | Финальная упаковка, HITL перед сдачей |
| 9 | GeoScout | B | Gemini 2.5 Flash (T5) | [IMPL] | H3 гексы, Overpass API, геолокация бизнесов |
| 10 | Outreach | B | Gemini 3.1 Pro (T2) | [IMPL] | Email/Telegram outreach, touch sequence |
| 11 | SalesAgent | B | Claude Opus 4.6 (T1) | [PLAN] | Переговоры, Concept Review, сделки |
| 12 | Portfolio Agent | — | DeepSeek V3.2 (T6) | [PLAN] | Автозаполнение портфолио |

---

## Готовые спецификации

| Спека | Файл | Описание |
|-------|------|----------|
| Pipeline A | [pipeline-a-spec.md](pipeline-a-spec.md) | Биржи фриланса: Scout → Bid → HITL → переговоры → Dev Cycle |
| Pipeline B | [pipeline-b-spec.md](pipeline-b-spec.md) | Прямые продажи: GeoScout → Analyzer → SalesAgent → Dev Cycle |
| Dev Cycle Engine | [dev-cycle-spec.md](dev-cycle-spec.md) | Фабрика исполнения: Planner → Agents → Critic → Packager |
| Интерфейс | [интерфейс.md](интерфейс.md) | Общее описание UI/UX Dashboard |

---

## Спецификации подсистем

| Спека | Файл | Описание |
|-------|------|----------|
| Агенты | [specs/agents-spec.md](specs/agents-spec.md) | Все агенты, LangGraph state, checkpoints |
| GeoScout | [specs/geo-scout-spec.md](specs/geo-scout-spec.md) | H3 гексы, Overpass API, geocoding |
| Outreach + Sales | [specs/outreach-spec.md](specs/outreach-spec.md) | Каналы, тон, Touch Sequence, SalesAgent |
| LLM и промпты | [specs/llm-spec.md](specs/llm-spec.md) | OpenRouter, модели, costs, semantic cache |
| Database | [specs/database-spec.md](specs/database-spec.md) | PostgreSQL schema, pgvector, миграции |
| API | [specs/api-spec.md](specs/api-spec.md) | Litestar, routes, WebSocket, auth, rate limiting |
| Deploy | [specs/deploy-spec.md](specs/deploy-spec.md) | Railway, Docker, CI/CD, backup, performance |
| Telegram-бот | [specs/telegram-bot-spec.md](specs/telegram-bot-spec.md) | HITL через Telegram |
| HITL-система | [specs/hitl-spec.md](specs/hitl-spec.md) | Все HITL точки, edge cases, audit, expiry |
| Enrichment | [specs/enrichment-spec.md](specs/enrichment-spec.md) | Waterfall, Hunter, Apollo, senders, email warmup |
| Platform Adapters | [specs/platform-adapters-spec.md](specs/platform-adapters-spec.md) | 6 платформ, browser, stealth, ToS |
| Security | [specs/security-spec.md](specs/security-spec.md) | Semgrep, encryption, stealth, policies |
| RAG-память | [specs/rag-memory-spec.md](specs/rag-memory-spec.md) | Knowledge base, semantic cache, embeddings |
| Orchestrator | [specs/orchestrator-spec.md](specs/orchestrator-spec.md) | Sisyphus runner, goals, health, phases |
| Legal Compliance | [specs/legal-compliance-spec.md](specs/legal-compliance-spec.md) | GDPR, ToS, data retention, consent |
| Тестирование | [specs/testing-spec.md](specs/testing-spec.md) | Стратегия, паттерны, CI, 2330+ тестов |
| Negotiation Engine | [specs/negotiation-spec.md](specs/negotiation-spec.md) | State machine переговоров, classifier, follow-up, WebSocket chat |
| SalesAgent & Pipeline B | [specs/sales-agent-spec.md](specs/sales-agent-spec.md) | SalesAgent, Business Analyzer, Lead Scorer, Touch Sequence |
| Infrastructure | [specs/infrastructure-spec.md](specs/infrastructure-spec.md) | WebSocket events, concurrency, crash recovery, capability report |

---

## Portfolio

| Файл | Описание |
|------|----------|
| [portfolio/strategy.md](portfolio/strategy.md) | Стратегия холодного старта портфолио |
| [portfolio/portfolio-agent.md](portfolio/portfolio-agent.md) | Portfolio Agent (#12) — автозаполнение |
| [portfolio/visuals-research.md](portfolio/visuals-research.md) | Исследование визуалов для портфолио |

---

## Идеи (backlog)

| Файл | Описание |
|------|----------|
| [ideas/client-growth-upselling.md](ideas/client-growth-upselling.md) | Pipeline C: Auto-Monitor → Upsell → новый проект |

---

## HITL-точки (сводка)

Детали — в спеках соответствующих пайплайнов.

### Pipeline A

| # | Момент | Обязательна? |
|---|--------|-------------|
| 1 | Bid approve перед отправкой | ДА |
| 2 | **Dev Launch** (запуск разработки после бида) | **ДА** |
| 3 | Спорный момент в диалоге с клиентом | Условно |
| 4 | Revision escalation (>2 итераций) | Условно |
| 5 | Final Delivery | ДА |

### Pipeline B

| # | Момент | Обязательна? |
|---|--------|-------------|
| 1 | Lead Card (hot/warm лид найден) | ДА |
| 2 | Outreach approve перед отправкой | ДА |
| 3 | Concept Review (черновик концепции) | ДА |
| 4 | Concept Approved (клиент согласен) | ДА |
| 5 | Design Review | ДА |
| 6 | Design Client (финальный макет) | ДА |

### Dev Cycle

| # | Момент | Обязательна? |
|---|--------|-------------|
| 1 | **Dev Launch** (оператор запускает разработку) | **ДА** |
| 2 | Plan Review при объёме >20ч | Условно |
| 3 | Revision Escalation | Условно |
| 4 | Partial Failure (агент не справился) | Условно |
| 5 | Final Delivery | ДА |

---

## Data Flow

```
Scout / GeoScout
    │
    ▼
jobs / leads таблицы ──────────────► Dashboard (Remix UI)
    │
    ▼
Bid / SalesAgent
    │
    ▼
bids / deals таблицы ──────────────► HITL (Telegram бот)
    │                                      │
    ▼                                      ▼
Platform Adapters              approve / reject / edit
(Freelancer, FL.ru,
 Kwork, Upwork*)
    │
    ▼
Dev Cycle Engine
    │
    ├── Planner → задачи
    ├── Dev / Content / Design → артефакты
    └── Critic → ревью → Packager
                              │
                              ▼
                   RAG Experience Store (pgvector)
                   — обогащает будущие Bid и Dev
```

_* Upwork: только чтение, auto-submit ЗАПРЕЩЁН (ToS)_

---

## Архив

Старые файлы перемещены в [archive/](archive/):
- `vision/` — ранние "видение" файлы (MAS.md, pipeline visuals, dashboard)
- `reviews/` — code reviews, security reviews, architecture reviews (2026-02-23)
- `research/` — market research, competitor analysis, MAS overview
- `archive/` корень — mas_architecture_v4.2.md, technical_implementation_guide.md
- Дизайн-документы: telegram-strategy, personalized-outreach, design-workflow, scout-extended-coverage, self-improvement-agent
