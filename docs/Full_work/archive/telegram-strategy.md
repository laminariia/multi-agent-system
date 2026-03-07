# Telegram-стратегия MAS

> Дизайн-документ. Brainstorm 2026-02-24/25. Статус: **дизайн утверждён**, реализация частично начата.
> Обновлено 2026-02-25: добавлен Dual Pipeline Design (секция 7).

---

## 1. Архитектура аккаунтов

### MAS-аккаунт (отдельный от личного)

| Параметр | Значение |
|----------|----------|
| API_ID | `34706111` |
| API_HASH | `a158b8aa...` (в `.env`) |
| Session String | Генерируется интерактивно |
| Назначение | Мониторинг каналов + DM outreach |

**Почему отдельный аккаунт:**
- Бан MAS-аккаунта не затронет личный
- Разделение контекстов (бот vs человек)
- Отдельный warmup-цикл для DM
- Возможность замены без потери личных контактов

**Риски:**
- Telegram может связать аккаунты по IP/устройству — использовать разные IP для авторизации
- Новый аккаунт с активным API = подозрительно — warmup обязателен (см. раздел 4)

---

## 2. Мониторинг каналов

### 2.1 Текущая архитектура

```
TG каналы (22 шт, 3 категории)
    │
    ▼
TelegramListener (Telethon MTProto, отдельный процесс)
    │ events.NewMessage → фильтр по active channels
    │ Refresh каждые 5 мин из БД
    ▼
TelegramParser (DeepSeek V3.2 через OpenRouter)
    │ is_job? → extract: title, description, budget, skills, deadline
    ▼
Valkey queue (mas:tg_jobs:pending, LPUSH/RPOP)
    │
    ▼
TelegramChannelAdapter.fetch_jobs() → Scout Agent → скоринг → Bid → [HITL]
```

### 2.2 Каналы (22 шт, seeded в БД)

**Фриланс (8):** freelead, freelancetaverna, freelansim_ru, kwork_market, frilans, FRILANSb, frilanse, digitaltender

**Бизнес-чаты (10):** onpeak_chat, biznes_chat, BiznesKontakti, bizneschats, chat_biznes1, smp37, sprosiprobiznes, hmoffice, ipomogator1, pomogator

**Нишевые (2):** imexpert_talk (маркетинг), resto_business (HoReCa)

**TODO:** добавить 5-10 EN-каналов для международного расширения.

### 2.3 Ключевые файлы

| Файл | LOC | Роль |
|------|-----|------|
| `src/services/telegram_listener.py` | 304 | Telethon listener, channel refresh |
| `src/services/telegram_parser.py` | 123 | LLM-парсинг постов |
| `src/adapters/telegram_channels.py` | 73 | Scout adapter (rpop из Valkey) |
| `src/api/routes/telegram_channels.py` | 184 | CRUD API каналов |
| `scripts/generate_telegram_session.py` | 72 | Генерация session string |

### 2.4 Известные gaps и решения

| Gap | Критичность | Решение |
|-----|------------|---------|
| Нет дедупликации | Средняя | Хэш `title+description` → проверка в Valkey SET перед LPUSH |
| Message loss при падении Valkey | Средняя | Fallback в SQLite WAL (локальный буфер) |
| Нет алерта при падении listener | Высокая | Heartbeat → Telegram bot alert если нет ping 5 мин |
| Нет backpressure | Низкая | Rate limiter: max 100 LLM-вызовов/мин на парсинг |
| LLM на каждый пост (нет кэша) | Низкая | Keyword pre-filter перед LLM (~$0.001/пост, не критично) |
| Нет FloodWait handling | Низкая | Telethon сам бэкофит для чтения |

---

## 3. DM Outreach (TelegramDMSender)

### 3.1 Концепция

Дополнение к Pipeline B (GeoScout → Outreach). После генерации персонализированного сообщения Outreach Agent'ом, отправка через Telegram DM вместо/вместе с Email.

### 3.2 Архитектура

```
Outreach Agent (генерирует сообщение)
    │
    ▼
[HITL] — проверка каждого сообщения (MVP)
    │ approve / edit / reject
    ▼
TelegramDMSender (новый компонент)
    │ Telethon user API
    │ Rate limit: 5-10 DM/час (безопасный режим)
    ▼
Delivery tracking (sent/delivered/read/replied)
    │
    ▼
Touch Sequence Manager (День 1 → День 3 → Стоп)
```

