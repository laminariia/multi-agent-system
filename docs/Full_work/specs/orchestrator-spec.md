# Orchestrator (Sisyphus): Спецификация

> Автономный оркестратор MAS — управляет жизненным циклом runner-процесса, целями (goals), health-мониторингом и milestone-трекингом проекта. Вдохновлён паттернами Sisyphus (Oh My OpenCode): task categories, hook system, todo-continuation-enforcer, background execution.

## Назначение

Orchestrator решает три задачи:

1. **Runner lifecycle** — запуск/остановка автономного процесса (`orchestrator-runner.sh`), который выполняет agent-driven сессии без фиксированного таймера. Агент сам решает, когда завершить сессию (code review clean, все goals done).
2. **Goal management** — CRUD целей в PostgreSQL (`orchestrator_goals`). Цели создаются через Dashboard или Telegram-бот, runner забирает `pending` цели и выполняет.
3. **Observability** — health report (8 измерений, YAML), vision milestones (Markdown), runner logs. Dashboard отображает всё в реальном времени через WebSocket.

## Архитектура

### Компоненты

```
┌──────────────────────────────────────────────────────────────┐
│                      Dashboard (Remix)                       │
│   Orchestrator Page: status, goals, health, milestones, logs │
└──────────────────────┬──────────────────────┬────────────────┘
                       │ REST                 │ WebSocket
                       ▼                      ▼
┌──────────────────────────────┐  ┌────────────────────────────┐
│  OrchestratorController      │  │  ChannelsPlugin            │
│  /api/v1/orchestrator/*      │  │  orch:status, orch:goal,   │
│  6 endpoints                 │  │  orch:log                  │
└──────────────┬───────────────┘  └────────────────────────────┘
               │
               ▼
┌──────────────────────────────┐
│  OrchestratorService         │
│  Бизнес-логика (stateless)   │
│  DB ↔ Parsers ↔ Subprocess   │
└──────────┬───────────────────┘
           │
     ┌─────┴──────────────┐
     ▼                    ▼
┌──────────┐    ┌─────────────────────┐
│ PostgreSQL│    │ File-based state    │
│ goals     │    │ ~/.claude/          │
│ table     │    │  orchestrator/      │
└──────────┘    │    health-report.yaml│
                │    vision.md         │
                │    runner.pid        │
                │  logs/               │
                │    runner_YYYY-MM-DD │
                │  scripts/            │
                │    orchestrator-     │
                │    runner.sh         │
                └─────────────────────┘
```

### Data Flow

1. Пользователь нажимает **Start** в Dashboard → `POST /api/v1/orchestrator/start`
2. `OrchestratorService.start_runner()` запускает `~/.claude/scripts/orchestrator-runner.sh` как detached process
3. PID записывается в `~/.claude/orchestrator/runner.pid`
4. WebSocket публикует `orch:status { action: "started", pid }` всем подключённым клиентам
5. Runner выполняет agent-driven сессии, обновляет `health-report.yaml` и логи
6. Dashboard периодически запрашивает `GET /status`, `/health`, `/logs` для обновления UI

## Реализация

### API Routes — `src/api/routes/orchestrator.py`

`OrchestratorController` (Litestar Controller, path `/api/v1/orchestrator`):

| Метод | Endpoint | Guard | Описание |
|-------|----------|-------|----------|
| `GET` | `/status` | — | Статус runner: alive, pid, uptime, mode, goal counts, health grade |
| `POST` | `/start` | `owner`, `co_owner` | Запуск runner как detached process |
| `POST` | `/stop` | `owner`, `co_owner` | Остановка runner (kill PID) |
| `GET` | `/goals` | — | Список целей, фильтр по `?status=pending` |
| `POST` | `/goals` | `owner`, `co_owner` | Создание новой цели |
| `DELETE` | `/goals/{goal_id}` | `owner`, `co_owner` | Удаление цели по human-readable ID (`g_001`) |
| `GET` | `/health` | — | Health report: 8 измерений, overall grade, problems |
| `GET` | `/milestones` | — | Фазы и milestones из `vision.md` |
| `GET` | `/logs` | — | Последние N строк лога runner (`?n=20&date=2026-03-01`) |

Все мутирующие endpoints публикуют WebSocket-события через `publish_event()` с guard-паттерном `try/except (OSError, ConnectionError)` для защиты от разрывов WS-соединения.

