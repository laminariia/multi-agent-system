# Platform Adapters: Спецификация

## Архитектура

Каждый адаптер -- самостоятельный класс с единым интерфейсом `fetch_jobs()`. Базового абстрактного класса нет (duck typing), но все адаптеры возвращают нормализованные job-словари с ключами: `external_id`, `platform`, `title`, `description`, `budget_min`, `budget_max`, `currency`, `skills_required`, `url`, `raw_data`.

Scout Agent вызывает адаптеры через единый интерфейс, не зная деталей платформы. Каждый адаптер самостоятельно управляет rate limiting через `AdaptiveRateLimiter`.

## Freelancer.com (`FreelancerClient`)

### Тип подключения: REST API

- **Base URL**: `https://www.freelancer.com/api`
- **Auth**: OAuth2 (header `Freelancer-OAuth-V1: <token>`)
- **HTTP-клиент**: `httpx.AsyncClient` с lazy init и shared instance

### Методы

| Метод | Endpoint | Описание |
|-------|----------|----------|
| `fetch_jobs(category, min_budget, max_results=50)` | `GET /projects/0.1/projects/active/` | Поиск активных проектов. Params: `jobs[]`, `min_avg_price`, `limit` (max 100), `full_description`, `job_details`, `user_details` |
| `submit_bid(project_id, description, amount, period, milestone_percentage=100)` | `POST /projects/0.1/bids/` | Отправка bid (после HITL approve) |
| `get_messages(platform_bid_id)` | `GET /messages/0.1/threads/{id}/messages/` | Сообщения по bid-треду (non-critical, возвращает `[]` при ошибке) |
| `get_client_profile(user_id)` | `GET /users/0.1/users/{id}/` | Профиль клиента: rating, hire_rate, total_spent, country |

### Rate limits

- API: 100 req/hour
- Search: 30 req/min
- Bids: 10/hour
- Timeout: 30s per request

### Error handling

- 429 -> `PlatformRateLimitError` (с Retry-After)
- 403 -> `PlatformBannedError`
- 400+ -> `PlatformAPIError` (body[:500])
- JSON `{"status": "error"}` -> `PlatformAPIError`

### Нормализация

Budget из `proj.budget.minimum/maximum`, skills из `proj.jobs[].name`, client_info из `proj.owner` (user_id, username, rating, country).

## FL.ru (`FlRuClient`)

### Тип подключения: RSS Feed

- **URL**: `https://www.fl.ru/rss/all.xml` (или `{category}.xml`)
- **Парсинг**: `xml.etree.ElementTree` (RSS 2.0: `<rss><channel><item>`)
- **HTTP-клиент**: `httpx.AsyncClient`, timeout=20s, Chrome User-Agent

### Методы

| Метод | Описание |
|-------|----------|
| `fetch_jobs(category=None)` | Fetch + parse RSS feed. Category заменяет `all.xml` на `{category}.xml` |
| `parse_rss_entry(entry)` | Статический метод: `<item>` XML -> normalised job dict |

### Rate limits

- Внутренний: 10 запросов/час (sliding window, monotonic clock)
- `_request_timestamps[]` pruning при каждом вызове
- Превышение -> `PlatformRateLimitError`

### Бюджет

Regex из description/title: `Бюджет: 50000 руб`, `$500`, `от 3000 до 10000 руб`.
Конвертация в USD: RUB x0.011, EUR x1.08. Нативная валюта: RUB.

### External ID

Из ссылки `/projects/(\d+)` или из GUID.

## Kwork (`KworkClient`)

### Тип подключения: Playwright Stealth scraping

- **URL**: `https://kwork.ru/projects` (+ `?c={category}`)
- **Browser**: через `BrowserPool.acquire("kwork")`
- **Задержки**: увеличенные 2-7s (vs стандартные 1-5s) из-за агрессивной anti-bot защиты

### Методы

| Метод | Описание |
|-------|----------|
| `fetch_jobs(category=None, max_results=50)` | Загрузка страницы проектов, парсинг карточек |

### Парсинг

CSS-селекторы: `.card__content`, `.wants-card`, `.kwork-card`, `article.js-want-wrapper`.
Поля: title (`h3 a`), description (`.breakwords`), budget (`.card__price`), link (`.card__title a`).

### Block detection

- Captcha: `#challenge-running`, `.g-recaptcha` -> `CaptchaDetectedError`
- Cloudflare: `#challenge-stage` -> `CloudflareBlockError`
- Ban: `.blocked-user` -> `PlatformBannedError`

