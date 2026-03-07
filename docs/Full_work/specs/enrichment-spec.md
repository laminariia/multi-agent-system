# Enrichment и доставка: Спецификация

## Назначение

Подсистема обогащения данных о лидах (поиск email, телефон, соцсети ЛПР) и доставки outreach-сообщений через email и Telegram DM. Используется в Pipeline B после GeoScout и Outreach агентов.

## Enrichment Waterfall

### Принцип работы

Каскадный перебор источников от дешёвых к дорогим. Останавливается при первом найденном email.

```
OSINT (бесплатно) -> Hunter.io ($0.01/запрос) -> Apollo.io ($0.05/запрос)
```

### Класс `EnrichmentWaterfall`

- **Конструктор**: принимает `hunter_api_key` и `apollo_api_key` (оба опциональные)
- **`enrich(business_name, city, domain=None)`**: последовательно пробует все источники
- **`total_cost`**: свойство, накапливает стоимость за сессию
- **`close()`**: закрывает HTTP-клиенты всех enricher'ов

### EnrichmentResult (dataclass)

```python
@dataclass
class EnrichmentResult:
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    source: str = ""           # "osint_duckduckgo", "hunter", "apollo", "none"
    confidence: float = 0.0    # 0.0 - 1.0
    raw_data: dict | None = None
```

## OSINT модуль (`OsintEnricher`)

- **Источник**: DuckDuckGo HTML search (бесплатно, без API-ключа)
- **Запрос**: `"{business_name} {city} email contact"`
- **Парсинг**: regex для email (`[\w.+-]+@[\w-]+\.[\w.-]+`) и телефонов (`+?\d[\d\s\-()]{8,}\d`)
- **Фильтрация**: исключает .png/.jpg/.gif/.svg, example.com, duckduckgo
- **Confidence**: 0.4 (только email), 0.5 (email + phone)
- **HTTP-клиент**: `httpx.AsyncClient`, timeout=15s, User-Agent спуфинг (Windows Chrome)
- **Стоимость**: $0.00

## Hunter.io (`HunterEnricher`)

- **API**: `GET https://api.hunter.io/v2/domain-search?domain=...&api_key=...`
- **Вход**: домен компании (например, `acme.com`)
- **Логика**: из списка найденных email выбирает с максимальным confidence
- **Confidence**: Hunter возвращает 0-100, нормализуется в 0.0-1.0
- **Ограничения**: не предоставляет телефоны, только email
- **Стоимость**: ~$0.01/запрос
- **Пропускается**: если `hunter_api_key` не задан или `domain` не передан
- **Error handling**: HTTPStatusError и HTTPError -> возврат пустого результата (graceful degradation)

## Apollo.io (`ApolloEnricher`)

- **API**: `POST https://api.apollo.io/v1/mixed_people/search`
- **Вход**: `q_organization_name` (бизнес), `person_locations[]` (город, опционально)
- **Логика**: ищет людей в компании, предпочитает тех у кого есть email, приоритет owners/managers
- **Данные**: email + phone (из `phone_numbers[0].raw_number`) + имя, должность, организация
- **Confidence**: фиксированный 0.75 (высокое качество данных)
- **Стоимость**: ~$0.05/запрос
- **Пропускается**: если `apollo_api_key` не задан

## Email Sender (`EmailSender`)

### Конфигурация

- SMTP: host, port (default 587), user, password, from address -- из `Settings` или override при создании
- `MAX_EMAILS_PER_DAY`: default 50
- `EMAIL_HTML_ENABLED`: default True
- `EMAIL_STAGGER_MIN_SECONDS` / `EMAIL_STAGGER_MAX_SECONDS`: default 30/60

### Отправка одного письма

`send_email(to, subject, body, from_addr=None) -> bool | str`

- **Проверки**: SMTP_HOST настроен, from address есть, recipient указан
- **Rate limit**: модуль-level счётчик `_daily_count`, сбрасывается ежедневно
- **Протокол**: aiosmtplib + STARTTLS
- **MIME**: multipart/alternative (plain text + HTML)
- **HTML**: автоматическая конвертация plain text в `<p>` теги с `<br>` для переносов
- **Unsubscribe**: `List-Unsubscribe` и `List-Unsubscribe-Post` заголовки (RFC 8058), footer в обоих вариантах

