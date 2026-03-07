# Security: Спецификация

## Semgrep Gate

### Класс `SemgrepGate` (`src/security/semgrep_gate.py`)

Статический анализатор кода, запускаемый перед выполнением в sandbox. Обёртка над CLI `semgrep`.

```python
gate = SemgrepGate(rules_dir="src/security/rules/")
result = await gate.scan_code("import os; os.system('rm -rf /')")
if result.blocked:
    # код отклонён
```

### Fail-closed дизайн

- Если бинарник `semgrep` не найден (`FileNotFoundError`):
  - **Production:** `blocked=True`, finding `mas-semgrep-unavailable` — код НЕ выполняется
  - **Debug mode** (`DEBUG=True`): `blocked=False` — пропускает для удобства разработки
- Если вывод `semgrep` не парсится как JSON: `blocked=True`, finding `mas-semgrep-parse-error`
- Если `semgrep` timeout (>60с): `blocked=True`, finding `mas-semgrep-timeout`

### Результат сканирования

```python
@dataclass
class ScanResult:
    findings: list[SemgrepFinding]  # rule_id, severity, message, path, line, code_snippet
    critical_count: int             # severity=ERROR
    warning_count: int              # severity=WARNING
    blocked: bool                   # True если critical_count > 0
```

Правило блокировки: `blocked = critical_count > 0`. Warnings не блокируют.

### Методы

- `scan_code(code, language="python")` — сканирует один фрагмент кода (создаёт temp file)
- `scan_files(files)` — сканирует набор файлов `[{"path": ..., "content": ...}]`
- Path sanitization: strip `..`, `/`, `\` — защита от traversal при создании temp files

### Интеграция с агентами

- **Critic Agent** — запускает Semgrep перед `approve`. Если `blocked=True` — verdict `reject`
- **Dev Agent** — `analyze_with_semgrep` tool для самопроверки кода

В тестах: autouse fixture `_mock_semgrep_gate` в `tests/conftest.py` патчит `SemgrepGate` для `src.agents.critic` и `src.agents.dev`.

### Custom rules (`src/security/rules/`)

**`mas_dangerous_patterns.yaml`** — 8 правил (все ERROR):

| Rule ID | Паттерн | Язык |
|---------|---------|------|
| `mas-dangerous-os-system` | `os.system(...)` | Python |
| `mas-dangerous-eval` | `eval(...)` | Python |
| `mas-dangerous-exec` | `exec(...)` | Python |
| `mas-dangerous-dunder-import` | `__import__(...)` | Python |
| `mas-dangerous-subprocess-call-shell` | `subprocess.call(..., shell=True)` | Python |
| `mas-dangerous-subprocess-popen-shell` | `subprocess.Popen(..., shell=True)` | Python |
| `mas-dangerous-subprocess-run-shell` | `subprocess.run(..., shell=True)` | Python |
| `mas-dangerous-subprocess-check-output-shell` | `subprocess.check_output(..., shell=True)` | Python |

**`mas_file_access.yaml`** — 11 правил (все ERROR):

- Запись в системные директории: `/etc/`, `/usr/`, `/var/`, `/root/`, `/sys/`, `/proc/`
- Чтение `.env` файлов (прямо и через path)
- Чтение credential/secret/password/token/api_key/private_key файлов
- Чтение SSH-ключей (`.ssh/`)

**`mas_network_access.yaml`** — 8 правил:

- `socket.connect()` — ERROR (прямой сетевой доступ)
- `urllib.request.urlopen()` — ERROR
- `requests.get/post/put/delete()` — WARNING (может быть легитимным)
- `httpx.get/post()` — WARNING

WARNING-правила не блокируют выполнение, но фиксируются в findings.

## Encryption

### Fernet-шифрование (`src/security/encryption.py`)

Симметричное шифрование для credentials через `cryptography.fernet.Fernet`.

**Ключ:**
- Переменная окружения `ENCRYPTION_KEY`
- Если значение — валидный 32-byte URL-safe base64 Fernet ключ → используется напрямую
- Иначе — деривация через PBKDF2-HMAC-SHA256 (480,000 итераций, фиксированный salt)
- В production: `ENCRYPTION_KEY == "change-me-in-production"` → `ValueError` (защита от дефолта)

**API:**
- `encrypt_dict(data)` → JSON-сериализация → Fernet encrypt → URL-safe base64 строка
- `decrypt_dict(token)` → Fernet decrypt → JSON parse → dict
- `encrypt_credentials(creds)` → `{"_encrypted": "<fernet-token>"}`
- `decrypt_credentials(creds)` → если `_encrypted` ключ есть → расшифровка, иначе as-is
- `mask_value(value, visible=4)` → `"*************3xyz"` (для логов и API)
- `mask_dict(data)` — рекурсивная маскировка всех значений

### Что шифруется

- Platform credentials (Freelancer API keys, OAuth tokens) в таблице `PlatformAccount`
- API ключи (OpenRouter, OpenAI, Hunter, Apollo) через Settings dashboard
- Формат хранения в БД: `{"_encrypted": "<fernet-token>"}`

## Sandbox Isolation

### Абстракция (`src/sandbox/base.py`)

```python
class SandboxExecutor(ABC):
    async def execute(files, command=None, timeout_seconds=60) -> ExecutionResult
    async def cleanup() -> None

