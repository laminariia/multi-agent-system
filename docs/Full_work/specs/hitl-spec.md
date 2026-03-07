# HITL-система: Спецификация

## Назначение

Human-in-the-Loop (HITL) -- механизм остановки пайплайна для получения решения оператора. Система гарантирует, что ни один бид не отправляется, ни один код не деплоится, ни одно письмо не уходит без явного approve от человека.

## Архитектура

### HITLQueue таблица (PostgreSQL)

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID (PK) | `gen_random_uuid()` |
| `type` | String(30) | Тип: `bid_approval`, `dev_launch`, `code_review`, `delivery`, `revision`, `scope_creep`, `plan_review`, `alert`, `email_approval`, `final_review`, `job_review` |
| `priority` | String(10) | `urgent` / `normal` / `low` (default: `normal`) |
| `bid_id` | UUID (FK) | Связь с таблицей `bids` |
| `project_id` | UUID (FK) | Связь с таблицей `projects` |
| `task_id` | UUID (FK) | Связь с таблицей `tasks` |
| `title` | String(500) | Заголовок для отображения |
| `description` | Text | Описание для оператора |
| `payload` | JSONB | Полные данные для принятия решения (bid_amount, proposal_text, scored_job и т.д.) |
| `available_actions` | ARRAY(Text) | Доступные действия: `["approve", "reject", "edit", "skip", "later"]` |
| `status` | String(20) | `pending` / `resolved` / `expired` |
| `resolution` | String(30) | Какое действие выбрал оператор |
| `resolution_note` | Text | Комментарий оператора |
| `resolved_by` | UUID (FK) | Кто принял решение (users.id) |
| `created_at` | DateTime(tz) | Время создания |
| `expires_at` | DateTime(tz) | Дедлайн (nullable) |
| `resolved_at` | DateTime(tz) | Время резолюции |
| `telegram_sent` | Boolean | Уведомление в TG отправлено |
| `email_sent` | Boolean | Уведомление по email отправлено |

Индексы: `(status, priority, created_at)`, `(type, status)`.

### LangGraph-интеграция

Паттерн остановки графа:

1. Агент (Bid, Planner, Critic, Packager, Outreach) устанавливает `requires_hitl=True`, `status="paused"` через `update_state()`
2. Routing-функция (`_route_after_bid`, `_route_after_critic` и т.д.) проверяет `state.get("requires_hitl")` и направляет в HITL-ноду
3. HITL-нода (`hitl_bid_node`, `hitl_review_node`, `hitl_outreach_node`) фиксирует `status="paused"` и возвращает state
4. Граф завершается (роутинг к END из HITL-ноды при `status=="paused"`)
5. Состояние сохраняется через `HybridCheckpointSaver` (Valkey + PostgreSQL)

Паттерн возобновления:

1. Оператор вызывает `POST /api/v1/hitl/{id}/resolve` с `action` и опциональным `edited_payload`
2. API-хэндлер обновляет HITLQueue запись, публикует события в WebSocket и Valkey pub/sub
3. Fire-and-forget: `asyncio.create_task(resume_from_hitl(thread_id, hitl_response))`
4. `resume_from_hitl()` загружает checkpoint, применяет `_apply_*_approval()`, запускает граф с обновленным state

## Все HITL-точки

### Pipeline A (5 точек)

| # | Момент | action_type | payload | Обязательна? | Действия оператора |
|---|--------|-------------|---------|--------------|-------------------|
| 1 | Scout нашел заказ, LLM оценил | `job_review` | `scored_job` (title, description, budget, score, platform, skills_required) | Да | approve → Bid Agent запускается; reject → заказ отклонен; skip → пропущен |
| 2 | Bid Agent сгенерировал предложение | `bid_approval` | `bid_amount`, `proposal_text/preview`, `platform`, `client_rating`, `thread_id` | Да | approve → бид отправляется на платформу; edit → правка текста/суммы; reject → бид отброшен |
| **3** | **Бид отправлен, ждём решения оператора на запуск разработки** | **`dev_launch`** | См. ниже | **Да** | **start_development → Planner; negotiate_more → пауза (ожидание); decline → END** |
| 4 | Planner декомпозировал проект | `plan_review` | План задач, estimated_hours, зависимости | Условно (>20ч) | approve → execution (agent_sequence[0]); edit → правка плана; reject → план отклонен |
| 5 | Critic обнаружил scope creep / reject | `scope_creep` или `code_review` | quality_score, security_issues, test_coverage, feedback | При эскалации | approve → продолжить; reject → Dev переделывает; escalate → к оператору |
| 6 | Packager подготовил финальную доставку | `final_review` | deliverables, client_name, deadline | Да | approve → доставка клиенту; edit → правки; reject → на доработку |