### Бюджет

Regex: `5 000 руб`, `от 3000 до 10000 руб`, `$500`, `5000₽`.
Конвертация в USD аналогично FL.ru.

## Upwork (`UpworkClient`)

### Тип подключения: Playwright Stealth scraping (READ-ONLY)

- **URL**: `https://www.upwork.com/nx/search/jobs`
- **Browser**: через `BrowserPool.acquire("upwork")`
- **ЗАПРЕТ**: `_SUBMIT_GUARD = True` -- auto-submit bid ЗАПРЕЩЁН (ToS violation). Метода `submit_bid` нет.

### Методы

| Метод | Описание |
|-------|----------|
| `fetch_jobs(category=None, max_results=50)` | Скрапинг search results page |
| `get_job_details(job_url)` | Детали конкретного job posting |

### Парсинг

Селекторы: `section[data-test="JobTile"]`, `a[data-test="job-tile-title-link"] h2`, `[data-test="budget"]`, `[data-test="token"]`.

### Block detection

- Captcha: `#challenge-running`, `#captcha-container`
- Cloudflare: `#challenge-stage`, `.cf-browser-verification`
- Login redirect: `form[action*="login"]` -> `CaptchaDetectedError` ("Session expired")
- Ban: `.account-suspended`, `.account-hold` -> `PlatformBannedError`

### Бюджет

Regex: `$100-$500`, `$250`. Валюта всегда USD.

## Telegram Channels (`TelegramChannelAdapter`)

### Тип подключения: Valkey Queue

Архитектура split-process: отдельный `TelegramListener` (Telethon) мониторит каналы, парсит посты LLM, пушит нормализованные job-словари в Valkey list `mas:tg_jobs:pending`.

Адаптер просто забирает (`RPOP`) элементы из очереди.

### Методы

| Метод | Описание |
|-------|----------|
| `fetch_jobs(max_results=50)` | RPOP до max_results элементов из Valkey queue |

### Каналы (22 шт.)

Хранятся в таблице `telegram_channels`:
- 8 freelance-каналов
- 10 business-каналов
- 2 niche-каналов
- Поля: username, title, category, active (тумблер)

## Fiverr

### Статус: зарегистрирована как валидная платформа (`VALID_PLATFORMS` в `src/api/routes/settings.py`), адаптер НЕ реализован

Fiverr — платформа с моделью "seller creates gig" (продавец публикует услугу, покупатель выбирает). Отличается от остальных платформ, где заказчик создаёт проект, а фрилансер подаёт bid.

### Тип подключения: Playwright Stealth scraping (планируется)

- **URL**: `https://www.fiverr.com/categories` (browse) / `https://www.fiverr.com/search/gigs` (search buyer requests)
- **API**: Fiverr не предоставляет публичный API для поиска buyer requests
- **Buyer Requests**: `https://www.fiverr.com/users/{username}/manage_requests` — раздел для фрилансеров с запросами покупателей
- **Browser**: через `BrowserPool.acquire("fiverr")`

### Планируемые методы

| Метод | Описание |
|-------|----------|
| `fetch_jobs(category=None, max_results=50)` | Скрапинг Buyer Requests page |
| `get_request_details(request_url)` | Детали конкретного buyer request |

### Особенности платформы

- **Модель:** gig-based (продавец создаёт услугу), но есть Buyer Requests (аналог проектов)
- **Комиссия:** 20% от суммы заказа
- **Валюта:** USD
- **Bid submission:** через UI Buyer Requests (HITL mandatory)
- **Anti-bot:** агрессивная защита (Cloudflare + device fingerprinting)

### Block detection (планируется)

- Cloudflare: `#challenge-stage`, `.cf-browser-verification`
- Captcha: reCAPTCHA, hCaptcha
- Login redirect: session expired

### Нормализация (планируется)

Budget из buyer request description, skills из тегов/категорий. Формат вывода: стандартный job-словарь (`external_id`, `platform="fiverr"`, `title`, `description`, `budget_min`, `budget_max`, `currency="USD"`, `skills_required`, `url`, `raw_data`).

## Browser Infrastructure

### Playwright Stealth (`src/browser/stealth.py`)

Три уровня абстракции:

