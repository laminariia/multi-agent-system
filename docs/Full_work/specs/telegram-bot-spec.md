# Telegram-бот: Спецификация

## Назначение

HITL-интерфейс и операторский пульт управления через Telegram. Альтернатива Dashboard для быстрых решений с мобильного. Два блока функциональности: HITL-очередь и управление Orchestrator'ом.

## Архитектура

- **Библиотека**: `python-telegram-bot` 21.x
- **Режим**: Long Polling (`app.run_polling()`)
- **Entry point**: `python -m src.bot` (`src/bot/__main__.py`)
- **Handlers**: `CommandHandler` + `CallbackQueryHandler` (inline keyboards)
- **БД**: shared `get_db_session()` / `get_valkey()` с основным приложением
- **Токен**: `TELEGRAM_BOT_TOKEN` из Settings

### Application builder (`src/bot/handler.py`)

`create_bot_application()` -> регистрирует 17 handlers:
- 7 HITL commands: `/start`, `/status`, `/pending`, `/stats`, `/approve`, `/skip`, `/scan`
- 8 Orchestrator commands: `/run`, `/stop`, `/orch`, `/goals`, `/health`, `/milestones`, `/logs`, `/add_goal`
- 2 callback handlers: `orch:*` pattern + fallback HITL buttons

### Dashboard Sync Listener

Background task подписывается на Valkey pub/sub каналы:
- `hitl:resolved:bot` -- уведомления о HITL-резолюциях из Dashboard
- `orch:event:bot` -- события оркестратора

При получении сообщения пересылает форматированный текст в `TELEGRAM_CHAT_ID`.

## Commands

### HITL-блок

| Команда | Описание | Требует linked account? |
|---------|----------|------------------------|
| `/start` | Welcome, генерация 6-символьного link code (alphanumeric, 10 мин TTL в Valkey). При `/start auto` -- auto-link к первому owner. При linked -- показывает help + inline keyboard | Нет |
| `/status` | Обзор здоровья агентов: green/blue/red circles для каждого агента + pending HITL count. Данные из `agent_heartbeats` (healthy threshold: 3 мин) | Да |
| `/pending` | Показывает до 5 pending HITL items как interactive cards с inline buttons. Сортировка: urgent first, newest first | Да |
| `/stats` | Статистика HITL за сегодня: pending, resolved, expired, total | Да |
| `/approve <id>` | Resolve HITL item как approved. Проверяет: UUID валидный, элемент exists, status=pending, action available | Да |
| `/skip <id>` | Resolve HITL item как skipped. Аналогичные проверки | Да |
| `/scan <city>` | Запуск Pipeline B geo scan для указанного города. Fire-and-forget `asyncio.create_task(run_pipeline_b(city))` | Да |

### Orchestrator-блок

| Команда | Описание |
|---------|----------|
| `/orch` | Главная панель: статус runner'а (работает/остановлен, PID), pending/completed/failed goals, health grade. Inline keyboard с навигацией |
| `/run` | Запуск orchestrator-runner как background subprocess. Режим: self-direct. macOS: `bash runner.sh`, Windows: `powershell runner.ps1`. PID сохраняется в файл |
| `/stop` | Остановка runner'а: `kill {pid}` (Unix) / `taskkill /PID {pid} /T /F` (Windows). Удаление PID-файла |
| `/goals [pending\|done]` | Список целей из БД (OrchestratorGoal), группировка по статусу. Inline keyboard: Pending / Done / All / Back |
| `/health` | Health report: overall grade, score, dimension grades (Code, Tests, Infra, CI/CD, Docs, API, Security, Quality) в 2-колоночной таблице. Проблемы с severity emoji |
| `/milestones [all]` | Прогресс milestones из vision.md: фазы, checkbox-статус. Inline: "Все фазы" / "Статус" |
| `/logs [N]` | Последние N строк (default 10, max 50) runner-лога. Формат: время [LEVEL] message. Inline: "+20 строк" / "+50 строк" |
| `/add_goal <title>` | Добавление новой цели в БД. Auto-increment ID: `g_001`, `g_002`, ... Priority: medium, Category: feature |

## HITL через бот

### Push notification при новом HITL

`TelegramNotifier` (`src/bot/notifications.py`) -- отдельный модуль для отправки из Litestar-процесса (без зависимости от python-telegram-bot). Использует `httpx` напрямую к Telegram Bot API.

Формат карточки:
```
{emoji} New HITL Item
{title}
Type: {type} | Priority: URGENT
{description[:200]}
ID: {uuid}
Expires: 2026-03-07 14:30 UTC
[Approve] [Skip] [Later]   <- inline buttons
```

