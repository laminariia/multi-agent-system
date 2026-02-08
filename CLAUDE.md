# Multi-Agent System (MAS) — Полный контекст проекта

> **Этот файл — главный источник контекста для разработки.**  
> Claude Code: читай его первым при работе над проектом.

**GUI:** См. `docs/gui/GUI_SPECIFICATION.md` для wireframes и design system.

## 🎯 Цель проекта

Создать **автономную мульти-агентную систему из 10 ИИ-агентов**, которая работает 24/7 на сервере и выполняет две задачи:

1. **Pipeline A (Freelance):** Автоматический поиск заказов на фриланс-биржах, генерация откликов, выполнение задач
2. **Pipeline B (Outreach):** Поиск офлайн-бизнесов без сайтов, генерация холодных писем

**Ключевая особенность:** Human-in-the-Loop (HITL) — человек одобряет критические действия (отправку откликов, финальную сдачу работы).

---

## 🏗️ Архитектура системы

### Технологический стек

| Слой | Технология | Назначение |
|------|------------|------------|
| **Оркестрация** | LangGraph 1.0 | Граф состояний, управление агентами |
| **LLM** | Gemini 3 Flash + Claude Opus 4.6 + GPT 5.3 Codex | Flash для большинства, Opus для кода, GPT для code review |
| **Backend** | Litestar + Python 3.12 | API, WebSocket для UI |
| **База данных** | PostgreSQL | Состояния LangGraph, история, задачи |
| **Кэш/Очереди** | Valkey | Pub/Sub, очереди задач, сессии |
| **Песочница** | Docker (E2B для коротких) | Изолированное выполнение кода |
| **Браузер** | Playwright + Stealth | Мониторинг, НЕ auto-submit |
| **Frontend** | Remix | Dashboard для HITL |
| **Эмбеддинги** | Google text-embedding-004 | Векторный поиск (768 dim) |
| **Деплой** | Docker + VPS | Hetzner/DigitalOcean |

### 10 агентов системы

```
Pipeline A (Freelance):
├── Scout Agent      — Поиск заказов (RSS, API)
├── Bid Agent        — Генерация и отправка откликов
├── Planner Agent    — Декомпозиция задач
├── Dev Agent        — Генерация кода (fullstack)
├── Content Agent    — Копирайтинг, документация
├── Design Agent     — UI/UX, графика
├── Critic Agent     — Проверка качества + Semgrep
└── Packager Agent   — Сборка и доставка

Pipeline B (Outreach):
├── Geo Scout Agent  — Поиск бизнесов через OSM/H3
└── Outreach Agent   — Обогащение лидов + email
```

---

## 📊 Поток данных

```
[Cron: 5 мин] → Scout → Valkey Queue → Bid → [HITL: одобрение] → Submit
                                              ↓
                                          Planner → Dev/Content/Design (параллельно)
                                              ↓
                                          Critic → [HITL: финал] → Packager → Delivery
```

### Ключевые точки HITL:
1. **Bid Approval** — одобрение отклика перед отправкой
2. **Final Review** — проверка готовой работы перед сдачей
3. (Опционально) Plan Review — проверка плана задач

---

## 🔧 Ключевые технические решения

### 1. LLM стратегия (трёхуровневая)
- **Gemini 3 Flash** ($0.001/1K): Scout, Bid, Content, Packager, GeoScout, Outreach
- **Claude Opus 4.6** ($0.015/1K): Planner + Dev Agent (высокое качество планирования и кодогенерации)
- **GPT 5.3 Codex**: Critic Agent (лучший для code review)
- **Gemini 3 Pro** (NanoBanana Pro): Design Agent (генерация изображений)

> Полная таблица агент → модель: см. `TECH_STACK.md` → "LLM Models — Canonical Agent Assignment"

### 2. Платформы фриланса (HITL-FIRST!)

> ⚠️ **КРИТИЧНО:** Upwork ЗАПРЕЩАЕТ auto-submit заявок. Только HITL!