**StealthBrowser** -- anti-detection Chromium launcher:
- Аргументы: `--disable-blink-features=AutomationControlled`, `--no-sandbox`, `--disable-dev-shm-usage`
- `connect_over_cdp()` НИКОГДА не используется (детектируется anti-bot системами)
- Proxy: BrightData residential через `http://user:pass@host:22225`
- `playwright_stealth.Stealth()` patches на каждой новой странице

**StealthContext** -- обёртка BrowserContext:
- `new_page()` -> создаёт Page + `_stealth.apply_stealth_async(page)` -> StealthPage
- Cookie management: `cookies()`, `add_cookies()`

**StealthPage** -- human-like interaction proxy:
- `goto(url)` -- навигация + random delay
- `click(selector)` -- bezier-curve mouse movement (15-25 промежуточных точек) + click
- `type_text(selector, text)` -- per-keystroke delay 50-150ms
- `scroll_to(selector)` -- smooth scroll
- `wait_random(min_s, max_s)` -- random sleep (default 1-5s)

**StealthConfig** (frozen dataclass):
- `headless=False` (рекомендуется для снижения детекции)
- viewport: 1920x1080
- locale: `en-US`, timezone: `America/New_York`
- proxy_url: опциональный
- min/max_delay: 1.0-5.0s

### Session Management (`src/browser/session.py`)

**SessionManager** -- cookie persistence в Valkey:
- **Key layout**: `browser:session:{platform}:cookies` (JSON), `browser:session:{platform}:meta` (JSON metadata)
- **TTL**: 72 часа (3 дня) по умолчанию
- `save_session(platform, context)` -- извлекает cookies, сериализует в JSON, сохраняет с TTL
- `restore_session(platform, context)` -- загружает cookies из Valkey, применяет к контексту
- `clear_session(platform)` -- удаление
- `get_session_meta(platform)` -- last_saved, cookie_count

### Browser Pool (`src/browser/pool.py`)

**BrowserPool** -- bounded pool browser-инстансов по одному на платформу:

- **`acquire(platform)`**: возвращает StealthPage. Если browser для платформы уже есть -- переиспользует. Если нет и лимит не превышен -- запускает новый. Если лимит -- evicts oldest (сохраняет cookies перед удалением).
- **`release(platform, page)`**: сохраняет cookies, закрывает page (browser остаётся)
- **`shutdown()`**: закрывает все браузеры

**PoolConfig**:
- `max_browsers=3`
- `proxy_rotation_minutes=45` -- после 45 минут browser пересоздаётся с новым proxy
- `session_ttl_hours=72`

**Proxy rotation**: при превышении `proxy_rotation_minutes` -- save cookies -> close browser -> launch new with fresh proxy -> restore cookies. Proxy URL: BrightData residential `http://{BRIGHTDATA_USERNAME}:{BRIGHTDATA_PASSWORD}@{BRIGHTDATA_HOST}:22225`.

## Adaptive Rate Limiter (`AdaptiveRateLimiter`)

Общий для всех платформ, per-platform state:

### Default limits

| Платформа | Initial RPM | Min RPM | Max RPM |
|-----------|-------------|---------|---------|
| freelancer | 15 | 2 | 30 |
| fl_ru | 5 | 1 | 10 |
| kwork | 3 | 1 | 6 |
| upwork | 3 | 1 | 6 |
| telegram | 30 | 5 | 60 |

### Адаптивная логика

- **Success** (каждые 5 подряд): rate x1.1 (до max_rpm)
- **429/timeout**: rate x0.5 (до min_rpm) + exponential backoff
- **Captcha/ban**: rate = min_rpm + 30-минутная пауза + Telegram alert

### Persistence

State сохраняется в Valkey (`mas:rate_limiter:{platform}`, HSET, TTL=24h) -- переживает рестарт процесса.

### Retry decorator

`@retry_on_transient(max_retries=3, rate_limiter=..., platform=...)`:
- Retries: `PlatformRateLimitError`, `TimeoutError`
- No retry: `PlatformBannedError`, `CaptchaDetectedError`, `CloudflareBlockError`

### Circuit Breaker Pattern

При повторных failures адаптер переходит в circuit breaker mode:

