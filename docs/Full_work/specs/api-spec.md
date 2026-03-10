# API (Litestar): Спецификация

## Стек

| Компонент | Технология |
|-----------|------------|
| Framework | **Litestar** (НЕ FastAPI!) |
| Launch | `litestar --app src.api.main:app run --reload` |
| Auth | JWT (`JWTAuth[User]`, HS256) |
| WebSocket | ChannelsPlugin + Valkey PubSub backend |
| Docs | OpenAPI 3.1 на `/schema`, Swagger UI, Redoc |
| Logging | structlog |

## Middleware и Конфигурация

- **CORS:** allow_origins из settings, allow_methods: GET/POST/PUT/PATCH/DELETE/OPTIONS, allow_credentials: true
- **Rate Limiting:** 300/min глобально (исключая /health, /schema, /metrics), 60/min на auth controller. Railway reverse proxy коллапсирует все IP в один -- rate limit фактически глобальный
- **Security Headers:** X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy, Permissions-Policy, X-XSS-Protection, HSTS (в production)
- **Exception Handlers:** MASException -> JSON, HTTPException -> JSON, Exception -> 500 JSON (без stack trace)
- **Lifespan:** startup проверяет DB + Valkey + инициализирует SemanticCache + seed admin user; shutdown закрывает engine + valkey + container

## Route Groups

### /health -- Health Check
| Метод | Путь | Описание | Auth |
|-------|------|----------|------|
| GET | `/health` | Проверка DB + Valkey, version, статус healthy/degraded/unhealthy | Нет |

### /api/v1/auth -- Аутентификация
| Метод | Путь | Описание | Auth |
|-------|------|----------|------|
| POST | `/register` | Регистрация. Первый пользователь = owner (auto-login). Остальные = pending_approval (202) | Нет |
| POST | `/login` | Email + password -> JWT access + refresh tokens | Нет |
| POST | `/refresh` | Обмен refresh token на новую пару access + refresh | Нет |
| POST | `/logout` | Добавляет JTI токена в blacklist (Valkey, TTL = остаток жизни токена) | Да |
| GET | `/me` | Текущий пользователь | Да |
| POST | `/telegram/link` | Привязка Telegram через 6-символьный код от бота | Да |

Rate limit: 60/min на весь controller (кроме /me).

### /api/v1/jobs -- Заказы
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/` | Список заказов, фильтры: status, platform, min_score, search, sort. Пагинация limit/offset | Да | any |
| GET | `/stats` | Агрегация: by_platform, by_status, total | Да | any |
| GET | `/{job_id}` | Детали заказа + все bids (selectinload) | Да | any |
| GET | `/{job_id}/messages` | Proposal text + Freelancer API messages (если platform_bid_id) | Да | any |
| POST | `/{job_id}/disqualify` | Ручная дисквалификация (только new/qualified) | Да | owner |
| POST | `/scan` | Триггер Scout Agent (background task). Body: `{"platform": "all"}` | Да | owner/co_owner/moderator |
| POST | `/{job_id}/run-pipeline` | Запуск Pipeline A для заказа (qualified/bid_sent/won) | Да | owner/co_owner |

### /api/v1/hitl -- HITL очередь
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/pending` | Pending items. Фильтры: type, search. Сортировка по priority (urgent first) | Да | any |
| POST | `/{hitl_id}/resolve` | Resolve: approve/reject/edit/skip/later. Merge edited_payload при edit. Resume pipeline для resumable типов. WS + Valkey pub/sub нотификации | Да | owner/co_owner |
| POST | `/bulk-resolve` | Bulk resolve нескольких items одним action | Да | owner/co_owner |
| GET | `/stats` | Сегодня: pending/resolved/expired, avg_resolution_time, breakdown by type | Да | any |
| GET | `/trends` | Тренды: daily created/resolved за последние N дней (default 7, max 90) | Да | any |

**Resumable HITL types:** bid_approval, plan_review, email_approval, final_review.
**job_review approve:** автоматически ставит job.status = qualified + запускает Bid pipeline.