| Платформа | Интеграция | Метод | Auto-submit |
|-----------|------------|-------|-------------|
| Freelancer.com | ✅ API | REST API | ⚠️ С осторожностью |
| Upwork | ⚠️ Опционально | Только мониторинг через Playwright+Stealth ИЛИ ручной поиск заказов | ❌ ЗАПРЕЩЕНО |
| FL.ru | ✅ RSS Feed | Парсинг | N/A |
| Kwork | ⚠️ Scraper | Playwright | ⚠️ Риск бана |

### 3. Безопасность кода
- **Docker** — для задач >5 мин (E2B ограничен 5-10 мин!)
- **E2B Sandbox** — только для быстрых тестов (<5 мин)
- **Semgrep** — статический анализ перед выполнением
- **Запрещённые паттерны:** `os.system()`, `eval()`, `subprocess.call(shell=True)`

### 4. Гео-поиск бизнесов
- **H3 Hexagonal Indexing** — эффективное разбиение города на гексагоны
- **Overpass API** — запросы к OpenStreetMap
- **Enrichment Waterfall:**
  1. OSINT (бесплатно): Google, соцсети
  2. Hunter.io ($0.01/контакт)
  3. Apollo.io ($0.05/контакт) — только для premium ниш

### 5. Мониторинг
- **Heartbeat** — каждый агент пингует каждые 90 сек
- **Auto-restart** — при timeout > 180 сек
- **LangSmith** — трассировка всех LLM-вызовов

### 6. Лимиты параллельных проектов
- Phase 1: 5 параллельных проектов, Phase 2+: до 15 с масштабированием по tier

### 7. Антидетект для браузера (КРИТИЧНО!)

> ⚠️ Без stealth-режима бан за 1-2 недели на Upwork/Freelancer.

**Обязательные техники:**

```python
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def create_stealth_browser():
    browser = await playwright.chromium.launch(
        headless=False,  # НЕ headless для меньшего детекта
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-setuid-sandbox'
        ]
    )
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        locale='en-US',
        timezone_id='America/New_York',
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64)...'
    )
    page = await context.new_page()
    await stealth_async(page)  # Применить stealth патчи
    return page
```

**Checklist:**
- ✅ **playwright-stealth** — скрытие navigator.webdriver
- ✅ **Резидентные прокси** — BrightData/Oxylabs (НЕ datacenter!)
- ✅ **Сохранение cookies** — Redis persistence
- ✅ **Human-like delays** — random 1-5 сек между действиями
- ✅ **Движения мыши** — bezier curves, не teleport
- ✅ **Typing simulation** — random delay между keystrokes
- ⚠️ **НЕ использовать CDP protocol** — легко детектируется
- ⚠️ **Rotation IP** — менять прокси каждые 30-60 мин

---

## 📊 Success Rate Benchmarks (Реалистичные!)

> 📎 Детали см. в `docs/deep_research_freelance_market.md`

| Тип проекта | Ожидаемый success rate | Время |
|-------------|----------------------|-------|
| Landing pages | **95%+** | ~1 час |
| WordPress sites | **90%+** | 2-4 часа |
| React components | **85-90%** | 4-8 часов |
| Full websites | **80-85%** | 6-12 часов |
| Simple web apps | **70-80%** | 15-30 часов |
| Complex apps | **50-60%** | Требует HITL |

**⚠️ НЕ ВЕРЬТЕ 95%+ оценкам для всего!** — это только для micro/small проектов.

### Bid Win Rate Reality

| Метрика | Значение |
|---------|----------|
| Среднее заявок на проект | 22 |
| Win rate новичка | 5-10% |
| Win rate с репутацией | 15-25% |
| **Нужно bids/day** | **50-100** |

---

## 💰 Оценка стоимости (месяц, 24/7) — ОБНОВЛЕНО

> ⚠️ Исправлено на основе реальных цен API (Jan 2026)