### Pipeline B (3 точки)

| # | Момент | action_type | payload | Обязательна? | Действия оператора |
|---|--------|-------------|---------|--------------|-------------------|
| 1 | Outreach сгенерировал сообщения | `email_approval` | drafts (email/telegram), lead info, channel_type, thread_id | Да | approve -> отправка; edit -> правка текстов; reject -> отмена |
| 2 | Revision от клиента | `revision` | client_feedback, revision_number | При scope creep | approve -> принять ревизию; reject -> отклонить |
| 3 | Alert от системы | `alert` | severity, details | При ошибках | acknowledge -> принять к сведению; skip -> игнорировать |

### Dev Cycle (4 точки)

| # | Момент | action_type | payload | Обязательна? | Действия оператора |
|---|--------|-------------|---------|--------------|-------------------|
| 1 | Plan review после Planner | `plan_review` | task decomposition, agent_sequence, delivery_type, estimates | Условно (>20ч, re-plan, major revision) | approve/edit/reject |
| 2 | Code review после Dev | `code_review` | quality_score, security_issues, test_coverage | При Critic-эскалации | approve/reject/edit |
| 3 | Scope creep обнаружен Critic | `scope_creep` | scope change details | При обнаружении | approve/reject/edit |
| 4 | Final delivery после Packager | `final_review` | deliverables | Да | approve/edit/reject |

## API Endpoints

### GET /api/v1/hitl/pending

Список pending HITL-элементов с пагинацией и фильтрацией.

- **Query params**: `type` (фильтр по типу), `search` (ILIKE по title), `limit` (1-100, default 20), `offset` (default 0)
- **Response**: `{ items: HITLItemSchema[], total: int, pending_urgent: int }`
- **Сортировка**: по priority weight (urgent=0, normal=1, low=2), затем newest first
- **Защита от SQL injection**: `_escape_like()` для ILIKE-запросов

### POST /api/v1/hitl/{hitl_id}/resolve

Резолюция одного элемента. Guard: `require_role("owner", "co_owner")`.

- **Body**: `{ action: str, note?: str, edited_payload?: dict }`
- **Валидации**: элемент существует, не resolved, не expired, action в available_actions
- **Side effects**:
  - При `action="edit"` -- merge `edited_payload` в payload
  - При `job_review` + `approve` -- Job.status = "qualified", fire-and-forget `run_bid_generation()`
  - Valkey pub/sub `hitl:resolved:bot` -> TG-бот получает уведомление
  - WebSocket `hitl:resolved` -> Dashboard обновляется в реальном времени
  - Fire-and-forget `resume_from_hitl()` для resumable types: `bid_approval`, `plan_review`, `email_approval`, `final_review`
- **Response**: `{ id, status, resolution, next_action }`

### POST /api/v1/hitl/bulk-resolve

Массовая резолюция с тем же action для нескольких элементов. `SELECT ... WITH FOR UPDATE` для row-level lock.

- **Body**: `{ ids: UUID[], action: str, note?: str }`
- **Response**: `{ resolved: int, failed: int, errors: [{ id, error }] }`
- **Поведение**: пропускает already resolved, expired, invalid action; одно WebSocket-событие `hitl:bulk_resolved`

### GET /api/v1/hitl/stats

Агрегированная статистика: pending/resolved/expired за сегодня, avg_resolution_time_minutes, разбивка по типам.

### GET /api/v1/hitl/trends

Тренды по дням: created/resolved counts за последние N дней (1-90, default 7).

## Dashboard UI

### HITL Queue страница (`_app.hitl.tsx`)

- **Табы-фильтры**: All, Заказы (job_review), Bids, Reviews, Deliveries, Scope, Plans, Alerts
- **Поиск**: debounced (300ms) ILIKE по title
- **Сетка карточек**: responsive grid (1/2/3 колонки)
- **Auto-refresh**: staleTime=10s, refetchInterval=30s
- **Stats header**: Today resolved, avg resolution time, expired count
- **Export CSV**: выгрузка текущей страницы
- **Bulk mode**: checkbox-выделение, плавающая панель внизу с Approve All / Reject All / Skip All / Clear
- **Пустое состояние**: "All clear" с зеленой галочкой