@dataclass
class ExecutionResult:
    stdout, stderr, exit_code, timed_out, duration_ms, files_created
    success: bool  # exit_code == 0 and not timed_out
```

### Docker Executor (`src/sandbox/docker_executor.py`) — Primary

- Одноразовый контейнер на каждый вызов
- Образ: `mas-sandbox:latest`
- **Ограничения ресурсов:** CPU/memory limits (через Docker SDK)
- **Сетевая изоляция:** нет сетевого доступа из контейнера
- **Non-root user:** код выполняется не от root
- **Path sanitization:** strip `..`, `/`, `\` из file paths перед записью в контейнер
- Все Docker SDK вызовы обёрнуты в `asyncio.to_thread()` (SDK синхронный)
- Обязательный `client.close()` после использования (предотвращение утечки fd/socket)
- Поддержка: Python, JavaScript, TypeScript

### E2B Executor (`src/sandbox/e2b_executor.py`) — Fallback

- Cloud sandbox через `e2b-code-interpreter` SDK
- **Optional dependency:** `_E2B_AVAILABLE` flag, graceful degradation при отсутствии пакета
- Ограничение: таймаут 5-10 мин (не подходит для долгих задач)
- Ключ: `E2B_API_KEY`
- Используется только для быстрой валидации кода

### SandboxManager (`src/sandbox/manager.py`)

Роутер между Docker и E2B: `estimated_time > 5 мин → Docker, иначе → E2B`.

## Anti-Detection (Playwright Stealth)

### Класс `StealthBrowser` (`src/browser/stealth.py`)

Chromium launcher с anti-detection мерами. **Критически:** `connect_over_cdp()` НИКОГДА не используется — CDP-соединения тривиально детектируются.

**Launch args:**
- `--disable-blink-features=AutomationControlled` — убирает `navigator.webdriver = true`
- `--no-sandbox`, `--disable-setuid-sandbox`, `--disable-dev-shm-usage`
- `headless=False` по умолчанию (headless легче детектируется)

**`playwright-stealth` интеграция:**
- Модуль-уровневый инстанс: `_stealth = Stealth()`
- `await _stealth.apply_stealth_async(page)` — на каждую новую страницу
- Патчит: WebGL, Canvas, AudioContext, navigator properties

### Класс `StealthPage`

Human-like interactions:
- **Mouse:** движение по кубической кривой Безье (15-25 шагов) с рандомными control points
- **Typing:** случайная задержка 50-150мс между нажатиями клавиш
- **Delays:** рандомная пауза 1-5с между действиями (настраивается через `StealthConfig`)
- **Scrolling:** smooth scroll через `scrollIntoView({ behavior: 'smooth' })`

### Класс `StealthConfig`

```python
@dataclass(frozen=True, slots=True)
class StealthConfig:
    headless: bool = False
    viewport_width: int = 1920
    viewport_height: int = 1080
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    proxy_url: str | None = None      # http://user:pass@host:port
    min_delay: float = 1.0
    max_delay: float = 5.0