```python
class PlatformCircuitBreaker:
    """Circuit breaker для platform adapters.

    States: CLOSED (normal) → OPEN (blocked) → HALF_OPEN (probe)
    """

    FAILURE_THRESHOLD = 5       # Failures до открытия
    RECOVERY_TIMEOUT = 300      # 5 минут в OPEN state
    HALF_OPEN_MAX_CALLS = 2     # Пробных вызовов в HALF_OPEN

    def __init__(self, platform: str, valkey: Valkey) -> None:
        self.platform = platform
        self.valkey = valkey
        self.state_key = f"circuit:{platform}"

    async def can_execute(self) -> bool:
        """Проверка: можно ли делать запрос к платформе."""
        state = await self._get_state()

        if state["status"] == "closed":
            return True

        if state["status"] == "open":
            # Проверить timeout
            if time.time() - state["opened_at"] > self.RECOVERY_TIMEOUT:
                await self._transition("half_open")
                return True  # Пробный запрос
            return False

        if state["status"] == "half_open":
            return state["half_open_calls"] < self.HALF_OPEN_MAX_CALLS

        return False

    async def record_success(self) -> None:
        """Успешный запрос — закрыть circuit если half_open."""
        state = await self._get_state()
        if state["status"] == "half_open":
            await self._transition("closed")
        # Reset failure counter
        await self.valkey.hset(self.state_key, "failures", 0)

    async def record_failure(self) -> None:
        """Неуспешный запрос — инкремент failures, возможно открыть circuit."""
        failures = await self.valkey.hincrby(self.state_key, "failures", 1)

        state = await self._get_state()
        if state["status"] == "half_open":
            # Failure в half_open → обратно в open
            await self._transition("open")
        elif failures >= self.FAILURE_THRESHOLD:
            await self._transition("open")
            # Telegram alert
            await notify_circuit_open(self.platform)
```

**Интеграция с AdaptiveRateLimiter:**

```python
# В каждом адаптере
async def fetch_jobs(self, **kwargs):
    if not await self.circuit_breaker.can_execute():
        logger.warning("circuit_open", platform=self.platform)
        return []  # Graceful degradation

    try:
        result = await self._do_fetch(**kwargs)
        await self.circuit_breaker.record_success()
        return result
    except (PlatformRateLimitError, PlatformBannedError, TimeoutError) as e:
        await self.circuit_breaker.record_failure()
        raise
```

**Dashboard Integration:**

Circuit breaker status показывается на странице Settings → Platform Manager:
- 🟢 CLOSED — нормальная работа
- 🔴 OPEN — платформа заблокирована (показать время до retry)
- 🟡 HALF_OPEN — пробные запросы

## Dashboard Integration

(реализовано в Settings; описание в `docs/Full_work/specs/api-spec.md`)

- Platform Manager: CRUD для `platform_accounts` через API
- Connection Mode: API / Stealth / RSS (per platform)
- ON/OFF тумблер per platform

## ToS Compliance по платформам

### Общее правило

> **НИКОГДА не автоматизировать полностью отправку bid.** Все платформы имеют anti-bot detection. HITL обязателен для всех submissions.

### Матрица автоматизации

| Платформа | Auto-мониторинг | Auto-генерация bid | Auto-submit | Messaging | Ban Risk |
|-----------|----------------|-------------------|-------------|-----------|----------|
| **Freelancer** | API (safe) | safe | HITL (с осторожностью через API) | HITL | Medium |
| **Upwork** | Monitor only | safe | **ЗАПРЕЩЁН** | Manual | **High** |
| **FL.ru** | RSS (safe) | safe | N/A (нет API) | N/A | Low |
| **Kwork** | Stealth (careful) | safe | Manual only | Manual | Medium |
| **Fiverr** | Stealth (planned) | safe | Manual only | Manual | Medium-High |
| **Telegram** | Valkey queue | safe | N/A | N/A | Low |

### Risk Matrix

| Действие | Freelancer | Upwork | FL.ru | Kwork | Fiverr |
|----------|-----------|--------|-------|-------|--------|
| Monitor jobs | Safe | Careful | Safe | Careful | Careful |
| Auto-generate proposals | Safe | Safe | Safe | Safe | Safe |
| Auto-submit bids | Risky | **BANNED** | N/A | Manual only | Manual only |
| Mass messaging | Risky | **BANNED** | Risky | Manual only | Manual only |
| Residential proxy | Required | Required | N/A | Required | Required |

### Freelancer.com — ToS Compliance

**Официальная политика:** предоставляет REST API, разрешает автоматизацию для поиска, деталей проектов, messaging, project management. Bid submission через API разрешён (rate limited, OAuth required).

**Риски:**
- Rate limits: 30 bids/day, 100 API calls/hour
- Pattern detection: идентичные proposals → флаг
- Account verification: может потребовать phone/ID

**Ban recovery:**