| Компонент | Стоимость | Примечание |
|-----------|-----------|------------|
| **LLM API** | **$450-600** | Claude Opus дорогой! |
| Docker/E2B | $50-100 | Docker дешевле |
| Lead Enrichment | $40-100 | Hunter.io + Apollo |
| Redis/Postgres | $50-90 | |
| Прокси | $60-100 | |
| VPS сервер | $20-40 | |
| Email (warm-up) | $50-100 | Instantly.ai |
| **ИТОГО** | **$800-1200/мес** | Пересчитано с учётом объёма bids и embedding costs |

---

## 📁 Структура проекта

```
multi-agent-service/
├── src/
│   ├── agents/                 # Логика агентов
│   │   ├── base.py            # Базовый класс с heartbeat
│   │   ├── scout.py           # Pipeline A: поиск заказов
│   │   ├── bid.py             # Pipeline A: отклики
│   │   ├── planner.py         # Декомпозиция задач
│   │   ├── dev.py             # Генерация кода
│   │   ├── content.py         # Копирайтинг
│   │   ├── design.py          # Дизайн
│   │   ├── critic.py          # QA + Semgrep
│   │   ├── packager.py        # Сборка
│   │   ├── geo_scout.py       # Pipeline B: гео-поиск
│   │   └── outreach.py        # Pipeline B: email
│   ├── core/
│   │   ├── graph.py           # LangGraph StateGraph
│   │   ├── state.py           # AgentState TypedDict
│   │   ├── heartbeat.py       # Мониторинг агентов
│   │   └── semantic_cache.py  # Кэш LLM ответов
│   ├── adapters/
│   │   ├── freelancer.py      # API Freelancer.com
│   │   ├── upwork.py          # Playwright для Upwork
│   │   ├── fl_ru.py           # RSS парсер FL.ru
│   │   └── kwork.py           # Scraper Kwork
│   ├── browser/
│   │   ├── stealth.py         # Антидетект настройки
│   │   ├── session.py         # Управление cookies
│   │   └── pool.py            # Пул браузеров
│   ├── security/
│   │   ├── semgrep_gate.py    # Статический анализ
│   │   └── rules/             # Semgrep правила
│   ├── enrichment/
│   │   ├── waterfall.py       # Каскадное обогащение
│   │   ├── osint.py           # Бесплатные источники
│   │   ├── hunter.py          # Hunter.io API
│   │   └── apollo.py          # Apollo.io API
│   ├── geo/
│   │   ├── h3_scanner.py      # H3 индексация
│   │   └── overpass.py        # OSM запросы
│   ├── api/
│   │   ├── main.py            # Litestar app
│   │   ├── websocket.py       # Real-time updates
│   │   └── routes/
│   └── prompts/               # System prompts агентов
├── dashboard/                  # Remix frontend
├── knowledge/                  # RAG базы знаний
│   ├── proposals/             # Успешные отклики
│   └── portfolio/             # Кейсы проектов
├── tests/
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .env.example
├── requirements.txt
├── pyproject.toml
└── CLAUDE.md                  # ← Этот файл
```

---

## 🔑 Переменные окружения

> Каноничные имена — см. `TECH_STACK.md` → "Environment Variables"

```bash
# LLM APIs
GEMINI_API_KEY=
ANTHROPIC_API_KEY=          # Claude Opus 4.6 (Planner, Dev)
OPENAI_API_KEY=             # GPT 5.3 Codex (Critic Agent)

# Databases
DATABASE_URL=postgresql://user:pass@localhost:5432/mas
VALKEY_URL=valkey://localhost:6379  # Redis-compatible

# E2B Sandbox
E2B_API_KEY=

# Freelance Platforms
FREELANCER_CLIENT_ID=
FREELANCER_CLIENT_SECRET=
# UPWORK_EMAIL=             # OPTIONAL
# UPWORK_PASSWORD=          # OPTIONAL

# Enrichment
HUNTER_API_KEY=
APOLLO_API_KEY=

# Proxies
BRIGHTDATA_USERNAME=
BRIGHTDATA_PASSWORD=
BRIGHTDATA_HOST=brd.superproxy.io

# Auth
JWT_SECRET_KEY=
ENCRYPTION_KEY=  # Fernet key for credentials encryption

# Notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Monitoring
LANGSMITH_API_KEY=
SENTRY_DSN=
```