### Bounce handling

- **Hard bounce** (550, 551, 552, 553): возвращает `"hard_bounce"`, CampaignLead.status -> `"bounced"`, Lead.status -> `"no_contact"`
- **Soft bounce** (421, 450, 451, 452): возвращает `"soft_bounce"`, CampaignLead.status -> `"failed"` (для retry)
- **Generic failure**: возвращает `False`, CampaignLead.status -> `"failed"`

### Массовая отправка

`send_approved_emails(campaign_id, db_session) -> { sent, failed, rate_limited, bounced }`

- Выбирает `CampaignLead` со `status='approved'`
- Staggered delivery: `random.uniform(stagger_min, stagger_max)` между письмами
- Обновляет `CampaignLead.sent_at` и `EmailCampaign.sent_count` / `bounce_count`
- Progress logging: `email_stagger_progress` с `sent="{idx}/{total}"`

### Warm-up стратегия (6 недель)

(не реализовано в коде)

#### Предварительные требования (до начала warm-up)

| Требование | Описание | Проверка |
|------------|----------|----------|
| SPF | TXT-запись `v=spf1 include:... -all` в DNS домена | `dig TXT domain.com` |
| DKIM | Подпись RSA 2048-bit через SMTP-провайдера | Заголовок `DKIM-Signature` в отправленном письме |
| DMARC | `v=DMARC1; p=quarantine; rua=mailto:...` | `dig TXT _dmarc.domain.com` |
| rDNS (PTR) | Reverse DNS совпадает с HELO/EHLO | `dig -x <IP>` |
| Dedicated IP | Отдельный IP для cold outreach (не shared) | Провайдер SMTP |
| Домен | Отдельный домен или субдомен для outreach (не основной) | Защита репутации основного домена |

#### Фазы прогрева

| Неделя | Объём/день | Получатели | Контент | Цель |
|--------|-----------|------------|---------|------|
| 1 | 5 писем | Личные контакты, коллеги | Реальная переписка, reply-worthy | Установить базовую репутацию |
| 2 | 10 писем | Личные + seed list (собственные ящики) | Переписка + тестовые цепочки с ответами | Получить reply rate > 30% |
| 3 | 15 писем | Mix: 70% personal, 30% warm leads | Персонализированные, короткие | Отслеживать inbox placement |
| 4 | 25 писем | Mix: 50% personal, 50% cold (verified) | A/B тесты subject lines | Bounce rate < 3% |
| 5 | 35 писем | Mix: 30% personal, 70% cold | Отточенные шаблоны outreach | Spam complaint rate < 0.1% |
| 6 | 50 писем | 100% cold outreach (verified emails) | Финальные шаблоны Pipeline B | Готовность к production |

#### Метрики здоровья домена

| Метрика | Целевое значение | Красная линия (stop & review) |
|---------|-----------------|-------------------------------|
| Bounce rate | < 3% | > 5% |
| Spam complaint rate | < 0.1% | > 0.3% |
| Reply rate | > 10% | < 3% (признак спам-фолдера) |
| Open rate | > 40% | < 15% |
| Inbox placement | > 90% | < 70% |
| Unsubscribe rate | < 1% | > 2% |

#### Domain Reputation мониторинг

Инструменты проверки (ручные, бесплатные):
- **Google Postmaster Tools** -- репутация домена/IP для Gmail
- **Microsoft SNDS** -- статус для Outlook/Hotmail
- **MXToolbox** -- blacklist check, SPF/DKIM/DMARC validator
- **mail-tester.com** -- спам-скор конкретного письма (10/10 = идеально)

Действия при деградации репутации:
1. Немедленно снизить объём до уровня предыдущей недели
2. Увеличить долю personal/reply-generating писем до 70%
3. Проверить контент на spam-триггеры (ALL CAPS, excessive links, "free", "urgent")
4. Подождать 3-5 дней, перепроверить метрики
5. Возобновить наращивание темпа только после восстановления показателей

#### Stagger и timing