### 3.3 TelegramDMSender — интерфейс

```python
class TelegramDMSender:
    """Sends approved DMs via Telegram user API (Telethon)."""

    async def send_dm(
        self,
        username: str,         # TG username получателя
        message: str,          # Текст (уже одобрен HITL)
        lead_id: str,          # ID лида для трекинга
    ) -> DMResult:
        """Send a single DM. Returns delivery status."""

    async def check_reply(self, username: str) -> Reply | None:
        """Check if target replied to our DM."""
```

### 3.4 Rate Limits и безопасность

| Параметр | Значение | Обоснование |
|----------|----------|-------------|
| DM/час | 5-10 | >20 DM/мин = бан |
| DM/день | 30-50 | Безопасный лимит для нового аккаунта |
| Stagger | 3-7 мин между DM | Имитация человека |
| Warmup период | 2-3 недели | Постепенное наращивание (см. раздел 4) |
| Unique messages | 100% | Никаких копипаст, каждое сообщение уникально |
| HITL | Каждое сообщение (MVP) | Позже: пакетное одобрение шаблонов |

### 3.5 Каскад каналов (приоритет)

Из `docs/plans/2026-02-24-personalized-outreach-design.md`:

```
1. Telegram DM (самый быстрый отклик для RU-бизнеса)
2. WhatsApp (если есть номер, фаза 2)
3. Email (если есть email, всегда как backup)
```

---

## 4. Безопасность и Anti-Ban

### 4.1 Warmup MAS-аккаунта

**Неделя 1-2: Органическая активность**
- Подписаться на 20-30 каналов по интересам
- Читать, лайкать, иногда комментировать
- Вступить в 5-10 групп, участвовать в обсуждениях
- НЕ отправлять DM незнакомым

**Неделя 2-3: Мягкий старт DM**
- 2-3 DM/день знакомым или в ответ на посты
- Постепенно наращивать до 5-10 DM/день
- Мониторить FloodWait ответы

**Неделя 3+: Рабочий режим**
- 5-10 DM/час, 30-50 DM/день
- Автоматический мониторинг каналов включён
- HITL на каждый DM

### 4.2 FloodWait Handling

```python
try:
    await client.send_message(username, text)
except FloodWaitError as e:
    log.warning("flood_wait", seconds=e.seconds)
    await asyncio.sleep(e.seconds + random.uniform(10, 30))
    # НЕ ретраить сразу — поставить в очередь на потом
```

### 4.3 Признаки скорого бана

| Сигнал | Действие |
|--------|----------|
| FloodWait > 300с | Стоп на 24ч, алерт в бот |
| 3+ FloodWait за час | Снизить rate до 2 DM/час |
| UserPrivacyRestricted | Пропустить, не ретраить |
| PeerFlood | Полный стоп, алерт, ждать 48ч |
| Account restricted | Стоп, ручное восстановление |

### 4.4 Общие правила

- **НИКОГДА** не отправлять одинаковые сообщения — каждый DM уникален
- **НИКОГДА** не спамить — один лид = макс 2 касания (День 1 + День 3), потом стоп
- **ВСЕГДА** HITL на каждое сообщение в MVP
- Случайные задержки между DM (не фиксированные интервалы)
- Чередовать чтение каналов с DM (имитация человека)

---

## 5. ENV-переменные

```bash
# Обязательные для мониторинга каналов
TELEGRAM_API_ID=34706111              # ✅ в .env
TELEGRAM_API_HASH=a158b8aa...         # ✅ в .env
TELEGRAM_SESSION_STRING=              # ⚠️ нужно сгенерировать

# Обязательные для работы listener'а
OPENROUTER_API_KEY=sk-or-v1-...      # ✅ в .env
DATABASE_URL=postgresql://...         # ✅ в .env
VALKEY_URL=valkey://localhost:6379    # ✅ в .env

# Будущие (DM outreach)
TELEGRAM_DM_RATE_PER_HOUR=10         # Лимит DM/час
TELEGRAM_DM_STAGGER_MIN=180          # Мин задержка между DM (сек)
TELEGRAM_DM_STAGGER_MAX=420          # Макс задержка между DM (сек)
```

---

## 6. Фазы реализации

### Фаза 1: Мониторинг (текущая — code done, нужен session string)