---

## 📋 Текущий статус разработки

### ✅ Готово:
- Архитектура v4.2 с 5 новыми фичами
- Техническое руководство по реализации
- Анализ 6 источников deep research

### 🔄 В процессе:
- Создание структуры проекта
- Написание базовых агентов

### ⏳ Запланировано:
- Phase 1: Scout + Bid + HITL dashboard
- Phase 2: Dev + Content + Critic
- Phase 3: Geo Scout + Outreach
- Phase 4: Деплой 24/7

---

## 📚 Связанные документы

### Архитектура
- `mas_architecture_v4.2.md` — детальная архитектура
- `technical_implementation_guide.md` — техническое руководство
- `research_cross_comparison.md` — анализ исследований

### Phase 0.5 документация
- `docs/database_schema.md` — схема БД
- `docs/api_specification.md` — API спецификация + **versioning strategy**
- `docs/auth_specification.md` — Auth & RBAC
- `docs/agent_specifications.md` — Content & Design агенты
- `docs/agent_specifications_core.md` — **Scout, Bid, Planner, Dev** агенты
- `docs/agent_specifications_support.md` — **Critic, Outreach, Packager, GeoScout** агенты
- `docs/rate_limiting.md` — стратегия лимитов
- `docs/negotiation_flows.md` — потоки переговоров
- `docs/telegram_bot.md` — спецификация бота
- `docs/edge_cases.md` — обработка ошибок + **disputes, LLM outage**
- `docs/deployment.md` — Docker, env vars, **logs, SSL, secret rotation**
- `docs/backup_recovery.md` — Backup, failover, disaster recovery
- `docs/platform_policies.md` — **ToS compliance, ban recovery, multi-account**
- `docs/legal_compliance.md` — **Taxes, currency control, GDPR**
- `docs/langgraph_state.md` — **State management, checkpoints, recovery**
- `docs/knowledge_base.md` — **RAG, embeddings, pgvector**
- `docs/semantic_cache.md` — **LLM caching, TTL, invalidation**
- `docs/ci_cd.md` — **GitHub Actions, Docker, VPS deployment**
- `docs/testing_strategy.md` — **Unit, Integration, E2E tests, LLM output testing**
- `docs/performance.md` — **Benchmarks, scaling, load testing, database optimization**

### 🔬 Deep Research (критично!)
- `deep-analysis-report.md` — **ЧИТАТЬ ОБЯЗАТЕЛЬНО**
- `docs/deep_research_freelance_market.md` — анализ рынка

### GUI
- `docs/gui/GUI_SPECIFICATION.md` — wireframes и design system

---

## 🚀 Как начать разработку