### HITLCard компонент (`hitl-card.tsx`)

- **Type badge**: цветовая маркировка (Bid=bid, Review=review, Delivery=delivery, Alert=alert, Plan=plan, Revision=revision)
- **Priority indicator**: красная полоска вверху + пульсирующая точка для urgent
- **PayloadDetails**: type-specific рендеринг payload:
  - `bid_approval`: platform, bid_amount, client_rating, proposal_preview
  - `code_review`: quality_score (/10 с цветом), security_issues, test_coverage
  - `delivery`: client, deadline, deliverables_count
  - `alert`: severity (цветовая маркировка), details
  - `revision`: client, revision_number, feedback
  - `job_review`: бюджет, платформа, скор (%)
- **Expiry countdown**: иконка часов + formatCountdown
- **Action buttons**: варианты окрашены по типу (approve=success, reject=destructive, skip=outline, later=warning)
- **Loading state**: spinner на активной кнопке
- **Bulk selection**: checkbox (при bulk mode)

### HITLDetailDrawer (`hitl-detail-drawer.tsx`)

- Dialog (modal) с полной информацией о HITL-элементе
- Развернутый PayloadDetails: для `job_review` показывает description, skills badges, score_reason
- Для `bid_approval` -- полный текст proposal в styled block
- Даты: "Истекает" и "Создано" в ru-RU формате
- Action buttons внизу диалога

## Audit Trail

- **resolved_by**: UUID пользователя, связанный через FK с `users.id`
- **resolved_at**: timestamp с timezone
- **resolution**: действие оператора (approve/reject/edit/skip/later)
- **resolution_note**: текстовый комментарий
- **Payload до/после edit**: при `action="edit"` payload мержится (`{...item.payload, ...edited_payload}`), оригинал не сохраняется отдельно (только merged result)
- **Логирование**: structlog с полями `hitl_id`, `action`, `resolved_by`, `next_action`

## Multi-User HITL Indication

При нескольких операторах, работающих с Dashboard одновременно, необходимо предотвратить конфликты.

### Viewing Lock (soft lock)

```typescript
// Dashboard: при открытии HITLDetailDrawer
const startViewing = async (hitlId: string) => {
  await fetch(`/api/v1/hitl/${hitlId}/viewing`, { method: "POST" });
};

const stopViewing = async (hitlId: string) => {
  await fetch(`/api/v1/hitl/${hitlId}/viewing`, { method: "DELETE" });
};
```

**Backend (Valkey-based, не DB):**

```python
# src/api/routes/hitl.py

async def mark_viewing(hitl_id: UUID, user_id: UUID) -> None:
    """Пометить HITL как просматриваемый оператором."""
    key = f"hitl:viewing:{hitl_id}"
    await valkey.hset(key, str(user_id), json.dumps({
        "user_id": str(user_id),
        "started_at": utcnow().isoformat(),
        "username": user.username,
    }))
    await valkey.expire(key, 300)  # 5 минут TTL (auto-cleanup)

async def get_viewers(hitl_id: UUID) -> list[dict]:
    """Получить список просматривающих операторов."""
    key = f"hitl:viewing:{hitl_id}"
    viewers = await valkey.hgetall(key)
    return [json.loads(v) for v in viewers.values()]
```

### Dashboard UI

В карточке HITL-элемента:

```tsx
// В HITLCard — маленькие аватары/инициалы просматривающих
{viewers.length > 0 && (
  <div className="flex -space-x-1">
    {viewers.map(v => (
      <span key={v.user_id} className="inline-flex h-5 w-5 rounded-full bg-blue-500 text-white text-xs items-center justify-center" title={v.username}>
        {v.username[0].toUpperCase()}
      </span>
    ))}
  </div>
)}
```

В HITLDetailDrawer:

```tsx
// Предупреждение если кто-то уже смотрит
{viewers.length > 0 && !viewers.find(v => v.user_id === currentUser.id) && (
  <Alert variant="warning">
    Сейчас просматривает: {viewers.map(v => v.username).join(", ")}
  </Alert>
)}
```

### Race Condition Prevention

Optimistic locking уже реализован в `resolve_hitl()` (SQL `WHERE status='pending'`). Viewing indication — только визуальная подсказка, не блокировка. Если два оператора нажмут Approve одновременно:

1. Первый `resolve_hitl()` → success (rowcount=1)
2. Второй `resolve_hitl()` → HTTP 409 "Already resolved"
3. Dashboard перезагружает карточку → показывает "Resolved by {username}"

## Каналы уведомлений

### Dashboard (WebSocket push)

- Канал `hitl:resolved` -- при единичной резолюции (hitl_id, type, title, action, next_action, resolved_by)
- Канал `hitl:bulk_resolved` -- при массовой (hitl_ids, action, resolved_count, resolved_by)
- Guard: `try/except (OSError, ConnectionError)` -- WebSocket disconnect не ломает API

### Telegram-бот (push notification)

- Valkey pub/sub канал `hitl:resolved:bot`
- Бот-listener подписан и пересылает в чат оператора
- Формат: тип, заголовок, действие, next_action, кто принял решение
- Для новых HITL: `TelegramNotifier.notify_new_hitl()` -- отправка карточки с inline-кнопками (Approve/Skip/Later)
- Guard: `try/except (OSError, ConnectionError)`

## Маппинг next_action

10 типов HITL x 5 действий = 50 комбинаций. Каждая комбинация маппится на описательную метку через `_NEXT_ACTION_MAP`:

- `bid_approval` + `approve` → `bid_will_be_submitted`
- `dev_launch` + `start_development` → `dev_cycle_started`
- `dev_launch` + `negotiate_more` → `waiting_for_client`
- `dev_launch` + `decline` → `project_declined`
- `job_review` + `approve` → `job_accepted_for_bidding`
- `email_approval` + `approve` → `emails_will_be_sent`
- `final_review` + `approve` → `work_delivered_to_client`
- И т.д. для reject, edit, skip, later

## HITL #3: `dev_launch` — Запустить разработку? (НОВАЯ)

> **Принцип:** Dev-цикл НИКОГДА не стартует автоматически. Оператор явно решает,
> начинать ли работу по проекту после того как бид отправлен (или клиент подтвердил).

### Место в графе

```
bid_node → hitl_bid_node → bid_submission_node → hitl_dev_launch_node → planner_node
```

`hitl_dev_launch_node` стоит **между** `bid_submission_node` и `planner_node`. Это обязательная HITL-точка — оператор должен явно нажать "Запустить разработку".

### Нода в LangGraph

```python
async def hitl_dev_launch_node(state: dict[str, Any]) -> dict[str, Any]:
    """HITL gate: оператор решает, запускать ли Dev Cycle.

    Создаёт HITLQueue запись type='dev_launch' и ставит граф на паузу.
    """
    if state["status"] == "paused":
        return state  # Уже на паузе (re-entry after resume)

    artifacts = dict(state.get("artifacts") or {})
    project = state.get("project") or {}
    bid_data = artifacts.get("bid", {})

    # Создаём HITL запись
    hitl_id = str(uuid.uuid4())
    async with get_db_session() as session:
        hitl = HITLQueue(
            id=uuid.UUID(hitl_id),
            type="dev_launch",
            priority="normal",
            title=f"Запустить разработку: {project.get('title', 'Unknown')[:200]}",
            description="Бид отправлен. Запустить Dev Cycle для этого проекта?",
            payload={
                "thread_id": state["thread_id"],
                "platform": project.get("platform"),
                "job_title": project.get("title", ""),
                "job_description": project.get("requirements", "")[:500],
                "bid_amount": bid_data.get("amount", 0),
                "bid_submitted": artifacts.get("bid_submitted", False),
                "manual_submit_required": artifacts.get("manual_submit_required", False),
                "client_name": project.get("client", {}).get("name", ""),
                "client_rating": project.get("client", {}).get("rating"),
            },
            available_actions=["start_development", "negotiate_more", "decline"],
            status="pending",
        )
        session.add(hitl)
        await session.commit()

    artifacts["_hitl_started_at"] = time.time()

    return update_state(
        state,
        status="paused",
        requires_hitl=True,
        hitl_request_id=hitl_id,
        current_agent="hitl_dev_launch",
        artifacts=artifacts,
    )
```

### Payload

```json
{
  "thread_id": "abc123",
  "platform": "freelancer",
  "job_title": "React лендинг для стоматологии",
  "job_description": "Нужен современный лендинг...",
  "bid_amount": 350.00,
  "bid_submitted": true,
  "manual_submit_required": false,
  "client_name": "John Doe",
  "client_rating": 4.8
}
```

