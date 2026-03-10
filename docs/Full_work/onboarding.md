# Onboarding: Руководство для новых разработчиков

> Точка входа в проект MAS. Настройка окружения, карта документации, ключевые паттерны.

---

## Первоначальная настройка

1. Клонируйте репозиторий
2. `cp .env.example .env` — заполните секреты (см. комментарии в `.env.example`)
3. Создайте Python virtual environment:
   ```bash
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -e ".[dev]"
   ```
4. Установите Claude Code CLI: https://docs.anthropic.com/en/docs/claude-code
5. Запустите `claude` в корне проекта — автоматически загрузит `CLAUDE.md` и `.claude/rules/`

## MCP Servers

При первом запуске Claude Code предложит одобрить MCP-серверы из `.mcp.json`:

| Сервер | Назначение |
|--------|-----------|
| context7 | Документация библиотек |
| sequential-thinking | Сложные рассуждения |
| exa | Web search для исследований |
| playwright | Browser automation и тестирование |
| serena | Семантический анализ кода |
| railway | Управление деплоем (требует `railway login`) |

---

## Карта документации

### Как читать документацию

```
Что за проект?         → CLAUDE.md (краткое описание + правила для ИИ)
Полная архитектура?    → docs/Full_work/MASTER-VISION.md (Single Source of Truth)
Найти конкретную спеку → docs/Full_work/MAS.md (индекс со ссылками)
Детали по подсистеме   → docs/Full_work/specs/*.md (19 спецификаций)
Как писать код?        → .claude/rules/patterns.md + RULES.md
Что-то сломалось?      → .claude/rules/debugging.md
```

### Иерархия документов

```
MASTER-VISION.md                    ← Единый источник истины
  │                                    (архитектура, решения, конфликты)
  │
  ├── MAS.md                        ← Индекс (навигация по всем спекам)
  │
  ├── Pipeline-спеки (3 файла)      ← Детали пайплайнов
  │   ├── pipeline-a-spec.md           Scout → Bid → HITL → Dev Cycle
  │   ├── pipeline-b-spec.md           GeoScout → Outreach → Email
  │   └── dev-cycle-spec.md            Planner → Dev/Content/Design → Critic → Packager
  │
  ├── specs/ (19 файлов)            ← Технические спецификации подсистем
  │   ├── agents-spec.md               11 агентов: роли, state, LangGraph nodes
  │   ├── api-spec.md                  Litestar API: routes, auth (JWT), rate limiting
  │   ├── database-spec.md             PostgreSQL 16 + pgvector: schema, миграции
  │   ├── deploy-spec.md               Railway, Docker, CI/CD, backup/recovery
  │   ├── security-spec.md             Playwright stealth, Semgrep, encryption
  │   ├── hitl-spec.md                 15 HITL-точек, edge cases, audit trail
  │   ├── orchestrator-spec.md         Sisyphus runner, goals, health checks
  │   ├── testing-spec.md              Стратегия, паттерны, 2330+ тестов
  │   ├── llm-spec.md                  OpenRouter, модели, costs, semantic cache
  │   ├── rag-memory-spec.md           Knowledge base, embeddings, vector search
  │   ├── enrichment-spec.md           OSINT waterfall, Hunter, Apollo, email warmup
  │   ├── geo-scout-spec.md            H3 гексы, Overpass API, geocoding
  │   ├── outreach-spec.md             Email/Telegram, touch sequence, тон
  │   ├── sales-agent-spec.md          SalesAgent, Business Analyzer, Lead Scorer
  │   ├── negotiation-spec.md          State machine переговоров, 9 состояний
  │   ├── platform-adapters-spec.md    6 платформ: Freelancer, FL.ru, Kwork, Upwork, Fiverr, YouDo
  │   ├── telegram-bot-spec.md         Telethon, HITL через Telegram
  │   ├── infrastructure-spec.md       WebSocket, concurrency, crash recovery
  │   └── legal-compliance-spec.md     GDPR, ToS, data retention, consent
  │
  ├── интерфейс.md                  ← UI/UX Dashboard (Remix + shadcn/ui)
  │
  ├── portfolio/                    ← Стратегия портфолио
  │   ├── strategy.md                  Холодный старт, 6 платформ
  │   ├── portfolio-agent.md           Агент #12 (будущее)
  │   └── visuals-research.md          Исследование визуалов
  │
  ├── ideas/                        ← Backlog идей
  │   └── client-growth-upselling.md   Pipeline C (будущее)
  │
  └── archive/                      ← Исторические документы (read-only)
      ├── reviews/                     6 code review от 2026-02-23
      ├── research/                    Market research, конкуренты
      └── *.md                         Старые версии спек (не редактировать)
```