1. **Создать виртуальное окружение:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
   ```

2. **Установить зависимости:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Запустить инфраструктуру:**
   ```bash
   docker-compose up -d postgres valkey
   ```

4. **Запустить API:**
   ```bash
   litestar --app src.api.main:app run --reload
   ```

---

## 🔄 Сценарий правок после сдачи работы (Revision Flow)

### Полный цикл с правками клиента

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REVISION FLOW                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  [Packager] ──отправка──▶ [Клиент] ──получил──▶ [Ревью]                    │
│                                                    │                        │
│                              ┌────────────────────┴────────────────────┐   │
│                              ▼                                         ▼   │
│                         ✅ APPROVED                              ❌ REVISION │
│                              │                                         │   │
│                              ▼                                         │   │
│                    [Milestone Released]                                │   │
│                    [Проект закрыт]                                     │   │
│                                                                        ▼   │
│                                                           ┌─────────────┐  │
│                                                           │ Парсинг     │  │
│                                                           │ фидбека     │  │
│                                                           └──────┬──────┘  │
│                                                                  │         │
│                              ┌───────────────────────────────────┴───┐     │
│                              ▼                   ▼                   ▼     │
│                         [Minor]             [Major]             [Scope]    │
│                       Мелкие баги       Переделать часть      Out of Scope │
│                              │                   │                   │     │
│                              ▼                   ▼                   ▼     │
│                      Dev Agent          Planner Agent        HITL Alert    │
│                      Auto-fix           Re-decompose         Human решает  │
│                              │                   │                         │
│                              └───────────────────┘                         │
│                                        │                                   │
│                                        ▼                                   │
│                               [Critic Agent]                               │
│                                        │                                   │
│                                        ▼                                   │
│                               [HITL Final Review]                          │
│                                        │                                   │
│                                        ▼                                   │
│                               [Packager] ──▶ [Клиент] (цикл повторяется)  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Классификация правок

| Тип | Примеры | Обработка | HITL |
|-----|---------|-----------|------|
| **Minor** | "Поменяй цвет кнопки", "Опечатка в тексте" | Dev/Content Agent → Critic → Packager | ❌ Авто |
| **Major** | "Переделай всю главную страницу", "Добавь фичу" | Planner → пересчёт задач → полный цикл | ✅ Да |
| **Scope Creep** | "А ещё добавь админку" (не было в ТЗ) | HITL Alert → человек обсуждает с клиентом | ✅ Обязательно |
| **Unclear** | "Мне не нравится общий вид" | HITL → уточняющие вопросы клиенту | ✅ Обязательно |

### Лимит ревизий

```python
MAX_REVISIONS = 3  # После 3 ревизий — HITL Alert

class RevisionTracker:
    def on_revision_request(self, project_id: str, feedback: str):
        count = self.get_revision_count(project_id)
        
        if count >= MAX_REVISIONS:
            self.notify_human(
                f"⚠️ Проект {project_id} превысил лимит ревизий ({count})!\n"
                f"Фидбек: {feedback}\n"
                "Action: Переговоры с клиентом о scope/оплате?"
            )
            return "HITL_REQUIRED"
        
        return self.classify_and_route(feedback)
```

---

## ⚠️ Сценарии сбоев и их обработка

### 1. Сбои LLM API

| Сценарий | Детект | Обработка | Fallback |
|----------|--------|-----------|----------|
| **Rate Limit (429)** | HTTP 429 | Exponential backoff (1s → 2s → 4s → 8s) | После 5 попыток → другой LLM |
| **API Timeout** | >30s без ответа | Retry 3x | Gemini ↔ Claude fallback |
| **Context Overflow** | Token limit error | Summarize context, reduce history | Chunk request |
| **API Down** | 503/502 | Alert + queue задачу | Подождать + retry каждые 5 мин |
| **Invalid Response** | Непарсимый JSON | Retry с "Reply ONLY in JSON" | После 3x → HITL |
| **Hallucination** | Critic детектит ложь | Reject + retry с ground truth | HITL если повторяется |

```python
class LLMClient:
    async def call_with_fallback(self, prompt: str):
        providers = [
            ("gemini", self.gemini_client),
            ("claude", self.claude_client),
        ]
        
        for name, client in providers:
            try:
                return await client.generate(prompt)
            except RateLimitError:
                await asyncio.sleep(self.backoff())
            except APIError as e:
                self.log_error(name, e)
                continue
        
        # Все провайдеры недоступны
        await self.alert_human("All LLM providers failed")
        raise CriticalFailure("LLM unavailable")