### Действия оператора

| Действие | Описание | State после resume |
|----------|----------|-------------------|
| `start_development` | Запустить Planner → Dev Cycle | `status="active"`, `requires_hitl=False`, route → `planner_node` |
| `negotiate_more` | Пока не начинать, ждём ответа клиента / ведём переговоры | `status="paused"` (остаётся на паузе, HITL перевыставляется) |
| `decline` | Отказаться от проекта | `status="failed"`, route → `END` |

### Routing после resume

```python
def _route_after_dev_launch(state: dict[str, Any]) -> str:
    """Route после HITL dev_launch."""
    if state.get("status") == "failed":
        return END
    # approve → Planner
    if not state.get("requires_hitl"):
        return "planner_node"
    # Всё ещё на паузе (negotiate_more → re-pause)
    return END
```

### Resume handler (`_apply_dev_launch`)

```python
def _apply_dev_launch(state: dict[str, Any], hitl_response: dict) -> dict[str, Any]:
    """Применяет решение оператора по dev_launch."""
    action = hitl_response.get("action", "")

    if action == "start_development":
        return update_state(state,
            status="active",
            requires_hitl=False,
            hitl_request_id=None,
            next_agent="planner",
        )
    elif action == "decline":
        return update_state(state,
            status="failed",
            requires_hitl=False,
            errors=[*state.get("errors", []), "Operator declined dev launch"],
        )
    elif action == "negotiate_more":
        # Перевыставляем HITL — оператор вернётся позже
        return update_state(state,
            status="paused",
            requires_hitl=True,
            # hitl_request_id остаётся (или создаётся новый)
        )

    return state
```

### Dashboard UI

В карточке `dev_launch`:
- Название проекта, платформа, сумма бида
- Статус бида: "Отправлен" / "Требуется ручная отправка"
- Информация о клиенте (рейтинг, имя)
- 3 кнопки: **Запустить** (зелёная), **Подождать** (жёлтая), **Отказаться** (красная)

### Регистрация в графе

```python
graph.add_node("hitl_dev_launch_node", hitl_dev_launch_node)

graph.add_conditional_edges(
    "bid_submission_node",
    _route_after_bid_submission,   # теперь → hitl_dev_launch_node вместо planner_node
    {"hitl_dev_launch_node": "hitl_dev_launch_node", END: END},
)

graph.add_conditional_edges(
    "hitl_dev_launch_node",
    _route_after_dev_launch,
    {"planner_node": "planner_node", END: END},
)
```

### Изменения в `_route_after_bid_submission`

```python
def _route_after_bid_submission(state: dict[str, Any]) -> str:
    """Route после bid submission: ВСЕГДА к dev_launch HITL."""
    if state.get("status") == "failed":
        return END
    return "hitl_dev_launch_node"   # БЫЛО: "planner_node"
```

---

## Resumable HITL Types

Только 5 типов автоматически возобновляют граф после resolve:
- `bid_approval` → bid_submission_node
- `dev_launch` → planner_node (NEW)
- `plan_review` → _route_next_in_sequence (agent_sequence[0])
- `email_approval` → message_dispatch_node
- `final_review` → END (completed)

Остальные типы (`job_review`, `code_review`, `alert` и т.д.) -- только обновляют статус в БД.

## 24-часовой timeout

`resume_from_hitl()` проверяет `artifacts["_hitl_started_at"]`. Если с момента паузы прошло >24 часов -- возвращает `status="failed"` с ошибкой "HITL approval expired after 24 hours".

## Edge Cases и Failure Scenarios (из legacy-дизайна)

### Матрица edge cases

| Категория | Сценарий | Вероятность | Импакт | Приоритет |
|-----------|----------|-------------|--------|-----------|
| Platform | Аккаунт забанен | Low | Critical | P0 |
| Platform | Rate limit hit | Medium | High | P1 |
| Client | Проект отменён mid-work | Medium | High | P1 |
| Client | 10+ побед одновременно | Low | High | P1 |
| Outreach | Email bounce > 10% | Medium | Medium | P2 |
| Agent | Dev fails после 3 retries | Low | Medium | P2 |
| System | LLM API down | Low | Critical | P0 |

### P0: Account Ban

**Детекция**: Login failure + индикаторы бана (`"account has been suspended"`, `"временно заблокирован"`, `"access denied"`, `"permanently banned"`, `"ToS violation"`).