### Файлы в корне проекта

| Файл | Назначение | Когда читать |
|------|-----------|-------------|
| `CLAUDE.md` | Правила для ИИ-агентов: стек, паттерны, git safety | Всегда (загружается автоматически) |
| `RULES.md` | Конвенции кода: именование, импорты, структура | При написании кода |
| `TECH_STACK.md` | Полный стек с обоснованиями, LLM assignments | При выборе технологий |
| `.env.example` | Все env vars с комментариями | При настройке окружения |

### Файлы для ИИ-агентов

| Файл | Назначение |
|------|-----------|
| `.claude/rules/patterns.md` | Канонические паттерны: agent, state, HITL, routing, tests |
| `.claude/rules/debugging.md` | Критические gotchas: LangGraph, Railway, Playwright |
| `.claude/agents/*.md` | 5 агентов для Team Mode |

### Контент (не спецификации)

| Папка | Назначение |
|-------|-----------|
| `docs/portfolio/` | 14 описаний проектов для платформ (RU+EN) |
| `docs/gui/screenshots/` | Скриншоты текущего Dashboard |
| `docs/gui/mockups/` | Мокапы UI (Pencil.dev) |
| `docs/review/` | Аудиты документации (2026-03-03) |

---

## Team Mode

Агенты в `.claude/agents/` готовы для сложных задач:

| Агент | Область | Что делает |
|-------|---------|-----------|
| Feature Worker | `src/` | Реализация фич, багфиксы |
| Quality Worker | `tests/` | Тесты, code review |
| Research Worker | `docs/` | Исследования, документация |
| Infra Worker | `docker/`, `alembic/`, CI | Инфраструктура, миграции |
| DB Migration Reviewer | read-only | Ревью Alembic-миграций |

## Запуск тестов

```bash
# Быстрая проверка (unit tests)
pytest tests/unit/ -x

# Полный набор (2330+ тестов)
pytest tests/ -x

# Линтинг
ruff check src/ tests/

# Проверка форматирования
ruff format --check src/ tests/
```

## Ключевые архитектурные заметки

- **10 реализованных + 2 проектируемых агента** в двух пайплайнах (A: фриланс-биржи, B: прямые продажи) + Dev Cycle Engine
- **Litestar** фреймворк (НЕ FastAPI)
- **Valkey** для кэширования (НЕ Redis) — env var `VALKEY_URL`
- **LangGraph** для оркестрации — см. `.claude/rules/debugging.md` для критических gotchas
- HITL (Human-in-the-Loop) — 9 обязательных + 6 условных точек (детали в `specs/hitl-spec.md`)
- **pgvector HNSW** для vector search (НЕ DiskANN)
- Все LLM-вызовы через **OpenRouter** (`OPENROUTER_API_KEY`), 6-tier LLM система (см. MASTER-VISION.md §4)
- **6 платформ**: Freelancer.com (API), FL.ru (RSS), Kwork (scraper), Upwork (read-only!), Telegram, Fiverr [PLANNED]
- Upwork auto-submit **ЗАПРЕЩЁН** (нарушение ToS)

## Деплой

Два Railway-сервиса:
- **API**: `railway up --detach` (из корня проекта)
- **Dashboard**: `railway up --detach --service dashboard --path-as-root dashboard`

Критично: `--path-as-root dashboard` для Dashboard — без него Railway использует Python nixpacks.

## Получение помощи

| Вопрос | Где искать |
|--------|-----------|
| Что за проект, зачем, как устроен? | `docs/Full_work/MASTER-VISION.md` |
| Где спека по конкретной подсистеме? | `docs/Full_work/MAS.md` → ссылка на нужную спеку |
| Как написать нового агента? | `.claude/rules/patterns.md` → "Agent Pattern" |
| Что-то сломалось при разработке? | `.claude/rules/debugging.md` |
| Как работает HITL? | `docs/Full_work/specs/hitl-spec.md` |
| Как деплоить? | `docs/Full_work/specs/deploy-spec.md` |
| Какие LLM используются? | `TECH_STACK.md` → "LLM Models" |