| Сценарий | Действие |
|----------|----------|
| Rate limit exceeded | Ожидание 24ч, снижение частоты |
| Temporary suspension | Обращение в поддержку, верификация |
| Permanent ban | Backup аккаунт (другой IP, email, payment) |
| IP ban | Switch to residential proxy |

### Upwork — ToS Compliance

> **ToS Section 5.3:** "Automated access to the Upwork platform is prohibited without prior written consent."

**Явно запрещено:**
- Auto-submit proposals
- Scraping job listings at scale
- Использование ботов для bidding
- Mass messaging клиентов

**Серая зона (tolerated):**
- GraphQL API для personal dashboard
- Browser extensions для personal use
- Manual assisted tools

**Стратегия:** MONITORING ONLY. Scout → Telegram alert → человек вручную открывает Upwork, ревьюит, сабмитит.

**Методы детекции Upwork:**
- Browser fingerprinting (Canvas, WebGL, fonts)
- Mouse movement analysis
- Timing analysis (too fast = bot)
- IP reputation (datacenter = flag)
- Device fingerprinting

**Ban recovery:**

| Тип | Восстановление |
|-----|---------------|
| Temporary suspension | Appeal, ожидание 7-14 дней |
| Permanent ban | **Нет recovery** — новая идентичность |
| Device ban | Новое устройство + IP + payment |

> **WARNING:** Создание нового аккаунта после бана = permanent ban ВСЕХ аккаунтов.

### FL.ru — ToS Compliance

RSS feeds публично доступны — полностью разрешено. Rate limit: 10 req/час (достаточно). API для submission отсутствует — bid отправляется вручную.

### Kwork — ToS Compliance

Нет официального API. Web scraping — серая зона. Стратегия: careful scraping через Playwright Stealth, 30 pages/час max, proxy rotation каждые 10 запросов, human-like delays 3-10с. Submission — вручную.

**Anti-detection:** stealth=True, timezone=Europe/Moscow, locale=ru-RU, viewport 1920x1080, residential proxy.

### Fiverr — ToS Compliance (планируется)

Нет публичного API. Buyer Requests доступны только для зарегистрированных продавцов. Агрессивная защита Cloudflare + device fingerprinting. Стратегия: careful monitoring Buyer Requests, bid через UI вручную (HITL).

### Incident Response (все платформы)

**При флаге аккаунта:**
1. СТОП вся автоматизация немедленно
2. Ожидание 24-48ч
3. Ручной вход, верификация
4. Возобновление на 50% от прежней частоты
5. Мониторинг 1 неделю до возврата к норме

**При бане:**
1. НЕ создавать новый аккаунт сразу
2. Задокументировать причину бана
3. Ожидание минимум 30 дней
4. Новая идентичность: email, IP (residential, другой регион), payment, device/browser profile
5. Старт с минимальной активности

### Multi-Account Strategy

| Аккаунт | Платформа | Назначение | Статус |
|---------|-----------|------------|--------|
| Primary | Freelancer | Main bidding | Active |
| Backup | Freelancer | Standby | Warm (occasional use) |
| Primary | Kwork | Russian market | Active |
| Monitoring | Upwork | Job discovery | HITL only |

**Правила изоляции аккаунтов:**
1. Отдельные IP (каждый аккаунт на своём proxy)
2. Отдельные email (разные провайдеры)
3. Отдельные payment (разные карты/PayPal)
4. Отдельные устройства (или browser profiles)
5. Нет cross-linking (не упоминать другие аккаунты)

## Ключевые файлы

- `src/adapters/freelancer.py` -- FreelancerClient (REST API)
- `src/adapters/fl_ru.py` -- FlRuClient (RSS feed)
- `src/adapters/kwork.py` -- KworkClient (Playwright scraper)
- `src/adapters/upwork.py` -- UpworkClient (Playwright read-only)
- `src/adapters/telegram_channels.py` -- TelegramChannelAdapter (Valkey queue)
- `src/adapters/rate_limiter.py` -- AdaptiveRateLimiter + retry_on_transient decorator
- `src/browser/stealth.py` -- StealthBrowser, StealthContext, StealthPage, StealthConfig
- `src/browser/session.py` -- SessionManager (Valkey cookie persistence)
- `src/browser/pool.py` -- BrowserPool, PoolConfig (bounded pool + proxy rotation)
- `src/core/exceptions.py` -- PlatformAPIError, PlatformRateLimitError, PlatformBannedError, CaptchaDetectedError, CloudflareBlockError