**Автоматическая реакция**:
1. Создать urgent HITL alert: `type="alert"`, `priority="urgent"`, title = `"{platform} ACCOUNT BANNED"`
2. Payload: `account_id`, `platform`, `username`, `reason`, `active_bids` count, `active_projects` count
3. Поставить на паузу все операции на платформе
4. Проверить наличие backup-аккаунта, уведомить оператора
5. Available actions: `acknowledge`, `switch_backup`, `manual_check`

### P0: LLM API Down

**Детекция**: 3+ consecutive 5xx errors или timeouts от LLM-провайдера.

**Failover chain**: DeepSeek V3.2 → Claude → Claude Haiku (emergency).

**При полном outage** (все провайдеры недоступны):
1. Немедленная пауза всех 10 агентов (`agent_paused:{name}` в Valkey)
2. Urgent HITL alert: `type="alert"`, title = `"ALL LLM PROVIDERS DOWN"`
3. Available actions: `pause_system`, `wait_and_retry`
4. Все pending задачи сохраняются в recovery queue (не теряются)
5. Health check loop каждые 5 минут
6. При восстановлении: unpause агентов → process recovery queue по приоритету → уведомление

**Что работает при outage**: Dashboard (static views), HITL queue (approve pending), мониторинг, ручные операции.

**Что блокировано**: Bid submission, code generation, content generation -- всё что требует LLM.

### P1: Проект отменён клиентом mid-work

**Детекция**: ключевые слова `"cancel"`, `"refund"`, `"don't need"`, `"changed mind"`, `"отмена"` в сообщениях клиента.

**Стратегия на основе прогресса**:

| Прогресс | Стратегия | Описание |
|----------|-----------|----------|
| < 20% | `full_refund_likely` | Полный возврат вероятен |
| 20-79% | `partial_payment_negotiate` | Переговоры о частичной оплате |
| >= 80% | `full_payment_request` | Запрос полной оплаты |

HITL: `type="project_cancellation"`, `priority="urgent"`. Payload: `progress_percent`, `hours_spent`, `agreed_amount`, `suggested_strategy`, `draft_response`. Actions: `accept_refund`, `negotiate_partial`, `request_full_payment`, `custom_response`.

### P1: Capacity Overflow (10+ побед)

**Детекция**: `won_today > MAX_CONCURRENT_PROJECTS - active_projects` (MAX_CONCURRENT = 5 в Phase 1).

**Автоматическая реакция**:
1. Ранжирование по приоритету: deadline urgency → bid amount → client rating
2. HITL: `type="capacity_overflow"`, `priority="urgent"`
3. Payload: `recommended_accept` (top N), `recommended_delay` (остальные), `total_value`
4. Actions: `accept_recommended`, `accept_all_overload`, `custom_selection`
5. Шаблон отсрочки для клиентов: "Finishing up a current commitment, can start on [DATE]"

### P1: Platform Rate Limit

**Детекция**: HTTP 429 response.

**Реакция**:
1. Пауза операций на платформе: `platform_paused:{platform}` в Valkey с TTL = retry_after + 60s
2. Если пауза > 1 часа → Telegram-уведомление оператору
3. Автоматическое возобновление через APScheduler job

### P2: Email Bounce Rate > 10%

**Детекция**: `bounce_count / sent_count > 0.10` в рамках кампании.

**Реакция**:
1. Пауза кампании
2. Анализ паттернов bounce (домены, типы)
3. HITL: `type="campaign_health"`, рекомендации: проверить контент на spam-триггеры, верифицировать enrichment sources, проверить domain reputation
4. Actions: `resume`, `pause`, `stop_permanently`

### P2: Dev Agent Fails 3+ раз

**Детекция**: `retry_count >= 3` для одной задачи.

**Реакция**:
1. Retry с exponential backoff: delay = 30 * 2^retry_count секунд
2. После 3 неудач → HITL: `type="agent_failure"`, payload с `task_description`, `error`, `logs`, `suggestions`
3. Actions: `retry_with_different_approach`, `break_into_subtasks`, `assign_manually`, `skip_task`
4. Задача помечается `status="blocked"`

## Client Dispute Escalation

### Типы диспутов

| Тип | Триггер | Время ответа |
|-----|---------|-------------|
| Quality complaint | Клиент считает работу неполной/неправильной | 24ч |
| Non-payment | Клиент отказывается платить после delivery | 48ч |
| Scope creep | Клиент хочет больше, чем согласовано | 24ч |
| Cancellation refund | Клиент хочет возврат после начала работы | 24ч |
| Plagiarism claim | Клиент утверждает что работа скопирована | Немедленно |