### Service Layer — `src/api/services/orchestrator.py`

`OrchestratorService` — stateless класс, инстанцируется как module-level singleton `_svc`.

**Ключевые методы:**

```python
class OrchestratorService:
    # Async (DB)
    async def get_status(session: AsyncSession) -> dict
    async def list_goals(session: AsyncSession, status_filter: str | None) -> dict
    async def add_goal(session: AsyncSession, title: str, priority: str, category: str) -> dict
    async def delete_goal(session: AsyncSession, goal_id: str) -> dict

    # Sync (file I/O)
    def start_runner() -> dict          # subprocess.Popen, detached
    def stop_runner() -> dict           # taskkill/kill PID
    def get_health() -> dict            # parse YAML
    def get_milestones() -> list[dict]  # parse Markdown
    def get_logs(n: int, date: str | None) -> dict  # tail log file
```

**Goal ID generation:**
Автоинкрементный формат `g_NNN` — запрашивает все существующие `goal_id` из БД, парсит числовую часть, `max + 1`.

**Runner start (кроссплатформенный):**
- macOS/Linux: `bash orchestrator-runner.sh self-direct`, `start_new_session=True`
- Windows: `powershell -File orchestrator-runner.ps1 -Mode self-direct`, `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`

### Parsers — `src/orchestrator/parsers.py`

Синхронные файловые парсеры, безопасны для вызова из async-контекста.

| Функция | Что делает |
|---------|-----------|
| `is_runner_alive() -> (bool, pid)` | Проверяет PID file + `os.kill(0)` / `OpenProcess` (Windows). Фильтрует zombie-процессы через `ps -o stat=` |
| `parse_health_report(path?) -> dict` | Парсит YAML-подобный `health-report.yaml` регулярками (без pyyaml-зависимости) |
| `parse_vision_md(path?) -> list[dict]` | Парсит Markdown: `## Phase N — Title`, `- [x] milestone` |
| `tail_file(path, n) -> list[str]` | Последние N строк файла |
| `get_runner_log_path(date?) -> Path` | Находит лог: конкретная дата или последний по glob `runner_*.log` |

### DB Model — `src/core/models.py`

```python
class OrchestratorGoal(Base):
    __tablename__ = "orchestrator_goals"

    id: UUID            # PK, gen_random_uuid()
    goal_id: str(20)    # unique, human-readable ("g_001")
    title: str(500)
    priority: str(20)   # critical | high | medium | low
    category: str(30)   # feature | bugfix | docs | testing | infra | refactor
    status: str(20)     # pending | completed | failed
    result: Text | None
    context: Text | None
    success_criteria: ARRAY(Text) | None
    depends_on: ARRAY(Text) | None
    created_at: DateTime(tz)
    completed_at: DateTime(tz) | None
```

Индексы: `idx_orch_goals_status`, `idx_orch_goals_goal_id`.

### WebSocket каналы — `src/api/websocket.py`

| Канал | Когда публикуется |
|-------|-------------------|
| `orch:status` | Start/stop runner |
| `orch:goal` | Add/delete goal |
| `orch:log` | (зарезервирован для стриминга логов) |

### Sisyphus-паттерны (из `docs/sisyphus_orchestration_patterns.md`)

Документ описывает паттерны оркестрации, адаптированные из Oh My OpenCode для MAS. Текущая реализация покрывает runner lifecycle и goal management. Следующие паттерны описаны для будущей реализации:

| Паттерн | Приоритет | Статус | Описание |
|---------|-----------|--------|----------|
| **Task Categories** | High | Не реализован | Преднастроенные профили LLM для типов задач (`freelance-bid`, `code-generation`, `design-ui` и др.) с моделью, temperature, thinking budget |
| **Hook System** | High | Не реализован | 4 точки перехвата: `PreToolUse`, `PostToolUse`, `AgentStart`, `AgentStop`. `HookRegistry` с `register()` / `trigger()` |
| **Todo Continuation Enforcer** | High | Не реализован | Ключевой Sisyphus-паттерн: агент не может "сдаться" — hook на `agent_stop` возвращает агента в работу до завершения всех задач или `max_retries` → HITL |
| **Background Executor** | Medium | Не реализован | Параллельный запуск агентов через `asyncio.create_task()` + результаты в Valkey. Лимиты concurrency per-provider: Anthropic=3, Google=10, OpenAI=5 |
| **Prometheus Planning** | Medium | Частично (Planner agent) | Interview-based планирование с clarifying questions → HITL → hidden requirements → plan generation → plan validation |
| **Fallback Chains** | Low | Не реализован | Цепочки fallback-моделей для каждого агента (primary → secondary → tertiary) |
| **Swarm Teams** | Low | Не реализован | Организация команд агентов с tmux UI для параллельной работы |