### /api/v1/agents -- Мониторинг агентов
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/status` | Статус всех агентов. system_health: healthy/degraded/critical. Dead threshold: 180s | Да | any |
| GET | `/{name}/logs` | Логи агента. Фильтры: limit (max 500), level, since | Да | any |
| POST | `/{name}/restart` | Restart через Valkey pub/sub + increment restart_count | Да | owner |
| POST | `/{name}/pause` | Пауза агента | Да | owner |
| POST | `/{name}/resume` | Возобновление агента | Да | owner |

### /api/v1/settings -- Настройки и Credentials
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/credentials` | Обзор: API keys (env + user) + platform accounts. Все masked | Да | owner/co_owner |
| GET | `/platform-accounts` | Список аккаунтов платформ (credentials masked) | Да | owner/co_owner |
| POST | `/platform-accounts` | Создание аккаунта. Platforms: freelancer, upwork, fl_ru, kwork, fiverr | Да | owner/co_owner |
| PUT | `/platform-accounts/{id}` | Обновление (credentials re-encrypted) | Да | owner/co_owner |
| DELETE | `/platform-accounts/{id}` | Удаление аккаунта | Да | owner/co_owner |
| PUT | `/api-keys` | Сохранение API keys (8 known: gemini, anthropic, openai, openrouter, e2b, hunter, apollo, langsmith) | Да | owner/co_owner |
| POST | `/test-credential` | Тест API key (Gemini, Anthropic, OpenAI, Hunter -- реальный HTTP запрос) | Да | owner/co_owner |

### /api/v1/users -- Управление пользователями
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/` | Список пользователей, фильтр по status | Да | owner/co_owner |
| POST | `/{id}/approve` | Утвердить pending регистрацию, назначить роль | Да | owner/co_owner |
| POST | `/{id}/reject` | Отклонить регистрацию | Да | owner/co_owner |
| PATCH | `/{id}/role` | Изменить роль (owner не меняется, co_owner назначает только owner) | Да | owner/co_owner |
| PATCH | `/{id}/status` | Suspend / reactivate (нельзя себя, нельзя owner) | Да | owner/co_owner |
| DELETE | `/{id}` | Удалить пользователя (нельзя себя, нельзя owner) | Да | owner/co_owner |
| POST | `/{id}/transfer-ownership` | Передать ownership (только owner) | Да | owner |

### /api/v1/orchestrator -- Автономный оркестратор
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/status` | Статус runner (alive, goals, health) | Да | any |
| POST | `/start` | Запуск runner как detached process | Да | owner/co_owner |
| POST | `/stop` | Остановка runner | Да | owner/co_owner |
| GET | `/goals` | Список целей, фильтр по status | Да | any |
| POST | `/goals` | Добавить цель (title, priority, category) | Да | owner/co_owner |
| DELETE | `/goals/{goal_id}` | Удалить цель по human-readable ID | Да | owner/co_owner |
| GET | `/health` | Health report (8 dimensions, overall grade + score) | Да | any |
| GET | `/milestones` | Milestones из vision.md (phases) | Да | any |
| GET | `/logs` | Tail runner log (max 200 lines) | Да | any |

### /api/v1/pipeline-b -- Pipeline B (Geo + Outreach)
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| POST | `/scan` | Запуск GeoScout для города (background). Body: `{"city": "Berlin"}` | Да | owner/co_owner/moderator |
| GET | `/leads` | Список лидов. Фильтры: city, status, search, sort. Пагинация | Да | any |
| GET | `/leads/{id}` | Детали лида (geo, contacts, enrichment) | Да | any |
| POST | `/leads/{id}/enrich` | Запуск enrichment waterfall (OSINT -> Hunter -> Apollo) | Да | owner/co_owner/moderator |
| GET | `/stats` | Статистика: by_status, top_cities | Да | any |

### /api/v1/campaigns -- Email/Outreach кампании
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/` | Список кампаний, фильтр по status | Да | any |
| GET | `/{id}` | Детали кампании | Да | any |
| POST | `/` | Создание кампании (name, subject_template, body_template, target_cities, target_categories) | Да | owner/co_owner/moderator |
| PUT | `/{id}` | Обновление кампании | Да | owner/co_owner/moderator |
| DELETE | `/{id}` | Удаление кампании + campaign_leads | Да | owner/co_owner |
| POST | `/{id}/start` | Старт: привязка enriched leads с email, status -> active | Да | owner/co_owner/moderator |
| GET | `/{id}/stats` | Breakdown: sent/failed/bounced/pending/approved | Да | any |
| GET | `/{id}/leads` | Leads кампании с персонализацией и статусами | Да | any |

### /api/v1/telegram-channels -- Telegram каналы
| Метод | Путь | Описание | Auth | Роль |
|-------|------|----------|------|------|
| GET | `/` | Список каналов (active_only фильтр) | Да | any |
| POST | `/` | Добавить канал (username, title, category). `@` стрипается | Да | owner/co_owner |
| PATCH | `/{id}` | Обновить active/title/category | Да | owner/co_owner |
| DELETE | `/{id}` | Удалить канал из мониторинга | Да | owner |

### /metrics -- Prometheus
| Метод | Путь | Описание | Auth |
|-------|------|----------|------|
| GET | `/metrics` | Prometheus exposition format (generate_latest) | Нет |

## WebSocket

**Endpoint:** `ws://host/ws/events` (exclude_from_auth)