Type -> emoji маппинг:
- `bid_approval` -> memo
- `code_review` -> magnifying glass
- `delivery` -> package
- `revision` -> arrows counterclockwise
- `alert` -> warning sign

### Inline buttons (HITL cards)

Callback data format: `hitl:<action>:<uuid_hex>` (max ~46 bytes, в пределах Telegram лимита 64 байта).

Per-type button sets (`src/bot/keyboards.py`):

| Type | Buttons |
|------|---------|
| `bid_approval` | Approve, Skip, Later |
| `code_review` | Approve, Skip |
| `delivery` | Approve, Later |
| `revision` | Approve, Skip, Later |
| `alert` | Ack, Skip |
| (default) | Approve, Skip |

Фильтрация: показываются только кнопки, action которых есть в `item.available_actions`.

### Callback handler (`button_callback`)

1. Parse `hitl:<action>:<uuid_hex>`
2. Verify linked Telegram account (`User.telegram_chat_id == tg_user_id`)
3. Load HITLQueue item, проверить status=pending, action available
4. Apply resolution: `item.status = "resolved"`, `item.resolution = action`, `item.resolved_by = user.id`
5. Edit original message: "Resolved: {Action}" + item ID

### Orchestrator callback handler (`orch_button_callback`)

Pattern: `orch:<action>[:<arg>]`. Actions: `status`, `goals[:pending|:done]`, `health`, `milestones[:all]`, `logs[:N]`, `run`, `stop`.

Каждый callback `edit_message_text` с обновлённым содержимым и новой клавиатурой.

## Уведомления

### Из Dashboard (Valkey pub/sub)

Dashboard sync listener получает events и пересылает в чат:

- `hitl:resolved:bot`: "HITL resolved from dashboard" -- type, title, action, next_action, resolved_by
- `orch:event:bot`: "Orchestrator event: {event}" -- message

### Из Orchestrator Runner

`src/bot/orchestrator_notify.py` -- standalone CLI script, вызывается runner-скриптом:

```bash
python -m src.bot.orchestrator_notify --event session_complete --data '{"session":3}'
python -m src.bot.orchestrator_notify --event critical_error --data '{"consecutive":3}'
python -m src.bot.orchestrator_notify --event all_goals_done --data '{"phase":2}'
```

Events:
- `session_complete` -- "Сессия #{N} завершена ({duration}мин)", goals_completed, health before->after, commits
- `critical_error` -- "Оркестратор: {N} ошибки подряд!", exit code, last error. "Остановлен автоматически"
- `all_goals_done` -- "Все цели фазы выполнены!", phase, milestones

## Безопасность

### Авторизация

- **Linked account check**: декоратор `@require_linked_account` -- ищет `User` по `User.telegram_chat_id == tg_user_id`. Если не найден -- "Your Telegram account is not linked"
- **Link flow**: `/start` -> генерация 6-char alphanumeric code -> `Valkey.setex("telegram_link:{code}", 600, tg_user_id)` -> пользователь вводит код в Dashboard Settings
- **Auto-link**: `/start auto` -- привязка к первому owner в БД (для быстрого setup)
- **HITL resolve**: проверка linked account в callback handler (не role-based в боте, в отличие от API где `require_role("owner", "co_owner")`)

### Ограничение доступа

- `TELEGRAM_CHAT_ID` в Settings -- единственный чат для push notifications
- Linked account -- per-user, через telegram_chat_id в таблице users
- Bot menu commands видны всем, но все handlers кроме `/start` требуют linked account

## Статус реализации

### Реализовано

- 15 команд (7 HITL + 8 Orchestrator)
- Inline keyboards с навигацией и HITL-actions
- HITL карточки с type-specific кнопками
- Dashboard sync через Valkey pub/sub
- Orchestrator runner management (run/stop/status)
- Goals management (CRUD через БД)
- Health report + milestones + logs viewer
- Push notifications: TelegramNotifier (httpx) + orchestrator_notify (CLI)
- Link code generation + auto-link

### Не реализовано

- Edit flow через бот (редактирование payload через Telegram -- только approve/skip/later)
- Групповые чаты (только 1:1 с ботом)
- Webhook mode (только polling)
- Multi-user notifications (один TELEGRAM_CHAT_ID)
- Notification preferences (всё или ничего)
- Rate limiting команд бота
- Timeout/expiry alerts для HITL items

## Детальные форматы HITL-карточек (из legacy-дизайна)

### bid_approval

```
{priority_emoji} New Bid for Approval

Job: {job_title[:50]}...
Platform: {platform}
Bid Amount: ${bid_amount:,.0f}
Client Rating: {client_rating} star(s)

Proposal preview:
{proposal_text[:200]}...

[Approve] [Skip] [Later] [View Full -> Dashboard URL]
```