## Конфигурация

### Paths (hardcoded, `~/.claude/`)

| Константа | Путь | Назначение |
|-----------|------|-----------|
| `ORCH_DIR` | `~/.claude/orchestrator/` | PID file, health report, vision |
| `LOG_DIR` | `~/.claude/logs/` | Runner logs (`runner_YYYY-MM-DD.log`) |
| `SCRIPTS_DIR` | `~/.claude/scripts/` | Runner script |
| `PID_FILE` | `~/.claude/orchestrator/runner.pid` | PID запущенного runner |
| `HEALTH_REPORT_FILE` | `~/.claude/orchestrator/health-report.yaml` | YAML health report |
| `VISION_FILE` | `~/.claude/orchestrator/vision.md` | Фазы и milestones проекта |
| `RUNNER_SCRIPT` | `~/.claude/scripts/orchestrator-runner.sh` (`.ps1` на Windows) | Bash/PowerShell скрипт runner |

### Env Vars

Orchestrator не использует собственных env vars. Зависит от:
- `DATABASE_URL` — PostgreSQL для таблицы `orchestrator_goals`
- `JWT_SECRET` — авторизация API endpoints через guards

### Goal параметры

| Поле | Допустимые значения | Default |
|------|---------------------|---------|
| `priority` | `critical`, `high`, `medium`, `low` | `medium` |
| `category` | `feature`, `bugfix`, `docs`, `testing`, `infra`, `refactor` | `feature` |
| `status` | `pending`, `completed`, `failed` | `pending` |

### API Limits

| Параметр | Значение |
|----------|----------|
| Max log lines per request | 200 (`min(n, 200)`) |
| Goal title length | 3–500 символов |
| Goal ID format | `g_NNN` (auto-increment) |

## Зависимости

### Внутренние модули

| Модуль | Что используется |
|--------|------------------|
| `src/core/models.py` | `OrchestratorGoal` ORM model |
| `src/api/schemas.py` | Все `Orchestrator*Schema`, `Goal*Schema`, `Health*Schema`, `Phase/Milestone/LogSchema` |
| `src/api/guards.py` | `require_role("owner", "co_owner")` для мутирующих endpoints |
| `src/api/websocket.py` | `publish_event()`, каналы `CHANNEL_ORCH_STATUS`, `CHANNEL_ORCH_GOAL` |
| `src/orchestrator/parsers.py` | File-based state: PID, health, vision, logs |

### Внешние зависимости

| Пакет | Использование |
|-------|---------------|
| `litestar` | Controller, guards, ChannelsPlugin, exceptions |
| `sqlalchemy` | AsyncSession, select, func.count |
| `structlog` | Structured logging |

### Связанные спеки

- **API Specification** (`docs/api_specification.md`) — общий формат API, auth, rate limiting
- **Auth Specification** (`docs/auth_specification.md`) — JWT guards, роли `owner`/`co_owner`
- **Database Schema** (`docs/database_schema.md`) — таблица `orchestrator_goals`
- **Deployment** (`docs/deployment.md`) — Railway deploy, `~/.claude/` paths

## Ключевые файлы

| Файл | Описание |
|------|----------|
| `src/api/routes/orchestrator.py` | REST API controller — 6 endpoints, WebSocket publish, guards |
| `src/api/services/orchestrator.py` | Business logic — runner lifecycle, goal CRUD, health/milestones/logs |
| `src/orchestrator/parsers.py` | File parsers — PID check, YAML health, Markdown vision, log tail |
| `src/core/models.py` | ORM model `OrchestratorGoal` (таблица `orchestrator_goals`) |
| `src/api/schemas.py` | Pydantic schemas: `OrchestratorStatusSchema`, `GoalSchema`, `HealthReportSchema`, `PhaseSchema`, `LogLineSchema` и др. |
| `src/api/websocket.py` | WebSocket каналы `orch:status`, `orch:goal`, `orch:log` |
| `docs/sisyphus_orchestration_patterns.md` | Референсный документ Sisyphus-паттернов для будущей реализации |