**Lifecycle:**
1. Client подключается -> `accept()`
2. Client шлет `{"type": "auth", "token": "Bearer ..."}`
3. Server валидирует JWT -> подписывает на default channels -> `auth:ok`
4. Server ping каждые 30s (keepalive), client отвечает pong
5. Client может подписаться: `subscribe:project` (project_id), `subscribe:agent` (agent)

**Default channels (9):**

| Канал | Назначение |
|-------|------------|
| `agent:heartbeat` | Heartbeat-статусы агентов |
| `agent:log` | Логи агентов в реальном времени |
| `hitl:new` | Новые HITL-запросы |
| `hitl:resolved` | Разрешенные HITL-запросы |
| `project:update` | Обновления проектов |
| `notification` | Общие уведомления |
| `orch:status` | Статус оркестратора |
| `orch:goal` | Изменения целей оркестратора |
| `orch:log` | Логи оркестратора |

**Динамические каналы:** `project:{uuid}`, `agent:{name}`

**Publish Guard Pattern:** все `publish_event()` / `channels.publish()` обернуты в `try/except (OSError, ConnectionError)` -- WebSocket disconnect не ломает API response.

**Max auth failures:** 5 попыток -> close(4001)

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `src/api/main.py` | App factory, CORS, rate limit, exception handlers, lifespan |
| `src/api/websocket.py` | WebSocket handler, channel definitions, publish_event() |
| `src/api/guards.py` | JWT auth, require_role guard, password hashing |
| `src/api/schemas.py` | Pydantic response/request schemas |
| `src/api/dependencies.py` | DI providers (db_session, valkey, settings) |
| `src/api/routes/__init__.py` | `_escape_like()` SQL wildcard escape |
| `src/api/routes/jobs.py` | JobController (6 endpoints) |
| `src/api/routes/hitl.py` | HITLController (5 endpoints) |
| `src/api/routes/auth.py` | AuthController (6 endpoints) |
| `src/api/routes/agents.py` | AgentController (5 endpoints) |
| `src/api/routes/settings.py` | SettingsController (7 endpoints) |
| `src/api/routes/users.py` | UserController (7 endpoints) |
| `src/api/routes/orchestrator.py` | OrchestratorController (9 endpoints) |
| `src/api/routes/pipeline_b.py` | PipelineBController (5 endpoints) |
| `src/api/routes/campaigns.py` | CampaignController (8 endpoints) |
| `src/api/routes/telegram_channels.py` | TelegramChannelController (4 endpoints) |
| `src/api/routes/health.py` | Health check endpoint |
| `src/api/routes/metrics.py` | Prometheus metrics endpoint |
| `src/api/services/orchestrator.py` | OrchestratorService (бизнес-логика) |

---

## JWT Token Structure