```

### 2. Сбои браузерной автоматизации

| Сценарий | Детект | Обработка |
|----------|--------|-----------|
| **Captcha** | Детект элемента captcha | HITL → ручное решение → сохранить сессию |
| **2FA Request** | SMS/Email код запрошен | HITL → ввести код → сохранить сессию |
| **Session Expired** | Redirect на login | Auto re-login → если 2FA → HITL |
| **Element Not Found** | Playwright timeout | Screenshot + HITL → возможно сайт изменился |
| **IP Ban** | 403/Cloudflare block | Rotate proxy → если повторяется → HITL |
| **Bot Detection** | Странное поведение сайта | Увеличить delays + more human behavior |
| **Site Redesign** | Селекторы не работают | HITL Alert → нужен редизайн адаптера |

```python
class BrowserAutomation:
    async def safe_action(self, action_func):
        try:
            return await action_func()
        except CaptchaDetected:
            screenshot = await self.page.screenshot()
            await self.request_hitl("Captcha detected", screenshot)
            # Ждём пока человек решит captcha
            await self.wait_for_hitl_resolution()
            return await action_func()  # Retry
        except ElementNotFound as e:
            await self.alert_human(f"UI changed: {e.selector}")
            raise AdapterNeedsUpdate()
```

### 3. Сбои выполнения кода (Dev Agent)

| Сценарий | Детект | Обработка |
|----------|--------|-----------|
| **Syntax Error** | E2B returns parse error | Dev Agent auto-fix → retry |
| **Runtime Error** | Exception в sandbox | Dev Agent debug → max 3 retries → HITL |
| **Infinite Loop** | E2B timeout (30s) | Kill + Dev Agent "fix infinite loop" |
| **Memory Limit** | OOM в sandbox | Reduce data size / optimize |
| **Semgrep Block** | Dangerous pattern | HITL Alert → код требует ревью |
| **Dependency Missing** | Import error | Auto pip install в sandbox |
| **Wrong Output** | Critic rejects | Dev Agent retry с feedback |

```python
class CodeExecutor:
    MAX_RETRIES = 3
    
    async def execute_safely(self, code: str):
        # 1. Semgrep scan
        if issues := await self.semgrep_scan(code):
            if any(i.severity == "ERROR" for i in issues):
                await self.alert_human(f"Dangerous code blocked: {issues}")
                return ExecutionBlocked(issues)
        
        # 2. Execute in sandbox
        for attempt in range(self.MAX_RETRIES):
            try:
                result = await self.sandbox.run(code)
                return result
            except SandboxTimeout:
                code = await self.dev_agent.fix_timeout(code)
            except RuntimeError as e:
                code = await self.dev_agent.fix_error(code, str(e))
        
        return await self.escalate_to_human(code)
```

### 4. Сбои платформ (Upwork, Freelancer, etc.)

| Сценарий | Детект | Обработка |
|----------|--------|-----------|
| **Account Suspended** | Login fails + ban message | 🚨 CRITICAL HITL → создать новый аккаунт |
| **Proposal Rejected** | Error при submit | Log + skip job → alert if repeated |
| **Payment Hold** | Platform warning | HITL → resolve manually |
| **Project Cancelled** | Client cancelled | Graceful stop → archive state |
| **Dispute** | Client opened dispute | 🚨 HITL Alert → human takes over |
| **API Rate Limit** | 429 от API платформы | Queue + backoff |
| **TOS Change** | New terms popup | HITL → review + accept |

### 5. Сбои инфраструктуры

| Сценарий | Детект | Обработка |
|----------|--------|-----------|
| **PostgreSQL Down** | Connection refused | Retry → Telegram alert → auto-restart |
| **Redis Down** | Connection error | Fallback to in-memory queue (temp) |
| **VPS Reboot** | Process died | Systemd auto-restart + state recovery from DB |
| **Disk Full** | Write error | Alert + cleanup logs/temp |
| **OOM Killer** | Process killed | Reduce workers, alert, restart |
| **Network Partition** | Can't reach APIs | Local queue + retry when restored |

```python
class InfrastructureMonitor:
    async def health_check(self):
        checks = {
            "postgres": self.check_postgres,
            "redis": self.check_redis,
            "llm_api": self.check_llm_api,
            "e2b": self.check_e2b,
        }
        
        for name, check_func in checks.items():
            try:
                await check_func()
            except HealthCheckFailed as e:
                await self.alert(f"⚠️ {name} is down: {e}")
                await self.attempt_recovery(name)
