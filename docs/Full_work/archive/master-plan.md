# Master Plan: от документации к продакшену

> Полный план превращения MAS из "код есть, документация фрагментирована" в "всё описано, проверено, работает".

////
я хочу пойти по такому пути: мы описываем поэтапно блоки из которых состоит мас, потом мы берем то как это должно быть в иделае, путем глубокого анализа раскладываем полную картину на много подробных описаний, делаем один мегафайл который будет имемть полное содержание всей картину того как мас работает и выглядит и ссылатсья на подробные спеки из чего строиться полная картина проекта как функциональная так и техническая так и визучльная, потом  сверяем что у нас есть, чего нет, выносим глобальный аудит с помошью полноценных е2е тестов, чистим все лишние файлы, что скажешь
////

---

## Фаза 1: ОПИСАНИЕ БЛОКОВ

Поэтапно описываем каждый компонент MAS по workflow из [spec-workflow.md](spec-workflow.md):
- Три взгляда (код / видение / доки) → матрица → уточнение → спека

**Подход: гибрид.** Крупные компоненты = отдельные файлы. Мелкие = секции внутри родительских спек. 120+ компонентов системы покрыты 15 глубокими спеками в 5 подпапках.

### Что входит в каждую спеку (для каждого компонента внутри):
1. Назначение — что делает, зачем нужен
2. Data flow — вход → обработка → выход (ASCII-схема)
3. Ключевые функции — API/методы с сигнатурами
4. Таблицы БД — какие использует, ключевые поля
5. Зависимости — от каких компонентов зависит
6. Конфигурация — env vars, settings
7. Ограничения — что НЕ делает, known gaps
8. Файлы — пути в проекте

### Блоки:

**Пайплайны:**

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 1 | Pipeline A (Freelance) | `pipeline-a-spec.md` | ✅ Готов |
| 2 | Pipeline B (Outreach) | `pipeline-b-spec.md` | ⬜ Следующий |

**Агенты** (`agents/`):

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 3 | Агенты Pipeline A | `agents/pipeline-a-agents-spec.md` | ⬜ |
|   | ↳ Scout, Bid, Planner, Dev, Critic (глубокие секции) | | |
|   | ↳ Content, Design, Packager (компактные секции) | | |
| 4 | GeoScout | `agents/geo-scout-spec.md` | ⬜ |
|   | ↳ H3, Overpass, geocoding, фильтрация | | |
| 5 | Outreach | `agents/outreach-spec.md` | ⬜ |
|   | ↳ channels, LLM, tone, dispatch | | |

**Ядро** (`core/`):

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 6 | Core Engine (Dev Cycle) | `core/dev-cycle-spec.md` | ⏳ В работе |
|   | ↳ Dynamic routing, Revision loop, Partial failure, delivery_type | | |
| 7 | LLM & Prompts | `core/llm-spec.md` | ⬜ |
|   | ↳ LLM Client, модели, промпты, costs, semantic cache | | |

**Инфраструктура** (`infrastructure/`):

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 8 | Database | `infrastructure/database-spec.md` | ⬜ |
|   | ↳ Schema (18 таблиц), pgvector, миграции | | |
| 9 | API | `infrastructure/api-spec.md` | ⬜ |
|   | ↳ Litestar, 12 route groups, WebSocket, auth | | |
| 10 | Deploy & Monitoring | `infrastructure/deploy-spec.md` | ⬜ |
|   | ↳ Railway, Docker, CI/CD, Sentry, metrics | | |

**Интерфейсы** (`interfaces/`):

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 11 | Dashboard | `interfaces/dashboard-spec.md` | ⏳ В работе |
|   | ↳ 20 страниц, 28 custom компонентов, 3 stores, auth, WebSocket | | |
| 12 | Telegram-бот | `interfaces/telegram-bot-spec.md` | ⬜ |
|   | ↳ Commands, HITL, notifications | | |

**Подсистемы** (`subsystems/`):

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 13 | HITL-система | `subsystems/hitl-spec.md` | ⬜ |
|   | ↳ Все HITL точки, UI, audit, expiry | | |
| 14 | Enrichment & Delivery | `subsystems/enrichment-spec.md` | ⬜ |
|   | ↳ Waterfall, Hunter, Apollo, OSINT, senders | | |
| 15 | Platform-адаптеры | `subsystems/platform-adapters-spec.md` | ⬜ |
|   | ↳ 5 платформ, rate limiter, browser, stealth | | |
| 16 | Security | `subsystems/security-spec.md` | ⬜ |
|   | ↳ Semgrep, encryption, policies | | |
| 17 | RAG-память | `subsystems/rag-memory-spec.md` | ⬜ |
|   | ↳ Knowledge base, embeddings, ingestion, retrieval | | |

**Отдельно:**

| # | Блок | Спека | Статус |
|---|------|-------|--------|
| 18 | Portfolio Agent | `portfolio-agent-spec.md` | ⬜ |

**Результат:** 18 глубоких спек в `docs/vision/`, организованных в 5 подпапок по доменам.

---

## Фаза 2: МЕГАФАЙЛ-ИНДЕКС

Создаём `docs/vision/MAS.md` — единая точка входа во всю систему.

**Структура:**
```
MAS.md
├── Общая архитектура (схема, стек, 10 агентов)
├── Pipeline A (10-20 строк) → ссылка на pipeline-a-spec.md
├── Pipeline B (10-20 строк) → ссылка на pipeline-b-spec.md
├── Агенты (сводная таблица) → ссылки на agents/*.md
├── Ядро (graph, state, LLM) → ссылки на core/*.md
├── Инфраструктура (БД, API, deploy) → ссылки на infrastructure/*.md
├── Интерфейсы (Dashboard, TG-бот) → ссылки на interfaces/*.md
├── Подсистемы (HITL, enrichment, adapters, security, RAG) → ссылки на subsystems/*.md
├── HITL-точки (сводная таблица по всей системе)
├── Приоритеты (P0-P3 глобальные)
└── Ключевые файлы (полная карта проекта, 120+ компонентов)
```