### Access Token (24h expiry, default_token_expiration)

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "role": "owner",
  "iat": 1706560000,
  "exp": 1706646400
}
```

### Refresh Token (7 days expiry)

```json
{
  "sub": "user-uuid",
  "type": "refresh",
  "iat": 1706560000,
  "exp": 1707164800
}
```

### Token Lifecycle

1. Login/Register -> пара access + refresh tokens
2. Access token истек -> POST `/refresh` с refresh token -> новая пара
3. Logout -> JTI текущего токена добавляется в blacklist (Valkey, TTL = остаток жизни токена)
4. `retrieve_user_handler(token, connection)` -> извлечение User по `token.sub`

**Env var:** `JWT_SECRET_KEY` (обязательный, HS256). Генерация: `openssl rand -hex 32`. При ротации -- все сессии инвалидируются.

### Litestar JWT Auth Setup

```python
jwt_auth = JWTAuth[User](
    retrieve_user_handler=retrieve_user_handler,
    token_secret=os.environ["JWT_SECRET_KEY"],
    default_token_expiration=timedelta(hours=24),
)
```

---

## RBAC (Role-Based Access Control)

### Роли (4 уровня)

| Роль | Описание |
|------|----------|
| `owner` | Полный доступ. Только 1 в системе. Создается при первой регистрации |
| `co_owner` | Как owner, но не может модифицировать owner/co_owner аккаунты |
| `moderator` | Управление jobs, HITL, agents. Без доступа к settings/users |
| `viewer` | Read-only доступ |

### Permission Matrix

| Действие | owner | co_owner | moderator | viewer |
|----------|-------|----------|-----------|--------|
| View Dashboard | + | + | + | + |
| View Analytics | + | + | + | + |
| View Agent Logs | + | + | + | + |
| Resolve HITL | + | + | - | - |
| Restart/Pause/Resume Agents | + | - | - | - |
| Start Scan (Scout/GeoScout) | + | + | + | - |
| Run Pipeline | + | + | - | - |
| Manage Campaigns | + | + | + | - |
| Manage Settings/Credentials | + | + | - | - |
| Manage Users | + | + | - | - |
| Transfer Ownership | + | - | - | - |
| Disqualify Jobs | + | - | - | - |

### Guard реализация

```python
def require_role(*roles: str):
    """Litestar guard -- проверяет роль пользователя."""
    async def guard(connection: ASGIConnection, handler: BaseRouteHandler) -> None:
        if not connection.user or connection.user.role not in roles:
            raise PermissionDeniedException("Insufficient permissions")
    return guard

# Использование:
@post("/{hitl_id:uuid}/resolve", guards=[require_role("owner", "co_owner")])
async def resolve_hitl(self, hitl_id: UUID) -> dict: ...
```

---

## Rate Limiting (расширенная стратегия)

### Layer 1: API Rate Limits (Litestar RateLimitConfig)

| Группа | Лимит | Примечание |
|--------|-------|------------|
| Глобальный | 300/min | Исключая /health, /schema, /metrics |
| Auth controller | 60/min | Кроме /me |
| WebSocket events | 100/sec | -- |

Railway reverse proxy коллапсирует все client IP в один внутренний IP (`100.64.0.3`) -- rate limit фактически глобальный для всех пользователей.

### Layer 2: Platform Rate Limits

| Платформа | Лимит | Тип |
|-----------|-------|-----|
| Freelancer API | 100 req/hour | REST API |
| Upwork GraphQL | 60 req/min | Browser (read-only!) |
| FL.ru RSS | 10 req/hour | RSS feed |
| Kwork | 30 pages/hour | Playwright scraper |

### Layer 3: LLM Provider Rate Limits (tier-based)

| Provider | Free/Tier0 | Tier 1 ($5-50/mo) | Tier 2+ ($250+/mo) | TPM |
|----------|-----------|-------------------|-------------------|-----|
| Gemini Flash | 5-15 RPM | 150-300 RPM | 2000+ RPM | 1M |
| Gemini Pro | 2 RPM | 60 RPM | 1000 RPM | 500K |
| Claude Opus | 50 RPM | 50-100 RPM | 300+ RPM | 40K |
| Claude Sonnet | 50 RPM | 200 RPM | 1000+ RPM | 400K |

**CRITICAL:** Free tier = 5 RPM Gemini = ~2 шага агента/минуту. Требуется Tier 1+ для реального параллелизма.

### Layer 4: Enrichment APIs

| API | Лимит |
|-----|-------|
| Hunter.io | 500 req/day |
| Apollo.io | 300 req/day |
| Overpass API | 10,000 req/day |

### Layer 5: Business Targets (24/7 operation)

| Метрика | Значение |
|---------|----------|
| Bids/day | до 150 |
| Projects/day | 3-4 (target 100/month) |
| Emails/hour | до 100 |
| Geo scans/hour | до 20 |

### Rate Limit Response Headers

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1706569200
```