- [x] TelegramListener (Telethon MTProto)
- [x] TelegramParser (LLM-парсинг)
- [x] TelegramChannelAdapter (Valkey → Scout)
- [x] CRUD API каналов
- [x] Seed 22 каналов в БД
- [x] Credentials в .env (API_ID, API_HASH)
- [ ] Сгенерировать session string (интерактивно)
- [ ] Запустить и протестировать end-to-end

### Фаза 2: Надёжность (1-2 дня)

- [ ] Дедупликация (хэш в Valkey SET)
- [ ] Heartbeat monitoring для listener
- [ ] Алерт в Telegram бот при падении
- [ ] Keyword pre-filter перед LLM

### Фаза 3: DM Outreach MVP (3-5 дней)

- [ ] TelegramDMSender (Telethon user API)
- [ ] FloodWait handling + rate limiting
- [ ] HITL-интеграция (каждый DM через бот)
- [ ] Delivery tracking (sent/read/replied)
- [ ] Warmup протокол (ручной, 2-3 недели)

### Фаза 4: Touch Sequence (2-3 дня)

- [ ] Touch Sequence Manager (День 1 → День 3 → Стоп)
- [ ] Автоматический follow-up
- [ ] CRM-интеграция (статус лида)
- [ ] Пакетное одобрение шаблонов (HITL batch mode)

### Фаза 5: Масштабирование

- [ ] EN-каналы для международного рынка
- [ ] Интеграция с WhatsApp (фаза 2 outreach)
- [ ] A/B тестирование сообщений
- [ ] Аналитика конверсии по каналам

---

## 7. Dual Pipeline Design (2026-02-25)

Текущий TelegramListener слушает только realtime-сообщения и ищет только фриланс-заказы. Dual Pipeline расширяет систему в двух направлениях: (1) backfill + поиск заказов, (2) анализ людей в бизнес-чатах для персонального outreach.

### 7.1 Pipeline 1: Охота за заказами (freelance-каналы)

**Каналы:** 8 шт (freelead, freelancetaverna, freelansim_ru, kwork_market, frilans, FRILANSb, frilanse, digitaltender)

**Принцип:** один пост = один потенциальный заказ (линейная обработка).

#### Поток

```
Сообщение в канале
    │
    ▼
LLM: "Это заказ?" ──── Нет → пропуск (новости, мемы, реклама курсов)
    │ Да
    ▼
Извлечение: title, budget, skills, deadline
    │
    ▼
Дедупликация (хэш title+description в Valkey SET)
    │ уже видели → пропуск
    ▼
Scout: скоринг (наш стек? бюджет адекватный? сроки реальные?)
    │ score ≥ 0.5
    ▼
Bid Agent: генерация предложения
    │
    ▼
[HITL] — проверка, правка, отправка
```

#### Backfill (новое)

При первом подключении к каналу (`last_backfilled_at IS NULL`) — загрузка 30 дней истории через `client.iter_messages()`. Ожидаемый выход: 50-200 потенциальных заказов сразу, вместо ожидания.

| Параметр | Значение | Обоснование |
|----------|----------|-------------|
| `_BACKFILL_BATCH_SIZE` | 100 | Сообщений за один запрос |
| `_BACKFILL_DELAY_BETWEEN_BATCHES` | 2.0с | Telegram rate limit safety |
| `_BACKFILL_MAX_MESSAGES` | 3000 | Лимит на канал |
| `_BACKFILL_DAYS` | 30 | Глубина загрузки |

**Обработка:** последовательно по каналам (не параллельно) — избежание rate-limit.

**Идемпотентность:**
- `backfill_status = "in_progress"` при старте, `"completed"` при завершении
- При рестарте: каналы с `"in_progress"` обрабатываются повторно
- Прогресс логируется каждые 100 сообщений

#### LLM-классификация (существующий `parse_telegram_post`)

- **Модель:** DeepSeek V3.2 via OpenRouter
- **Фильтр:** сообщения < 50 символов отбрасываются
- **Извлекает:** `is_job`, `title` (max 200 chars), `description`, `budget_min`, `budget_max`, `currency` (default RUB), `skills[]`, `deadline` (ISO date)
- **Не заказы:** новости, обсуждения, реклама курсов, мемы, промо каналов

#### Дедупликация

Хэш `title+description` → Valkey SET. Один заказ в 3 каналах = одна запись.

---