Каждый блок: что делает, из чего состоит, как связан с другими + ссылки на глубокие спеки.

**Схема связей (обязательна в MAS.md):**
ASCII data flow между блоками — как данные текут от Scout через Bid/Planner/Dev к Packager, как HITL пронизывает все этапы, как RAG-память питается от каждого завершённого заказа, как Platform-адаптеры используются Scout'ом и Bid'ом.

**Визуальная часть:**
Dashboard spec включает ASCII-mockup'ы ключевых экранов: Jobs (два таба), Bid Kanban (5 колонок), Dashboard Home (командный центр), HITL Queue, Lead Detail, Geo Scanner.

**Результат:** один файл чтобы понять всю систему за 5 минут.

---

## Фаза 3: АУДИТ (идеал vs реальность)

Сверяем спеки с кодом:

### 3.1 Gap Analysis
Для каждой спеки — таблица:

| Фича из спеки | В коде? | Тесты? | Работает? |
|---------------|---------|--------|-----------|
| Scout скан платформ | ✅ | ✅ | ✅ |
| Bid Kanban UI | ❌ | — | — |
| RAG Experience Store | ❌ | — | — |

### 3.2 E2E тесты
Полноценные end-to-end тесты по критическим путям:
- Pipeline A: Scout → Bid → HITL → submit → (mock) client response
- Pipeline B: GeoScout → Enrichment → Outreach → HITL
- Dashboard: login → jobs → HITL approve → status update
- API: все endpoints, auth, WebSocket

**Важно:** E2E тесты покрывают только СУЩЕСТВУЮЩИЙ код. Для нереализованных фич из спеки — gap report в audit-report.md, не тесты.

### 3.3 Audit Report
`docs/vision/audit-report.md` — результаты:
- Что работает (с доказательствами: тесты, скриншоты)
- Что не работает (с root cause)
- Что отсутствует (gap vs спека)
- Приоритезированный план доработок

---

## Фаза 4: ЧИСТКА

### 4.1 Файлы
- Архивировать устаревшие доки → `docs/archive/`
- Удалить дубликаты и одноразовые артефакты
- Обновить CLAUDE.md под новую структуру

### 4.2 Код
- Удалить dead code найденный аудитом
- Привести в соответствие с спеками (naming, структура)
- Обновить тесты под актуальное поведение

### 4.3 Документация
- Исправить противоречия (LLM модели, таблицы БД, API роуты)
- MAS.md становится главным entry point
- Старые доки ссылаются на новые спеки или архивируются

---

## Порядок работы

```
[Фаза 1] Описание блоков (18 спек, 5 подпапок)
  Pipeline A ✅
  → Pipeline B (сейчас) — включает agents/geo-scout + agents/outreach
  → agents/pipeline-a-agents (Scout, Bid, Planner, Dev, Critic + support)
  → core/ (engine + llm)
  → infrastructure/ (database + api + deploy)
  → interfaces/ (dashboard + telegram-bot)
  → subsystems/ (hitl + enrichment + adapters + security + rag)
  → portfolio-agent

[Фаза 2] Мегафайл
  MAS.md (после всех спек, ссылки на подпапки)

[Фаза 3] Аудит
  Gap analysis → E2E тесты → Audit report

[Фаза 4] Чистка
  Архивация → Dead code → Обновление CLAUDE.md
```

---

## Итоговая структура `docs/vision/`

```
docs/vision/
├── MAS.md                              ← мегафайл-индекс (вся система)
│
├── pipeline-a.md                       ← три взгляда Pipeline A (контекст)
├── pipeline-a-spec.md                  ← спека Pipeline A (source of truth) ✅
├── pipeline-b.md                       ← три взгляда Pipeline B
├── pipeline-b-spec.md                  ← спека Pipeline B
│
├── agents/                             ← глубокие спеки агентов
│   ├── pipeline-a-agents-spec.md       ← Scout, Bid, Planner, Dev, Critic + support
│   ├── geo-scout-spec.md              ← GeoScout: H3, Overpass, geocoding
│   └── outreach-spec.md              ← Outreach: channels, LLM, tone, dispatch
│
├── core/                              ← ядро системы
│   ├── engine-spec.md                 ← Graph, State, Checkpoints, Heartbeat
│   └── llm-spec.md                    ← LLM Client, модели, промпты, costs
│
├── infrastructure/                    ← инфраструктура
│   ├── database-spec.md               ← Schema (18 таблиц), pgvector, миграции
│   ├── api-spec.md                    ← Litestar, routes, WebSocket, auth
│   └── deploy-spec.md                 ← Railway, Docker, CI/CD, monitoring
│
├── interfaces/                        ← интерфейсы пользователя
│   ├── dashboard-spec.md              ← 15 страниц, 47 компонентов, auth
│   └── telegram-bot-spec.md           ← Commands, HITL, notifications
│
├── subsystems/                        ← кросс-системные подсистемы
│   ├── hitl-spec.md                   ← Все HITL точки, UI, audit, expiry
│   ├── enrichment-spec.md             ← Waterfall, Hunter, Apollo, senders
│   ├── platform-adapters-spec.md      ← 5 платформ, browser, stealth
│   ├── security-spec.md               ← Semgrep, encryption, policies
│   └── rag-memory-spec.md             ← Knowledge base, embeddings, retrieval
│
├── portfolio-agent-spec.md            ← 11-й агент (планируется)
│
└── audit-report.md                    ← результаты аудита
```