При превышении (HTTP 429):
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests",
    "details": {"retry_after": 15}
  }
}
```

### Retry Strategy (Exponential Backoff)

- `max_retries`: 5
- `base_delay`: 1.0s
- `max_delay`: 60.0s
- `exponential_base`: 2.0
- Jitter: +10% random для предотвращения thundering herd
- Retriable exceptions: `RateLimitError`, `TimeoutError`

---

## LLM Budget Control

### Конфигурация

```python
BudgetConfig(
    daily_limit_usd=50.0,
    monthly_limit_usd=500.0,
    alert_threshold=0.8  # Alert at 80%
)
```

### Алерты

| Условие | Severity | Действие |
|---------|----------|----------|
| Daily budget > 80% | Warning | Telegram notification |
| Monthly budget > 90% | Error | HITL + пауза optional агентов |
| Budget exhausted | Critical | `BudgetExceededError`, полная остановка LLM-вызовов |

### Request Batching

`PromptBatcher` объединяет несколько промптов в один API-вызов (max batch 10, max wait 500ms). Сокращает RPM usage в 5-10x.

### Response Caching

`ResponseCache` -- кэш LLM-ответов по hash промпта в Valkey (TTL 24h). Устраняет повторные вызовы.

### API Key Rotation

`APIKeyPool` -- ротация между несколькими API-ключами провайдера. Каждый ключ получает свой quota. Выбирается ключ с минимальным usage.

---

## Queue Overflow Protection

| Параметр | Значение |
|----------|----------|
| `MAX_PENDING_JOBS` | 100 |
| `MAX_PROCESSING_TIME` | 5 минут на job |
| Overflow action | Prune jobs старше 1 часа -> Dead Letter Queue |
| CPU throttle | > 80% -> пауза 5s |
| Memory throttle | > 85% -> пауза 5s |

---

## Error Codes

| Code | HTTP Status | Описание |
|------|-------------|----------|
| `UNAUTHORIZED` | 401 | Invalid или missing token |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `HITL_EXPIRED` | 410 | HITL request expired |
| `HITL_ALREADY_RESOLVED` | 409 | Already resolved |
| `AGENT_UNAVAILABLE` | 503 | Agent is not responding |
| `RATE_LIMITED` | 429 | Too many requests |

Формат ошибки:
```json
{
  "error": {
    "code": "HITL_EXPIRED",
    "message": "This HITL request has expired",
    "details": {"expired_at": "2026-01-29T21:00:00Z"}
  }
}
```

---

## Telegram Bot API

### Webhook

`POST /api/v1/telegram/webhook`

### Команды

| Команда | Описание |
|---------|----------|
| `/start` | Welcome message |
| `/status` | System health overview |
| `/pending` | Список pending HITL items |
| `/stats` | Статистика за сегодня |
| `/approve {id}` | Quick approve HITL |
| `/skip {id}` | Skip HITL item |
| `/run` | Start orchestrator pipeline |
| `/stop` | Stop orchestrator pipeline |
| `/orch` | Orchestrator control panel (inline keyboard) |
| `/goals` | View goal queue |
| `/health` | Detailed system health |
| `/milestones` | View phase milestones |
| `/logs` | Recent agent logs |
| `/add_goal` | Add new goal to queue |

### Inline Keyboards

**HITL notifications:** Approve, Skip, Later, Open Dashboard.
**Orchestrator control panel** (`/orch`): Start Pipeline, Stop Pipeline, View Goals, System Health, Milestones, Recent Logs.

### Аутентификация Telegram Bot

1. Verification через secret webhook URL
2. `get_user_by_chat_id(chat_id)` -> поиск пользователя по `users.telegram_chat_id`
3. HITL actions требуют роль `owner`

---

## API Versioning

### Формат

```
/api/v{major}/resource
```

### Правила

| Тип изменения | Влияние на версию | Пример |
|---------------|-------------------|--------|
| Bug fix | Patch (без смены URL) | Fix typo в response |
| Новое optional поле | Minor (без смены URL) | Добавить `created_by` |
| Новый endpoint | Minor (без смены URL) | `/api/v1/projects/archive` |
| Обязательное поле изменено | **Major** (новый URL) | Переименование `proposal_text` -> `content` |
| Структура response изменена | **Major** (новый URL) | Переименование `items` -> `data` |
| Удаление endpoint | **Major** (новый URL) | Deprecate -> remove |

### Deprecation Process

1. Анонс v2 (Day 0) + добавить deprecation header к v1
2. Мониторинг v1 usage (30 дней)
3. Deprecation warnings (Day 30)
4. Redirect v1 -> v2 (Day 60)
5. Удаление v1 (Day 90)

### Deprecation Headers

```http
X-API-Deprecated: true
X-API-Deprecation-Date: 2026-04-01
X-API-Replacement: /api/v2/hitl/pending
```

### Текущие версии

| Version | Status |
|---------|--------|
| v1 | **Stable** |
| v2 | Planning |