### 7.2 Pipeline 2: Охота за людьми (business/niche-каналы)

**Каналы:** 12 шт (10 business + 2 niche)

**Принципиальное отличие:** НЕ "один пост = один заказ", а "много постов от одного человека = одна картинка".

#### Пример

```
Иван (username: ivan_restoran) пишет в @resto_business:
  Дек 28: "Кто-нибудь пробовал электронное меню? QR-код на столах"
  Янв 5:  "Доставка у нас через Яндекс, но комиссия 35% убивает"
  Янв 12: "Подскажите нормальный конструктор сайтов для ресторана"
  Фев 3:  "Мы в Воронеже, 2 точки, думаем расширяться"
```

Один пост ничего не говорит. Четыре поста = полная картинка:
- Ресторатор, Воронеж, 2 точки, растущий бизнес
- Боли: высокая комиссия доставки, нет сайта, хочет электронное меню
- Предложение: сайт с онлайн-заказами (убирает комиссию агрегаторов) + QR-меню

#### Шаг 1: Сбор сообщений

Каждое сообщение из business/niche чата сохраняется в Valkey:

```python
{
    "telegram_user_id": sender.id,
    "username": sender.username,
    "display_name": sender.first_name,
    "channel": channel_name,
    "text": text[:2000],
    "message_id": msg.id,
    "timestamp": iso
}
```

**Важно:** broadcast-каналы не имеют sender — только группы/чаты (`event.is_group`). Проверять перед сбором.

#### Шаг 2: Агрегация профиля (≥3 сообщений)

Когда у пользователя накопилось ≥3 сообщений (`_ANALYSIS_THRESHOLD = 3`), LLM анализирует профиль.

**LLM output:**
```json
{
  "is_potential_client": true,
  "client_score": 0.85,
  "summary": "Ресторатор, Воронеж, 2 точки, растёт. Нет сайта, зависит от агрегаторов",
  "detected_needs": ["website_with_ordering", "qr_menu", "delivery_integration"],
  "business_category": "restaurant",
  "reasoning": "Прямо спрашивает про конструктор сайтов + жалуется на комиссию доставки"
}
```

- **Порог промоции в Lead:** `score >= 0.6` (`_CLIENT_SCORE_THRESHOLD = 0.6`)
- **Модель:** DeepSeek V3.2 (самая дешёвая, ~$0.001 на профиль)

#### Шаг 3: Intent Detection — что ищем

- Владельцы бизнеса обсуждают операции
- Вопросы про сайты, приложения, digital presence
- Предприниматели, малый бизнес
- Индустрии, которым типично нужны сайты (рестораны, магазины, услуги)

#### Шаг 4: Anti-Signals — кого НЕ ловим

- **Другие разработчики/фрилансеры** — конкуренты, не клиенты
- **Уже нашли подрядчика** — "спасибо, уже работаем с..."
- **Боты и спамеры**
- **Люди без бизнеса** — просто общаются

#### Шаг 5: Персонализированный outreach

Ключевое отличие от холодного спама — контекст:

```
ПЛОХО (generic):
"Здравствуйте! Мы делаем сайты. Хотите сайт?"

ХОРОШО (персонализированно):
"Иван, привет! Видел ваш вопрос про электронное меню в чате рестораторов.
У нас есть решение для ресторанов — сайт с QR-меню и встроенным заказом,
без комиссии агрегаторов. Для Воронежа делали похожий проект.
Могу показать, если интересно?"
```

Это не спам — это **адресный ответ на реальную потребность**.

---

### 7.3 Таблица маппинга: боли → услуги

| Сигнал в сообщениях | Боль | Что предлагаем |
|---------------------|------|----------------|
| "нужен сайт", "конструктор сайтов" | Нет online-присутствия | Landing / сайт-визитка |
| "комиссия Яндекса/Delivery Club" | Зависимость от агрегаторов | Свой сайт с онлайн-заказами |
| "всё в Excel/тетрадке" | Нет автоматизации | CRM / система учёта |
| "клиенты не могут найти" | Нет SEO/рекламы | SEO + контекстная реклама |
| "сайт тормозит/выглядит старо" | Устаревший сайт | Редизайн |
| "хочу приложение" | Мобильный канал | Mobile app / PWA |
| "как принимать оплату онлайн" | Нет эквайринга | Интеграция платёжной системы |
| "QR-код меню" | Ресторанная цифровизация | QR-меню + digital-решение |