```

User-agent: реалистичный Windows Chrome 124.

### Session Persistence (`src/browser/session.py`)

Класс `SessionManager` — сохранение cookies в Valkey:
- Ключи: `browser:session:{platform}:cookies`, `browser:session:{platform}:meta`
- TTL: 72ч по умолчанию
- `save_session(platform, context)` — извлечение cookies из BrowserContext → JSON → Valkey
- `restore_session(platform, context)` — восстановление cookies из Valkey в BrowserContext
- `clear_session(platform)` — удаление сессии
- Изоляция: 1 платформа = 1 proxy = 1 сессия

## Rate Limiting и Anti-Ban

### Класс `AdaptiveRateLimiter` (`src/adapters/rate_limiter.py`)

Адаптивный rate limiter с Valkey-backed state. Автоматически подстраивает частоту запросов.

**Лимиты по платформам (RPM):**

| Платформа | Max RPM | Min RPM | Initial RPM |
|-----------|---------|---------|-------------|
| Freelancer | 30 | 2 | 15 |
| FL.ru | 10 | 1 | 5 |
| Kwork | 6 | 1 | 3 |
| Upwork | 6 | 1 | 3 |
| Telegram | 60 | 5 | 30 |

**Адаптивная логика:**
- **Успех** → каждые 5 подряд: rate * 1.1 (до max_rpm)
- **429 / timeout** → rate * 0.5 (до min_rpm)
- **Captcha / ban** → rate = min_rpm + пауза 30 мин + Telegram alert
- State сохраняется в Valkey (`mas:rate_limiter:{platform}`, TTL 24ч)

**Декоратор `retry_on_transient()`:**
- Retries: `PlatformRateLimitError`, `TimeoutError` — до 3 попыток с exponential backoff
- Не retries: `PlatformBannedError`, `CaptchaDetectedError`, `CloudflareBlockError` — немедленный re-raise + запись ban

### Telegram FloodWait (специфика)

`FloodWait > 300с` → полная остановка на 24ч (реализовано в telegram adapter).

## Platform Policies Compliance

| Платформа | Политика | Ограничение |
|-----------|---------|-------------|
| **Upwork** | Auto-submit **ЗАПРЕЩЁН** (ToS violation) | Только мониторинг, read-only |
| **Freelancer.com** | REST API в рамках лимитов | API rate limits соблюдаются |
| **Kwork** | Нет API, stealth scraping | Обязательно Playwright + proxy |
| **FL.ru** | RSS feed (публичный) | Без ограничений |

## Authentication (API)

### JWT (`src/api/guards.py`)

- **Библиотека:** `litestar.security.jwt.JWTAuth`
- **Алгоритм:** HS256
- **Secret:** `JWT_SECRET_KEY` из Settings
- **Access token TTL:** `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (из config)
- **Refresh token TTL:** `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (из config)
- **Claims:** `sub` (user UUID), `email`, `role`, `jti` (unique token id), `type` (для refresh)

**Token lifecycle:**
- `create_access_token(user)` → short-lived JWT
- `create_refresh_token(user)` → long-lived JWT с `type: "refresh"`
- `retrieve_user_handler(token)` → запрос User из БД по `token.sub`, проверка `status == "active"`
- Blacklist: `token:blacklist:{jti}` в Valkey — проверяется на каждом запросе
- Fail-closed: если Valkey недоступен → доступ запрещён (предотвращает использование blacklisted tokens)

**Пароли:** bcrypt через `bcrypt.gensalt(rounds=12)` + `bcrypt.hashpw()`.

**Role guard:** `require_role("owner", "moderator")` — декоратор для endpoint'ов.

### Rate Limiting (API)

- 300/min global, 60/min auth endpoints
- **Проблема Railway:** reverse proxy схлопывает все client IP в один внутренний (`100.64.0.3`)
- Litestar `RateLimitConfig` трекает по IP → все пользователи делят один bucket
- Планируется: per-user rate limiting через JWT

## SQL Injection Prevention

### Хелпер `_escape_like()` (`src/api/routes/__init__.py`)

Экранирует спецсимволы `%`, `_`, `\` для ILIKE-запросов. Используется в:
- `src/api/routes/jobs.py`
- `src/api/routes/hitl.py`
- `src/api/routes/pipeline_b.py`
- `src/api/routes/agents.py`

Все остальные SQL-запросы используют параметризованные выражения (SQLAlchemy / asyncpg `$1`-style).

## Stealth Browser: Детали Anti-Detection

### JavaScript Injection (anti-fingerprinting)

`StealthBrowser._inject_stealth_scripts(page)` выполняет набор `add_init_script()` для обхода детекции:

| Техника | Что делает |
|---------|-----------|
| `navigator.webdriver = undefined` | Убирает флаг автоматизации (Playwright ставит `true`) |
| `navigator.plugins` mock | Headless имеет пустой plugins array — мокаем 3 плагина (Chrome PDF Plugin, Chrome PDF Viewer, Native Client) |
| `navigator.languages` mock | Устанавливает `['en-US', 'en']` |
| `window.__playwright` delete | Убирает Playwright-специфичные глобалы (`__playwright`, `__pw_manual`) |
| `window.chrome` mock | Создаёт объект `chrome.runtime`, `chrome.loadTimes`, `chrome.csi`, `chrome.app` |
| `Permissions API` mock | Перехватывает `navigator.permissions.query` для `notifications` |

Дополнительно `playwright-stealth` библиотека (`Stealth()`) патчит: WebGL renderer, Canvas fingerprint, AudioContext, navigator properties.

### Browser Fingerprint Evasion

**Viewport рандомизация:** размер окна 1280-1400 x 800-900 (рандом при каждом запуске). Device scale factor: 1.0, is_mobile: false, has_touch: false.

**Geolocation + Timezone:** привязаны к proxy location. Default: NYC (`-73.935242, 40.730610`), timezone `America/New_York`, locale `en-US`. Для Kwork: timezone `Europe/Moscow`, locale `ru-RU`.

**User-Agent ротация:** пул из 3+ реалистичных UA (Windows Chrome, macOS Chrome, Firefox). Ротация ежемесячно.

### Launch Arguments (полный список)

```
--disable-blink-features=AutomationControlled
--disable-dev-shm-usage
--no-sandbox
--disable-web-security
--disable-features=VizDisplayCompositor
--disable-breakpad
--window-size={random}
```

**КРИТИЧНО:** `connect_over_cdp()` НИКОГДА не используется — CDP-соединения тривиально детектируются anti-bot системами.

### Proxy Strategy

- **Residential proxy обязателен** для Upwork, Freelancer, Kwork. Datacenter IP детектируются мгновенно
- Провайдер: BrightData (`brd.superproxy.io:22225`)
- **Изоляция:** 1 платформа = 1 proxy IP = 1 browser profile = 1 сессия
- **Proxy rotation:** каждые 45 минут browser пересоздаётся (save cookies → close → launch new → restore cookies)

| Провайдер | Тип | Стоимость | Назначение |
|-----------|-----|-----------|------------|
| BrightData | Residential | ~$15/GB | Upwork, high-security |
| Oxylabs | Residential | ~$12/GB | General automation |
| SmartProxy | Datacenter | ~$5/GB | Low-security (НЕ для freelance платформ) |

### Session Rotation и Cooldown

**SessionRotator** — LRU-ротация сессий по платформам:
- Пул сессий per platform (e.g., `freelancer_main`, `freelancer_backup`)
- Выбор: least-recently-used session
- При бане: `mark_compromised()` → cooldown 48ч (Valkey TTL)

**AccountIsolation** — каждый аккаунт получает уникальный профиль:
- Proxy IP (отдельный для каждого аккаунта)
- User-Agent (из пула по индексу)
- Viewport (разные размеры)
- Timezone (соответствует proxy location)
- Browser cookies (изолированное хранилище)

### Cookie Encryption

Cookies шифруются при сохранении в Valkey через Fernet (AES-128-CBC):
- Key: тот же `ENCRYPTION_KEY` из env
- TTL: 7 дней max
- Key layout: `browser:cookies:{session_name}`

### Rate Limiting (browser actions)

| Действие | Min Delay | Max Delay |
|----------|-----------|-----------|
| Page navigation | 2с | 5с |
| Form submission | 1с | 3с |
| Между нажатиями клавиш | 50мс | 150мс |
| Scroll actions | 300мс | 800мс |
| Между bid submissions | 5 мин | 15 мин |

### Emergency Procedures (ban/captcha)

1. СТОП вся автоматизация немедленно
2. Ротация на другую сессию
3. Смена proxy IP
4. Ожидание 24-48ч перед возобновлением
5. Ручная верификация (если требуется)

### Методы детекции платформ

**Upwork** (наиболее агрессивный):
- Browser fingerprinting (Canvas, WebGL, шрифты)
- Mouse movement analysis (bot vs human patterns)
- Timing analysis (слишком быстро = bot)
- IP reputation (datacenter IP → мгновенный бан)
- Device fingerprinting

**Kwork:**
- Cloudflare protection (`#challenge-stage`)
- reCAPTCHA (`#challenge-running`, `.g-recaptcha`)
- User behaviour analysis
- Увеличенные задержки 2-7с (vs стандартные 1-5с)

## Ключевые файлы

- `src/security/semgrep_gate.py` — SemgrepGate, ScanResult, SemgrepFinding
- `src/security/rules/` — 3 YAML-файла с 27 Semgrep-правилами
- `src/security/encryption.py` — Fernet encrypt/decrypt, mask, PBKDF2 key derivation
- `src/browser/stealth.py` — StealthBrowser, StealthContext, StealthPage
- `src/browser/session.py` — SessionManager (cookie persistence в Valkey)
- `src/adapters/rate_limiter.py` — AdaptiveRateLimiter, retry_on_transient
- `src/sandbox/base.py` — SandboxExecutor ABC, ExecutionResult
- `src/sandbox/docker_executor.py` — DockerExecutor (primary sandbox)
- `src/sandbox/e2b_executor.py` — E2BExecutor (fallback sandbox)
- `src/api/guards.py` — JWTAuth, password hashing, role guards
- `src/core/credential_loader.py` — DB-first credential loading