```

### 6. Сбои бизнес-логики

| Сценарий | Детект | Обработка |
|----------|--------|-----------|
| **No Matching Jobs** | Scout returns empty | Normal → увеличить интервал → alert if 24h |
| **All Bids Rejected** | 0% win rate за неделю | HITL → пересмотреть стратегию |
| **Client Unresponsive** | Нет ответа 7 дней | Auto follow-up → затем archive |
| **Deadline Approaching** | <24h до дедлайна | 🚨 HITL Alert → приоритезация |
| **Budget Exceeded** | LLM costs > limit | Pause → alert → wait for approval |
| **Low Balance** | Email credits < 100 | Alert → auto-topup если настроен |

### 7. Сбои Geo Scout / Outreach

| Сценарий | Детект | Обработка |
|----------|--------|-----------|
| **Overpass API Limit** | Too many requests | Exponential backoff + cache |
| **No Email Found** | Enrichment failed | Skip lead → mark for manual |
| **Email Bounced** | Bounce notification | Remove from list + update DB |
| **Spam Complaint** | Provider notification | 🚨 CRITICAL → pause all sending → HITL |
| **Domain Blacklisted** | Delivery fails | HITL → change domain / warmup new |
| **GDPR Request** | Unsubscribe + delete | Auto-process + confirm deletion |

---

## 🔔 Система алертов и эскалации

### Уровни алертов

| Уровень | Когда | Куда | Пример |
|---------|-------|------|--------|
| **INFO** | Статистика, прогресс | Dashboard only | "10 jobs scanned" |
| **WARNING** | Требует внимания | Telegram (отложенный) | "LLM rate limit, retrying" |
| **ERROR** | Нужно действие | Telegram (немедленный) | "3 failed bids in a row" |
| **CRITICAL** | Срочно! | Telegram + звонок | "Account suspended" |

```python
class AlertSystem:
    async def send_alert(self, level: AlertLevel, message: str):
        if level == AlertLevel.CRITICAL:
            await self.telegram.send(f"🚨 CRITICAL: {message}")
            await self.phone_call(message)  # Twilio
        elif level == AlertLevel.ERROR:
            await self.telegram.send(f"❌ ERROR: {message}")
        elif level == AlertLevel.WARNING:
            # Batch warnings, send every 30 min
            self.warning_queue.append(message)
```

---

## ⚠️ Важные правила для разработки

1. **ВСЕГДА** используй типизацию (Pydantic, TypedDict)
2. **Docker** для задач >5 мин, E2B только для быстрых тестов
3. **ОБЯЗАТЕЛЬНО** добавляй heartbeat в каждого агента
4. **HITL** обязателен для: отправки откликов, финальной сдачи работы
5. **Semgrep** сканирование перед любым выполнением кода
6. **Логируй** все действия через LangSmith
7. **НЕ** делай auto-submit на Upwork — это ToS violation (бан!)
8. **Email warm-up** 6 недель до production cold outreach

---

## 📧 Email Warm-up Protocol (Pipeline B)

> ⚠️ Без warm-up 90%+ писем попадут в спам!

### Timeline

| Период | Emails/day | Engagement | Действия |
|--------|------------|------------|----------|
| Week 1-2 | 5→10 | 80% opens | Только warm-up сеть |
| Week 3 | 20 | 70% | Первые тесты |
| Week 4 | 30 | 65% | Анализ inbox placement |
| Week 5-6 | 50 | 60% | Production ready |

### Инфраструктура

```yaml
domains:
  - primary: yourbrand.com         # НЕ для cold outreach!
  - outreach_1: out1-yourbrand.com # Warmup отдельно
  - outreach_2: out2-yourbrand.com # Backup
  
dns_records:
  spf: "v=spf1 include:_spf.google.com ~all"
  dkim: enabled
  dmarc: "v=DMARC1; p=quarantine"

warmup_service: "Instantly.ai"  # Встроенный warmup

monitoring:
  - Google Postmaster Tools
  - MXToolbox blacklist check
  - Bounce rate alerts (>2% → pause)
```