---

### 7.4 Архитектура

#### Полная диаграмма

```
TG каналы (22 шт)
    │
    ├─ category="freelance" (8 шт)
    │   ├─ [backfill] iter_messages(30 дней) → parse_telegram_post → Valkey jobs queue
    │   └─ [realtime] events.NewMessage → parse_telegram_post → Valkey jobs queue → Scout
    │
    └─ category="business"/"niche" (12 шт)
        ├─ [backfill] iter_messages(30 дней) → collect sender messages
        └─ [realtime] events.NewMessage → collect sender messages
                                              │
                                              ▼
                                    Valkey (mas:tg_profiles:pending)
                                              │
                                              ▼
                                    ProfileAggregator (каждые 5 мин)
                                      │ upsert TelegramUserProfile
                                      │ ≥3 сообщений → LLM анализ
                                      ▼
                                    "Этот человек — потенциальный клиент?"
                                      │ score ≥ 0.6 → promote to Lead
                                      ▼
                                    Lead (source="telegram") → Pipeline B → Outreach → [HITL]
```

#### Модели данных

**Расширение `TelegramChannel` (3 новых поля):**

| Поле | Тип | Описание |
|------|-----|----------|
| `last_backfilled_at` | DateTime(tz=True), nullable | Когда последний раз делали backfill |
| `backfill_status` | String(20) | null \| in_progress \| completed \| failed |
| `backfill_message_count` | Integer, default 0 | Сколько сообщений загружено |

**Расширение `Lead` (2 новых поля):**

| Поле | Тип | Описание |
|------|-----|----------|
| `source` | String(30) | "geoscout" \| "telegram" \| "manual" |
| `source_id` | String(255), UNIQUE | "tg_{user_id}" для дедупликации |

**Новая таблица `telegram_user_profiles`:**

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | |
| `telegram_user_id` | BigInteger, UNIQUE | Ключ дедупликации |
| `username` | String(255), nullable | TG username |
| `display_name` | String(500), nullable | Имя в TG |
| `message_count` | Integer, default 0 | Всего сообщений |
| `channels_seen` | ARRAY(Text) | ["biznes_chat", "smp37"] |
| `first_seen_at` | DateTime | Первое сообщение |
| `last_seen_at` | DateTime | Последнее сообщение |
| `recent_messages` | JSONB | Последние 20 сообщений [{text, channel, ts}] |
| `is_potential_client` | Boolean, nullable | Результат анализа |
| `client_score` | Numeric(3,2) | 0.00-1.00 |
| `analysis_summary` | Text, nullable | Краткое описание |
| `detected_needs` | ARRAY(Text) | ["website", "crm", "mobile_app"] |
| `business_category` | String(100), nullable | Категория бизнеса |
| `lead_id` | UUID FK(leads.id), nullable | Связь с Lead после промоции |
| `status` | String(20) | collecting \| analyzing \| promoted \| discarded |
| `analyzed_at` | DateTime, nullable | Когда анализировали |

**Индексы:** `idx_tg_profiles_status` на status, partial index на `client_score` where `is_potential_client = true`.

#### Valkey-очереди

| Очередь | Назначение |
|---------|------------|
| `mas:tg_jobs:pending` | Существующая — заказы из freelance-каналов (Pipeline 1) |
| `mas:tg_profiles:pending` | **Новая** — сырые сообщения из business-каналов для агрегации (Pipeline 2) |

#### Scheduler

| Интервал | Задача | Описание |
|----------|--------|----------|
| Каждые 5 мин | `process_pending` | RPOP из Valkey, upsert TelegramUserProfile |
| Каждые 30 мин | `analyze_profiles` | LLM-анализ профилей с ≥3 сообщениями |

#### Новый сервис: TelegramProfileAggregator

**Файл:** `src/services/telegram_profile_aggregator.py`

**Константы:**
- `_ANALYSIS_THRESHOLD = 3`
- `_CLIENT_SCORE_THRESHOLD = 0.6`
- `_BATCH_SIZE = 50`
- `_VALKEY_QUEUE = "mas:tg_profiles:pending"`

