# Outreach и SalesAgent: Спецификация

## Outreach Agent (текущий)

**Файлы:** `src/agents/outreach.py`, `src/prompts/outreach.py`
**LLM:** DeepSeek V3.2
**Температура:** 0.7
**Класс:** `OutreachAgent(ConstrainedAgent)`, agent_name=`"outreach"`

### Назначение

Обогащение лидов контактными данными (enrichment waterfall) и генерация персонализированных сообщений через LLM. Поддерживает email и Telegram каналы. Никогда не отправляет автоматически -- только черновики для HITL approval.

### Allowed Tools

`enrich_lead`, `generate_email`, `queue_email`

### Flow

1. Определение города из `state["artifacts"]["_geo_scan_results"]["city"]` или `state["project"]["requirements"]`
2. `_load_leads(city)` -- SELECT `leads WHERE city=X AND status='new' LIMIT 50`
3. `_enrich_leads(leads, waterfall)` -- enrichment waterfall для каждого лида
4. `_create_campaign(city)` -- EmailCampaign с `status='draft'`
5. `_generate_messages(leads, campaign)` -- multi-channel: выбор канала + LLM-генерация
6. `_create_hitl_request(campaign, city, count, channel_counts)` -- HITLQueue `type='outreach_approval'`
7. State: `requires_hitl=True`, `status="paused"`

### Channel selection

```python
def _select_channel(lead: Lead) -> ChannelType:
    if lead.telegram_username:
        return "telegram"
    return "email"
```

Приоритет: Telegram > Email (выше open rate для локального бизнеса в РФ).

### Генерация сообщений

`_draft_message_for_lead(lead, channel)`:
- Контекст для LLM: business name, category, city, address, telegram username
- System prompt: `get_outreach_prompt(channel)` -- channel-specific промпт
- Возврат: `(subject, body)` для email, `(None, body)` для telegram
- **Fallback** при ошибке LLM:
  - Telegram: "Привет! Заметил, что у {name} нет сайта..."
  - Email: "Hi! I noticed {name} doesn't have a website yet..."

### Тон: "сосед, не продавец"

Из промптов `src/prompts/outreach.py`:

**7 принципов:**
1. Персонализация -- упомянуть название бизнеса естественно
2. Конкретика -- ссылка на деталь (адрес, категория)
3. Ценность первой -- что ОНИ получат, не что мы делаем
4. Один CTA -- один низкобарьерный следующий шаг
5. Краткость -- до 120 слов (малый бизнес), до 180 (крупнее)
6. Естественный язык -- как реальный человек
7. Уважение -- если "нет", это ОК

**Анти-паттерны (запрещены):**
- "Dear Hiring Manager", "I hope this message finds you well"
- Buzzwords: "synergy", "leverage", "cutting-edge"
- AI-маркеры: буллет-поинты, чрезмерное форматирование
- Давление: "limited spots", "act now"

### Каналы: промпты

**Email** (`EMAIL_OUTREACH_PROMPT`):
- JSON: `{channel: "email", subject: "...", body: "...", language: "ru|en"}`
- Subject: до 60 символов, конкретный
- Body: полный текст письма

**Telegram** (`TELEGRAM_OUTREACH_PROMPT`):
- JSON: `{channel: "telegram", body: "...", language: "ru"}`
- 2-3 предложения, до 60 слов
- Без формальных приветствий, без ссылок в первом сообщении
- "ты" вместо "вы" (если бизнес не формальный)

### Enrichment Waterfall

**Файл:** `src/enrichment/waterfall.py`

Каскад от дешёвого к дорогому, стоп на первом найденном email:

| Источник | Стоимость | Метод |
|----------|----------|-------|
| OSINT (DuckDuckGo) | $0.00 | `OsintEnricher.enrich(name, city)` |
| Hunter.io | $0.01/запрос | `HunterEnricher.enrich(domain)` (нужен домен) |
| Apollo.io | $0.05/запрос | `ApolloEnricher.enrich(name, city)` |

API ключи: из БД Settings (через `_get_credential()`) с fallback на env vars (`HUNTER_API_KEY`, `APOLLO_API_KEY`).

После enrichment: лид обновляется в БД (`status='enriched'`, email, phone, enrichment_source, enrichment_cost).

### State выход

```python
artifacts["campaign_id"] = str(campaign.id)
artifacts["_outreach_hitl_id"] = str(hitl_id)
artifacts["_outreach_results"] = {
    "city": "...",
    "leads_processed": 50,
    "enriched": 30,
    "messages_drafted": 25,
    "emails_drafted": 25,  # backward compat
    "channel_counts": {"email": 20, "telegram": 5},
    "enrichment_cost": 1.50,
}
requires_hitl = True  # если messages_drafted > 0
status = "paused"
```

---

## SalesAgent (не реализован -- дизайн)

### Отличие от Bid Agent

| Bid Agent | SalesAgent |
|-----------|-----------|
| Генерирует proposals для платформ | Ведёт direct переговоры с клиентами |
| Одноразовое сообщение | Multi-turn conversation |
| Фриланс-платформы | Email / Telegram / WhatsApp |
| Формальный тон | "Сосед, не продавец" |

### Multi-turn conversation flow (дизайн)