### code_review

```
{priority_emoji} Code Review Required

Project: {project_name}
Files: {files_count} files
Quality Score: {quality_score:.0%}

Semgrep: {security_issues} issues

[Approve] [Review in Dashboard -> URL]
```

### delivery

```
{priority_emoji} Delivery Ready

Project: {project_name}
Client: {client_name}
Deadline: {deadline}

Files packaged and ready for delivery.

[Deliver] [Review -> URL] [Hold]
```

### Notification Types (расширенные)

| Тип | Приоритет | Триггер |
|-----|-----------|---------|
| `urgent_hitl` | High | Бид истекает через < 2 часов |
| `new_hitl` | Normal | Новый HITL-элемент добавлен |
| `project_won` | High | Клиент принял бид |
| `deadline_warning` | High | Дедлайн проекта через < 24ч |
| `agent_error` | High | Агент упал 3+ раза подряд |
| `budget_alert` | Normal | Дневной бюджет > 80% |

## Deep Links

| Link | Назначение |
|------|------------|
| `t.me/MASBot?start=link_{code}` | Привязка Telegram к Dashboard-аккаунту |
| `t.me/MASBot?start=hitl_{id}` | Открытие конкретного HITL-элемента |
| `t.me/MASBot?start=project_{id}` | Просмотр статуса проекта |

## Quiet Hours (не реализовано)

Запланированная функциональность для подавления non-urgent уведомлений в ночное время.

| Параметр | Default | Описание |
|----------|---------|----------|
| `quiet_hours.enabled` | `false` | Включение тихих часов |
| `quiet_hours.start` | 23 | Начало (23:00 по timezone пользователя) |
| `quiet_hours.end` | 8 | Конец (08:00) |
| `timezone` | `UTC` | Timezone пользователя из `user.settings` |

Правила:
- **Urgent** уведомления отправляются ВСЕГДА, даже в тихие часы
- Остальные уведомления буферизируются и отправляются после окончания тихих часов
- Overnight mode (start > end, например 23-8) -- корректная обработка перехода через полночь

## Rate Limiting команд (не реализовано)

Запланированные лимиты:

| Категория | Лимит | Описание |
|-----------|-------|----------|
| `commands` | 30/мин | Общий лимит на команды |
| `hitl_actions` | 10/мин | Approve/Skip/Later действия |
| `notifications` | 30/мин per user | Исходящие уведомления |

Реализация: Valkey key `tg_rate:{user_id}:{category}` с TTL = window.

## Таблица telegram_notifications (не реализовано)

```sql
CREATE TABLE telegram_notifications (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    hitl_id         UUID REFERENCES hitl_queue(id),
    message_id      BIGINT,          -- Telegram message ID (для edit/delete)
    chat_id         BIGINT,
    sent_at         TIMESTAMPTZ,
    read_at         TIMESTAMPTZ,
    action_taken    VARCHAR(30),     -- approve/skip/later/NULL
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

Назначение: аудит отправленных уведомлений, отслеживание прочитанных/обработанных, предотвращение дублирования.

## Конфигурация (расширенная)

```python
# Обязательные переменные окружения
TELEGRAM_BOT_TOKEN = "..."          # Токен бота от @BotFather
TELEGRAM_CHAT_ID = "..."            # ID чата для push notifications

# Опциональные (из Settings)
DASHBOARD_URL = "..."               # URL для deep links в кнопках

# Лимиты
MAX_NOTIFICATIONS_PER_HOUR = 30     # Per-user notification rate limit
URGENT_THRESHOLD_HOURS = 2          # HITL urgent если expires_at < 2ч

# Webhook mode (не реализовано, текущий: polling)
TELEGRAM_WEBHOOK_URL = "..."        # URL для webhook endpoint
WEBHOOK_MAX_CONNECTIONS = 100
WEBHOOK_ALLOWED_UPDATES = ["message", "callback_query"]
```

## Ключевые файлы

- `src/bot/__main__.py` -- entry point (`python -m src.bot`)
- `src/bot/handler.py` -- Application builder, command registration, dashboard sync listener
- `src/bot/commands.py` -- HITL commands (/start, /status, /pending, /stats, /approve, /skip, /scan)
- `src/bot/keyboards.py` -- Inline keyboards, HITL card formatting, button callback handler
- `src/bot/notifications.py` -- TelegramNotifier (httpx-based, для Litestar-процесса)
- `src/bot/orchestrator_commands.py` -- Orchestrator commands (/run, /stop, /orch, /goals, /health, /milestones, /logs, /add_goal)
- `src/bot/orchestrator_notify.py` -- CLI script для push notifications от runner'а