**Методы:**
1. `process_pending()` — RPOP из Valkey, группировка по user_id, upsert профилей (message_count++, append recent_messages capped at 20, update channels_seen)
2. `analyze_profiles()` — SELECT where status='collecting' AND message_count >= 3 AND analyzed_at IS NULL, запуск LLM, обновление полей
3. `_promote_to_lead(profile)` — создание Lead(source="telegram", source_id=f"tg_{user_id}"), связь profile.lead_id, status="promoted"

#### Дедупликация

| Уровень | Стратегия |
|---------|-----------|
| Заказы между каналами | Хэш title+description → Valkey SET |
| Профили между каналами | `telegram_user_id` UNIQUE — один человек в нескольких чатах = один профиль |
| Lead | `source_id = "tg_{user_id}"` UNIQUE — один Lead на человека |
| Миграция существующих | `UPDATE leads SET source = 'geoscout' WHERE osm_id IS NOT NULL;` |

#### LLM-промпт для детекции клиентов

```
You analyze Telegram chat messages from a user to determine if they represent
a business or person who might need web development services.

Context: We are a web development agency. We look for:
- Business owners discussing their business operations
- People asking about websites, apps, or digital presence
- Entrepreneurs or small business owners
- People in industries that typically need websites (restaurants, shops, services)

Messages from this user (from various business/niche Telegram channels):

{messages}

Analyze these messages and respond with a JSON object:
{
    "is_potential_client": true/false,
    "client_score": 0.0 to 1.0,
    "summary": "Brief summary of who this person/business appears to be",
    "detected_needs": ["need1", "need2"] or [],
    "business_category": "restaurant" | "retail" | "services" | "other" | null,
    "reasoning": "Why you think they are/aren't a potential client"
}
```

---

### 7.5 Порядок реализации (7 шагов)

| # | Что | Файлы | Оценка |
|---|-----|-------|--------|
| 1 | Migration: схема БД (extend TelegramChannel, extend Lead, create telegram_user_profiles) | `src/core/models.py`, Alembic migration | 1ч |
| 2 | Backfill в TelegramListener (iter_messages 30 дней, batch, rate limiting) | `src/services/telegram_listener.py` | 2ч |
| 3 | Dual routing: split `_on_new_message` по категории канала (freelance vs business/niche) | `src/services/telegram_listener.py` | 1ч |
| 4 | ProfileAggregator + LLM-промпт анализа | **NEW** `src/services/telegram_profile_aggregator.py`, `src/services/telegram_parser.py` | 3ч |
| 5 | Lead promotion + подключение к Pipeline B (extend `_load_leads()` с source filter) | `src/services/telegram_profile_aggregator.py`, `src/agents/outreach.py` | 2ч |
| 6 | API: schemas с backfill-полями, endpoint ручного запуска backfill, enrich-telegram-leads | `src/api/routes/telegram_channels.py`, `src/api/routes/pipeline_b.py` | 1ч |
| 7 | Scheduler tasks (5-мин агрегация, 30-мин анализ) | `src/worker/scheduler.py` | 0.5ч |
| (8) | Тесты | `tests/unit/test_telegram_backfill.py`, `tests/unit/test_telegram_profile_aggregator.py` | 3ч |

**MVP (шаги 1-3):** backfill + routing, ~4ч
**Полная фича (шаги 1-7):** всё кроме тестов, ~10.5ч
**С тестами:** ~13.5ч

### 7.6 Известные сложности

1. **Rate limits при backfill** — 3000 сообщений по 22 каналам последовательно = 10-15 мин. Telethon сам обрабатывает FloodWaitError, но консервативный sleep между батчами обязателен.
2. **Business-каналы = группы, не каналы** — `events.NewMessage` в группах включает sender. В broadcast-каналах sender'а нет. Проверять `event.is_group` перед сбором профилей.
3. **Объём сообщений** — бизнес-чаты могут быть очень активны (сотни/день). ProfileAggregator должен batch'ить эффективно, `recent_messages` capped at 20.
4. **LLM cost** — ~$0.001 на профиль (DeepSeek V3.2). Batch-анализ + threshold-gating держат расходы низкими.
5. **Privacy** — сбор из публичных/полу-публичных групп. Данные = business intelligence, не персональные данные.

---

## Ссылки

- [Personalized Outreach Design](./2026-02-24-personalized-outreach-design.md)
- [Client Growth & Upselling Vision](./2026-02-24-client-growth-upselling-vision.md)
- [Email Warmup](../email_warmup.md)
- [Telegram Bot (HITL)](../telegram_bot.md)