1. **Initial outreach** -- первое персонализированное сообщение
2. **Follow-up** -- серия касаний по расписанию (Touch Sequence)
3. **Objection handling** -- работа с возражениями через LLM
4. **Concept proposal** -- генерация концепции решения
5. **Deal closing** -- финальное предложение с ценой

### Deal Memory (дизайн)

`client_context` -- персистентный контекст переговоров:
- История сообщений
- Выявленные потребности
- Возражения и ответы
- Стадия сделки
- Конкурентный анализ

### Tools (дизайн)

- `get_competitor_analysis(business_name, city)` -- анализ конкурентов
- `generate_concept(requirements)` -- генерация концепции
- `calculate_price(scope)` -- расчёт стоимости
- `schedule_call(datetime)` -- планирование звонка

### HITL точки (дизайн)

1. **Concept Review** -- ревью концепции перед отправкой клиенту
2. **Concept Approved** -- клиент утвердил, переход к выполнению

---

## Touch Sequence (не реализован -- дизайн)

### Расписание

| День | Канал | Действие |
|------|-------|---------|
| 1 | TG/Email | Первое сообщение |
| 3 | тот же | Follow-up если нет ответа |
| 5 | альтернативный | Попытка через другой канал |
| 10 | Email | Финальное касание |
| STOP | -- | Прекращение, отметка "not_interested" |

### Стоп-правила

- Ответ "не интересно" / "нет" -> немедленный STOP
- 4 касания без ответа -> STOP
- Bounce / privacy_restricted -> STOP, лид = "no_contact"
- Клиент ответил -> переход к SalesAgent

### Channel priority

Telegram > WhatsApp (не реализован) > Email.
Обоснование: Telegram имеет наивысший open rate для российского малого бизнеса.

---

## Каналы связи

| Канал | Реализован? | Лимиты | Инфраструктура |
|-------|-----------|--------|----------------|
| Email | Да | 50/день (configurable) | `EmailSender` + aiosmtplib + TLS |
| Telegram DM | Да | 5/час, 12 мин между DM | `TelegramDMSender` + Telethon (MTProto) |
| WhatsApp | Нет (дизайн) | -- | Планируется WhatsApp Business API |
| LinkedIn | Нет (дизайн) | -- | Планируется для международного outreach |

### Email Sender (`src/enrichment/email_sender.py`)

- **SMTP:** async через `aiosmtplib`, TLS (start_tls=True)
- **Rate limit:** `_check_and_increment_rate_limit(max_per_day)` -- дневной лимит, reset в полночь
- **HTML:** dual-format (plain + HTML), template `_HTML_WRAPPER`
- **Unsubscribe:** `List-Unsubscribe` + `List-Unsubscribe-Post` headers (RFC 8058)
- **Bounce detection:** hard bounce (550-553) -> lead `no_contact`, soft bounce (421/450-452) -> retry
- **Stagger:** случайная задержка 30-60 сек между отправками (`EMAIL_STAGGER_MIN/MAX_SECONDS`)
- **Bulk:** `send_approved_emails(campaign_id, db_session)` -- обрабатывает все approved CampaignLead

### Telegram DM Sender (`src/enrichment/telegram_sender.py`)

- **Протокол:** MTProto через Telethon (`TelegramClient` + `StringSession`)
- **Rate limit:** 5 DM/час (`max_dms_per_hour`), 720 сек (12 мин) между сообщениями (`min_interval_seconds`)
- **Ошибки:**
  - `FloodWaitError` -- ждать `e.seconds`, retry один раз
  - `UserPrivacyRestrictedError` -> `"privacy_restricted"`
  - Generic failure -> `False`
- **Bulk:** `send_approved_telegram_dms(campaign_id, db_session)` -- обрабатывает approved CampaignLead с `channel_type='telegram'`
- **Credentials:** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` из Settings/env

---

## Anti-ban стратегия

### Telegram

| Угроза | Защита |
|--------|--------|
| FloodWait | 5 DM/час max, 12 мин интервал |
| PeerFlood | Авто-стоп при `flood_wait` result |
| Spam Report | Персонализация через LLM, без ссылок в первом DM |
| Account Ban | Прогрев 2-3 недели органической активности перед DM outreach |
| Privacy Restricted | Пропуск, пометка лида как `no_contact` |

### Email

| Угроза | Защита |
|--------|--------|
| Spam фильтры | SPF/DKIM/DMARC (настройка домена) |
| Blacklist | 50 email/день max, стаггерная отправка 30-60 сек |
| Bounce | Hard bounce -> no_contact, soft bounce -> retry |
| Unsubscribe | RFC 8058 headers, footer с unsubscribe инструкцией |
| Warm-up | 6 недель прогрева перед production outreach (см. `docs/email_warmup.md`) |
| Reputation | Dual-format (plain + HTML), персонализация через LLM |

### Общие меры

- **HITL gate** -- ни одно сообщение не отправляется без одобрения человека
- **Нет массовой рассылки** -- каждое сообщение персонализировано через LLM
- **Градуальный scaling** -- начало с 5-10 сообщений/день, постепенное увеличение
- **Мониторинг** -- campaign stats (sent_count, bounce_count) обновляются в БД