### Детекция

Ключевые слова по типам:
- **Quality**: `"not what I asked"`, `"doesn't work"`, `"bugs"`, `"wrong"`, `"incomplete"`
- **Payment**: `"won't pay"`, `"refund"`, `"dispute"`, `"chargeback"`, `"scam"`
- **Scope**: `"also need"`, `"you should add"`, `"this wasn't included"`, `"more features"`
- **Plagiarism**: `"copied"`, `"plagiarism"`, `"not original"`, `"seen this before"`

### Escalation Flow

1. Детекция ключевых слов в сообщении клиента
2. Сбор доказательной базы: original requirements, delivered files, message history, completed milestones, time logs
3. Генерация стратегии защиты (LLM) по типу диспута
4. **Plagiarism** → НИКОГДА не auto-respond, немедленный HITL
5. HITL: `type="dispute"`, `priority="urgent"`, payload с `evidence_summary` и `suggested_response`
6. Actions: `send_suggested_response`, `escalate_to_platform`, `offer_partial_refund`, `stand_firm`, `custom_response`

### Platform-specific resolution

| Платформа | Система диспутов | Подход |
|-----------|-----------------|--------|
| Freelancer | Dispute Resolution Center | Собрать evidence, ответить в 24ч |
| Upwork | Mediation → Arbitration | Избегать диспутов (HITL предотвращает большинство) |
| FL.ru | Admin moderation | Шаблоны ответов на русском |

## Recovery Procedures

### Автоматическое восстановление

| Сценарий | Auto-actions | Требует HITL? | Время эскалации |
|----------|-------------|---------------|-----------------|
| Account ban | pause_platform, switch_backup | Да | Немедленно |
| Rate limit | pause_and_wait, resume_when_cleared | Нет (если < 1ч) | 1 час |
| LLM down | failover_to_backup | Да (если все down) | 5 минут |
| Project cancellation | pause_work, calculate_refund | Да | Немедленно |
| Capacity overflow | rank_by_priority | Да | Немедленно |

### Manual Recovery Checklist

| Сценарий | Шаги |
|----------|------|
| Account Banned | 1. Проверить email 2. Подать апелляцию 3. Зарегистрировать новый аккаунт 4. Обновить credentials |
| All LLMs Down | 1. Проверить status pages провайдеров 2. Ждать восстановления 3. Очистить expired из очереди |
| Data Corruption | 1. Остановить все агенты 2. Восстановить из backup 3. Верифицировать integrity 4. Возобновить операции |

## Мониторинг и Alerting

### Ключевые метрики

| Метрика | Warning | Critical |
|---------|---------|----------|
| Account login failures | 2/час | 5/час |
| LLM error rate | 5% | 20% |
| Task retry rate | 10% | 30% |
| Email bounce rate | 5% | 10% |
| Queue depth | 50 jobs | 100 jobs |
| HITL response time | > 2ч | > 4ч |

### Каналы алертов по severity

| Severity | Каналы |
|----------|--------|
| Critical (P0) | Telegram (немедленно) + Dashboard + Email |
| High (P1) | Telegram + Dashboard |
| Medium (P2) | Dashboard only |
| Low | Только логи |

## Ключевые файлы

- `src/api/routes/hitl.py` -- API endpoints (HITLController)
- `src/api/schemas.py` -- Pydantic-схемы запросов/ответов
- `src/core/models.py` -- HITLQueue ORM-модель
- `src/core/graph.py` -- HITL-ноды, routing, resume_from_hitl(), _apply_*_approval()
- `src/core/state.py` -- update_state() с requires_hitl, hitl_request_id
- `dashboard/app/routes/_app.hitl.tsx` -- HITL Queue страница
- `dashboard/app/components/hitl-card.tsx` -- карточка HITL-элемента
- `dashboard/app/components/hitl-detail-drawer.tsx` -- детальный просмотр
- `dashboard/app/lib/api.ts` -- fetchHITLPending, resolveHITL, bulkResolveHITL, fetchHITLStats
- `src/bot/commands.py` -- /pending, /approve, /skip команды
- `src/bot/keyboards.py` -- inline-кнопки и callback handler
- `src/bot/notifications.py` -- TelegramNotifier.notify_new_hitl()