- **Время отправки**: 9:00-17:00 по timezone получателя (рабочие часы)
- **Дни**: Вторник-Четверг оптимально, избегать понедельника и пятницы
- **Интервал между письмами**: `EMAIL_STAGGER_MIN_SECONDS` / `EMAIL_STAGGER_MAX_SECONDS` (30-60s в коде)
- **Рандомизация**: `random.uniform(stagger_min, stagger_max)` -- имитация человеческого поведения
- **Не отправлять**: в выходные, праздники, ночное время

#### Интеграция с EmailSender

Warm-up использует тот же `EmailSender`, но с жёстким контролем `MAX_EMAILS_PER_DAY`:

| Неделя | `MAX_EMAILS_PER_DAY` | Комментарий |
|--------|---------------------|-------------|
| 1 | 5 | Ручной override через Settings |
| 2 | 10 | |
| 3 | 15 | |
| 4 | 25 | |
| 5 | 35 | |
| 6 | 50 | Production-значение (default) |

После warm-up: наращивание до 100+/день возможно при стабильных метриках, но не ранее 8-й недели.

## Telegram DM Sender (`TelegramDMSender`)

### Конфигурация

- Telethon: `api_id`, `api_hash`, `session_string` (StringSession) -- из `Settings` или override
- `max_dms_per_hour`: default 5
- `min_interval_seconds`: default 720 (12 минут между сообщениями)

### Подключение

`connect()` -> создаёт `TelegramClient(StringSession(...), api_id, api_hash)` и подключается.

### Отправка DM

`send_dm(user_identifier, message_text) -> bool | str`

- **Rate limit**: модуль-level `_hourly_count`, сбрасывается каждый час (monotonic clock)
- **Minimum interval**: если с последней отправки прошло < `min_interval_seconds`, ждёт разницу
- **Результаты**:
  - `True` -- успешно отправлено
  - `"flood_wait"` -- Telegram rate limit (FloodWaitError). Ожидает `e.seconds`, повторяет один раз
  - `"privacy_restricted"` -- пользователь запретил DM (UserPrivacyRestrictedError)
  - `False` -- прочие ошибки

### Anti-ban стратегия

| Параметр | Значение | Обоснование |
|----------|----------|-------------|
| max_dms_per_hour | 5 | Telegram бан-порог ~20-30/час |
| min_interval_seconds | 720 (12 мин) | Имитация человеческого поведения |
| FloodWaitError >300s | (не реализовано, рекомендация: 24h stop) | Telegram даёт всё более долгие flood wait |
| PeerFlood | (не реализовано, рекомендация: 48h stop + alert) | Серьёзный сигнал, аккаунт под угрозой |

### Массовая отправка

`send_approved_telegram_dms(campaign_id, db_session) -> { sent, failed, privacy_restricted, rate_limited }`

- Выбирает `CampaignLead` со `status='approved'` И `channel_type='telegram'`
- При `flood_wait` -- немедленно прекращает отправку (`break`) для защиты аккаунта
- Обновляет `CampaignLead.sent_at` при успехе

### Прогрев аккаунта (2-3 недели)

(не реализовано автоматически, ручной процесс)

- Неделя 1: органическая активность (чтение каналов, реакции)
- Неделя 2: ответы в групповых чатах
- Неделя 3: осторожные DM знакомым

## WhatsApp (не реализовано)

- Планируется: WhatsApp Business API
- Стоимость: ~$0.05/сообщение
- Template messages для первого контакта
- Описано в outreach-design: `docs/Full_work/archive/personalized-outreach-design.md`

## Ключевые файлы

- `src/enrichment/waterfall.py` -- EnrichmentWaterfall (каскад)
- `src/enrichment/osint.py` -- OsintEnricher + EnrichmentResult dataclass
- `src/enrichment/hunter.py` -- HunterEnricher (domain-search)
- `src/enrichment/apollo.py` -- ApolloEnricher (people search)
- `src/enrichment/email_sender.py` -- EmailSender + send_approved_emails()
- `src/enrichment/telegram_sender.py` -- TelegramDMSender + send_approved_telegram_dms()
- `src/core/config.py` -- Settings (SMTP_*, TELEGRAM_*, EMAIL_STAGGER_*)
- `src/core/models.py` -- Lead, EmailCampaign, CampaignLead (channel_type, personalized_body)
