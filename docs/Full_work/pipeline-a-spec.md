# Pipeline A: Единая спецификация (source of truth)

> Объединение видения пользователя + технических деталей из идеала. При противоречии — приоритет у видения.
> Дата: 2026-03-03

---

## Полный flow

```
SCOUT → [HITL] → BID → [HITL #1] → ПЛАТФОРМА → ПАУЗА
→ ДИАЛОГ С КЛИЕНТОМ → [HITL #2] → PLANNER → DEV/DESIGN/CONTENT
→ CRITIC → [HITL #4?] → PACKAGER → [HITL #5] → СДАЧА → RAG SAVE
```

## Phase 1: ПОИСК И ОЦЕНКА

Я открываю Dashboard, нажимаю "Scan Now". Scout сканирует все платформы параллельно.

**Платформы:** FL.ru (RSS), Kwork (scraper), Freelancer (API), Upwork (read-only), Telegram.
Расширение (P3): пагинация Kwork, limit=100 Freelancer, YouDo, Profi.ru.

**Скоринг:** LLM батчами по 10. Для каждого заказа — `score` + `score_reason`.

**Нестандартные заказы:** Scout сам проверяет — есть ли MCP-серверы, API или инструменты для этой задачи. Формирует **capability report** который идёт дальше по цепочке.

**Результат — карточки двух типов:**

| Тип | Score | Метка | AI-пометка | Действия |
|-----|-------|-------|------------|----------|
| А: "Могу сам" | ≥ 0.7 | Зелёная | "могу полностью" или "могу, но тебе придётся X" | Approve → к Bid \| Отказ |
| Б: "Сомневаюсь" | 0.5-0.7 | Жёлтая | что сложно + capability report | В очередь Bid \| Отказ |

**Каждая карточка:** title, platform, budget, стек, AI-пометка, score, ссылка на оригинал заказа.

**Dashboard (страница Jobs):** два таба "Могу сам" / "Нужен разбор". Клик → детальная карточка.

## Phase 2: ПРЕДЛОЖЕНИЕ И ПЕРЕГОВОРЫ

**Генерация предложения (one-shot):**
- RAG: похожие выигранные биды из knowledge_base + **RAG-память** прошлых успешных бидов
- Контекст: job + capability report от Scout
- Структура: Hook → Credibility → Solution → Timeline → CTA
- Выход: `proposal_text`, `bid_amount`, `delivery_days`, `milestones`

**HITL #1 (ОБЯЗАТЕЛЬНЫЙ):** approve / edit / reject предложение. Приходит в Dashboard + TG-бот.

**После approve** → бид отправляется на платформу через адаптер.

**═══════ ПАУЗА: ЖДЁМ ОТВЕТА КЛИЕНТА ═══════**

**Platform message polling:**
- Freelancer API: проверка новых сообщений каждые 5 мин
- FL.ru: scraping inbox
- Kwork: scraping chat
- Telegram: Telethon listener

**Клиент отвечает → AI ведёт диалог автономно:**
- Тон: "сосед, не продавец" — конкретика, без давления, без AI-маркеров
- Считывает контекст всей переписки
- RAG-память: какие аргументы и подходы работали в прошлых переговорах
- Negotiation engine (state machine): initial → qualifying → proposing → negotiating → closing — **детали:** [specs/negotiation-spec.md](specs/negotiation-spec.md)
- Handlers: Q&A, counter-offer, scope change

**Я могу в любой момент подключиться** — зайти в карточку и продолжить диалог сам.

**Спорные моменты → HITL:** контрпредложение по цене, изменение scope, вопрос на который AI не уверен.

**Макс 3-5 параллельных диалогов.**

**Dashboard (Bid Kanban):**

| Backlog | Отправлен | Активный диалог | Подтверждён | Отказ |
|---------|-----------|-----------------|-------------|-------|
| Ждёт HITL | Ждём клиента | AI ведёт переговоры | Готов к Dev | — |

Карточка бида: job title, client, bid_amount, последнее сообщение. Клик → чат-окно (все сообщения: AI + клиент + оператор, поле ввода для оператора, метка кто написал).

**Таблицы БД:**
- `client_messages` (id, bid_id, sender, content, platform_message_id, created_at)
- `negotiations` (id, bid_id, status, state, history_json, updated_at)

**Клиент подтвердил → HITL #2 (ОБЯЗАТЕЛЬНЫЙ):** "Запустить разработку?" Dev-цикл **НИКОГДА** не стартует автоматически.

## Phase 3: ПЛАНИРОВАНИЕ

Planner получает: job + capabilities + bid context + переписку с клиентом + **RAG-память** похожих проектов (сроки, подходы).

**Динамически формирует workflow** — НЕ один фиксированный маршрут:
- Design-only (брендбук, концепт) → Design → Critic
- Dev-only (бот, API, backend) → Dev → Critic
- Full (лендинг, сайт) → Design → Dev → Content → Critic
- Custom (AI-инфографика, etc.) → по анализу Planner'а
- И ЛЮБОЙ другой вариант — Planner не ограничен шаблонами

**Флаги:** `needs_design`, `needs_content`, `needs_dev` → routing по флагам в графе.

**Определяет `delivery_type`:** files / credentials / deploy / instructions / mixed.

Декомпозиция на задачи ≤ 4 часа. Если > 20ч → **HITL #3** (ревью плана).

## Phase 4: РАЗРАБОТКА

**Агенты (Planner выбирает нужных):**
- **Dev** — код (Docker sandbox, Semgrep scan). RAG-память: паттерны решений по типам задач
- **Content** — тексты, копирайтинг
- **Design** — визуал, UI, инфографика (Pencil.dev MCP). Design выполняется ДО Dev

**Dashboard:** очередь заказов — какой где, в каком агенте. Real-time через WebSocket.

**Critic ревью:**
- ≥ 0.85 → к Packager
- 0.60-0.84 minor → назад к `revision_target` (макс 2 попытки)
- 0.60-0.84 major → назад к Planner (re-plan)
- < 0.60 или 2+ ревизий → **HITL #4** (эскалация)

## Phase 5: СДАЧА

**Packager** получает `delivery_type` от Planner и собирает артефакты:

| delivery_type | Что собирает | Что получает клиент |
|---------------|-------------|---------------------|
| `files` | Код, дизайн, контент → архив | ZIP/GitHub repo |
| `credentials` | Настроенный сервис + доступы | Логин/пароль + инструкция |
| `deploy` | Деплой на хостинг клиента | Ссылка + настройки |
| `instructions` | Документация + гайды | PDF/страница с инструкцией |
| `mixed` | Комбинация выше | Пакет: файлы + доступы + инструкция |

**HITL #5 (ОБЯЗАТЕЛЬНЫЙ):** approve / request_changes / reject. Показывает preview того что получит клиент.

После approve → доставка клиенту → Portfolio Agent автосохраняет в портфолио.

## Phase 6: RAG-ПАМЯТЬ (система учится)

После **каждого** завершённого заказа система сохраняет structured record в Experience Store (pgvector):

| Что запоминает | Кто использует | Как |
|---------------|---------------|-----|
| Успешные/неуспешные биды | **Bid Agent** | Similarity search при генерации новых предложений |
| Паттерны переговоров (что конвертило, что отпугивало) | **Negotiation Engine** | Адаптация тона, аргументов, стратегии |
| Технические решения, code snippets | **Dev Agent** | Reuse при похожих задачах |
| Точность оценки сроков (план vs факт) | **Planner** | Коррекция будущих estimates |
| Capability reports + результаты | **Scout** | Расширение знаний о возможностях |

**Инфраструктура:** pgvector уже есть, нужна логика:
1. После HITL #5 approve → `save_experience()` сохраняет record
2. Embedding через OpenAI text-embedding-3-large (3072 dim) — уже в стеке
3. При генерации (bid, plan, dialog) → `retrieve_similar()` → top-K в контекст LLM

---

## Execution Cloaking (маскировка исполнения)

Клиенты оценивают ценность через призму человеко-часов. Чтобы не вызывать подозрение при быстрой сдаче, система имитирует ритм работы человека.

### Двойная эстимация в Bid Agent

Bid Agent никогда не выдаёт клиенту реальные сроки выполнения нейросетью:

1. **Оценка по рынку:** агент рассчитывает, сколько времени задача заняла бы у обычного Middle-разработчика (например, 10 дней).
2. **WOW-оффер:** делит этот срок на 2-3, но никогда не опускается ниже психологического минимума — **24 часа**.
   - Пример: вместо 10 дней → 3-4 дня. Звучит как «профессионал», а не «нейросеть за час».

Промпт для Bid Agent: *"Оценивай задачу по стандартам человека, затем предлагай срок на 30-50% быстрее конкурентов. Никогда не предлагай срок менее 24 часов."*

### Value-based pricing (ценовое позиционирование)

Себестоимость (API-токены) стремится к нулю — но продавать дёшево ошибочно:
- Ценник **на 10-20% ниже рынка** → клиент выбирает нас.
- Берём деньги за «человеческий» объём работы.
- Высокий прайс + быстрые сроки = образ элитного специалиста.
- **Только Fixed Price**, никакой почасовой оплаты.

### Delivery Throttling (искусственная задержка сдачи)

Даже если Dev Agent завершил работу за 45 минут — отдавать результат сразу нельзя. Packager Agent удерживает доставку до истечения минимум 70% заявленного срока.

Система автоматически имитирует рабочий процесс через Scheduled Messages:
- **День 1 (через 2ч после старта):** *"Развернул архитектуру, настраиваю базы данных, приступаю к бэкенду. Возник вопрос по ТЗ: [AI генерирует технический вопрос]"* — создаёт иллюзию плотной ручной работы.
- **День 2:** *"Бэкенд готов, натягиваю фронт. Завтра пришлю на ревью."*
- **День 3 (по плану):** Packager отправляет готовый архив с README.

Технически: в LangGraph добавляется узел `Delay/Scheduler`, который замораживает доставку до нужного timestamp.

### Легенда «собственных наработок»

Если клиент спрашивает, как мы так быстро работаем:
- **Не говорим:** "Я использую ИИ". Клиент решит: "Я тоже так мог, за что плачу?"
- **Говорим:** *"У нас есть проприетарная библиотека компонентов, CLI-инструменты и CI/CD пайплайны, которые мы собирали годами. Это позволяет пропускать рутину и собирать проекты в 3 раза быстрее, фокусируясь на вашей бизнес-логике."*

Технически — это правда. Наш MAS — именно этот инструмент.

---

## Adapter Pattern (модульные платформы)

Чтобы Scout и Bid Agent не зависели от форматов конкретных платформ — система работает через абстрактный `BasePlatformAdapter`. Центральные агенты говорят: «отправь этот текст», а как именно — решает адаптер.

### Интерфейс BasePlatformAdapter

```python
class BasePlatformAdapter:
    def fetch_new_jobs(self) -> list[dict]:
        """Возвращает унифицированный список лидов."""

    def submit_proposal(self, job_id: str, text: str, price: float) -> bool:
        """Отправляет отклик на платформу."""

    def get_client_messages(self, job_id: str) -> list[dict]:
        """Проверяет ответы клиента."""

    def send_message(self, job_id: str, text: str) -> bool:
        """Пишет в чат с клиентом."""
```

### Адаптеры (текущие и планируемые)

| Адаптер | Метод | Особенности |
|---------|-------|-------------|
| `FreelancerApiAdapter` | Official REST API | Быстро, дёшево, но лимиты платформы |
| `KworkStealthAdapter` | Playwright Stealth | Браузер через резидентный прокси, посимвольный ввод |
| `FlRuRssAdapter` | RSS + scraping inbox | Только чтение через RSS, scraping для сообщений |
| `TelegramChannelAdapter` | Telethon MTProto | Мониторинг каналов, DM через Telethon |

### Dashboard: Platform Manager

В Dashboard появляется раздел `Platforms Integration` — список платформ как плагины.

Для каждой платформы карточка с настройками:
- **Тумблер [ON / OFF]:** включить/выключить из общего пула сканирования.
- **Connection Mode:**
  - `Official API` — быстро, дёшево, но с лимитами платформы
  - `Stealth Browser (Playwright)` — универсально, имитирует живого человека, требует прокси
  - `RSS / XML feed` — только чтение, для старых или специфических площадок
- **Лимиты:** сколько откликов в день на этой платформе (защита от shadowban).

Кнопка **"Scan All Active"** — параллельный запуск со всех включённых платформ.

### A/B тесты и защита от банов

1. **A/B тесты платформ:** в аналитике видно Win Rate и средний чек по платформам — выбирай лучшую.
2. **Shadowban evasion:** если API биржи начало пессимизировать отклики — переключи режим с `API` на `Stealth Browser` прямо в Dashboard, без изменений в коде агентов.
3. **Лёгкое масштабирование:** новая платформа = новый адаптер ~100 строк. Агенты не трогаются.

---

## Dashboard Home (командный центр)

```
┌─────────────────────────────────────────────────────┐
│  МЕТРИКИ       │  HITL-ВИДЖЕТ                       │
│  Заказы: 12    │  🔴 Bid approve: "React landing"   │
│  Биды: 5       │  🟡 Plan review: "TG-бот"          │
│  В работе: 3   │  🔴 Delivery: "WordPress site"     │
│  Сдано: 47     │  [Approve] [Reject] [Подробнее]    │
├─────────────────┼──────────────────────────────────────┤
│  АГЕНТЫ         │  АКТИВНЫЕ ЗАКАЗЫ                   │
│  Scout: ● scan  │  "React landing"    → Critic ██░░  │
│  Bid: ● диалог  │  "TG-бот"          → Planner █░░░ │
│  Dev: ● coding  │  "WordPress site"   → Packager ███ │
│  Critic: idle   │  "Брендбук"        → Design ██░░  │
└─────────────────┴──────────────────────────────────────┘
```

Быстрые действия прямо с главной + переходы на детальные страницы.

## Сводка HITL-точек

| # | Момент | Обязательна? | Канал |
|---|--------|-------------|-------|
| 1 | **Bid approve** (предложение) | **ДА, всегда** | Dashboard + TG-бот |
| 2 | **Dev launch** (клиент подтвердил) | **ДА, всегда** | Dashboard + TG-бот |
| 3 | Plan review (>20ч) | Условно | Dashboard + TG-бот |
| 4 | Critic escalation | Условно | Dashboard + TG-бот |
| 5 | **Final delivery** | **ДА, всегда** | Dashboard + TG-бот |
| — | Спорный момент в диалоге | Условно (AI решает) | Dashboard + TG-бот |
| — | Portfolio review | После сдачи | Dashboard |

## Приоритеты реализации

**P0 — Без этого не запустить:**
1. Починить Dashboard BLOCKER'ы (`.env`, WebSocket, runner)
2. End-to-end тест: Scout → Bid → HITL → submit
3. Показать `score_reason` в UI карточках

**P1 — Ядро видения:**
4. Пауза после bid submission (ждём клиента)
5. Bid Kanban UI (статусы, колонки)
6. HITL-виджет на Dashboard home
7. Planner dynamic routing (`needs_design`, `needs_content`)
8. Типы сдачи в Packager (`delivery_type` от Planner)

**P2 — AI-диалог + память:**
9. Таблицы `client_messages`, `negotiations`
10. Platform message polling
11. AI negotiation engine
12. Чат-окно в карточке бида
13. RAG Experience Store (save + retrieve)
14. HITL на спорных моментах

**P3 — Расширение:**
15. Scout capability report (MCP/API search)
16. Scout extended coverage (пагинация, YouDo, Profi.ru)
17. Portfolio Agent
18. Design workflow (Pencil.dev MCP)
19. Self-improvement (Capability Registry)

---

## Сценарии переговоров

> Полная спецификация: [specs/negotiation-spec.md](specs/negotiation-spec.md). Ниже — краткое описание сценариев.

### Negotiation State Machine

```
BID SENT → AWAITING RESPONSE → ACCEPTED → PLANNING
                  │
      ┌───────────┼───────────┐
      ▼           ▼           ▼
  QUESTION    COUNTER      REJECTED
              OFFER
      │           │
      ▼           ▼
  HITL         HITL
  REQUIRED     REQUIRED
```

### Сценарий 1: Вопросы клиента (Pre-Hire)

| Триггер | Детекция | Ответ | HITL |
|---------|----------|-------|------|
| Уточняющий вопрос | "can you", "how would", "what if" | Bid Agent генерирует ответ | Опционально |
| Технический вопрос | Термины фреймворков/кода | Dev Agent + Bid Agent | Опционально |
| Вопрос о сроках | "how long", "when", "deadline" | Bid Agent (из оригинального бида) | Нет |
| Запрос портфолио | "portfolio", "examples", "similar" | Авто-ответ со ссылкой | Нет |
| Вопрос о цене | "price", "cost", "budget", "discount" | — | **Всегда HITL** |

**Классификация вопросов:**

```python
QUESTION_TYPES = {
    "clarification": {"hitl_required": False, "handler": "generate_clarification_response"},
    "technical":     {"hitl_required": True,  "handler": "generate_technical_response"},
    "timeline":      {"hitl_required": False, "handler": "generate_timeline_response"},
    "pricing":       {"hitl_required": True,  "handler": None},  # Always HITL
}
```

При `hitl_required=True` генерируется draft-ответ и отправляется на HITL-ревью.

### Сценарий 2: Контрпредложения по цене

**Детекция:** "budget is", "can you do it for", "lower price", "discount" + число < оригинального бида.

**Стратегия (ВСЕГДА через HITL):**

| Разница | Рекомендация | Типовой ответ |
|---------|-------------|---------------|
| ≤ 10% | `ACCEPT_POSSIBLE` | "I can work with that budget. Let's proceed!" |
| 11-25% | `NEGOTIATE` | "Would $X work? I can adjust the timeline slightly." |
| > 25% | `DECLINE_OR_REDUCE_SCOPE` | "That's below my minimum for this scope. I could offer a reduced version." |

**HITL-интерфейс контрпредложения:**

```json
{
  "type": "counter_offer",
  "title": "Client counter offer: $1,500 (originally $2,000)",
  "payload": {
    "original_bid": 2000,
    "counter_amount": 1500,
    "difference_percent": 25,
    "recommendation": "NEGOTIATE",
    "suggested_responses": [
      {"action": "accept", "text": "I can work with $1,500..."},
      {"action": "counter", "text": "Would $1,750 work?", "amount": 1750},
      {"action": "decline", "text": "Unfortunately $1,500 is below my minimum..."}
    ]
  },
  "available_actions": ["accept", "counter", "decline", "custom_response"]
}
```

### Сценарий 3: Изменение Scope (Pre-Hire)

**Детекция:** "also", "additionally", "one more thing" + требование, отсутствующее в оригинальном ТЗ.

**Обработка:**
1. LLM сравнивает новое сообщение с оригинальными требованиями
2. При обнаружении scope change — оценивает дополнительную стоимость и сроки
3. Генерирует варианты ответов → HITL

**Варианты ответов:**
- "Happy to include that! It would add approximately $X and Y days."
- "Great addition! Let's discuss this as a Phase 2 after the initial delivery."
- "That's definitely possible. Want me to revise the proposal with this included?"

### Сценарий 4: Молчание клиента (No Response)

**Автоматические follow-up:**

| Дней без ответа | Действие | HITL |
|-----------------|----------|------|
| 2 | Мягкое напоминание | Авто |
| 5 | Второй follow-up | Авто |
| 10 | Финальный check-in | Авто |
| 14 | Архивировать как "No Response" | Авто |

**Шаблоны:**
- **2 дня:** "Hi! Just checking in on this. Let me know if you have any questions!"
- **5 дней:** "Wanted to follow up on my proposal. I'm still available and interested."
- **10 дней:** "Final check-in — I'll assume you've moved in a different direction if I don't hear back."

Follow-up отменяется автоматически, если клиент ответил (статус бида ≠ `awaiting_response`).

### Error Handling в переговорах

**Platform Account Issues:**

| Проблема | Детекция | Действие |
|----------|----------|----------|
| Account suspended | Login fails + ban message | CRITICAL HITL |
| Rate limited | 429 response | Пауза 1 час + уведомление |
| Session expired | Redirect to login | Авто re-login |
| 2FA required | 2FA prompt | HITL для ввода кода |

**Project Execution Issues:**

| Проблема | Детекция | Действие |
|----------|----------|----------|
| Клиент молчит (в проекте) | Нет ответа 5+ дней | Auto follow-up → HITL |
| Deadline risk | <24ч осталось, <80% готово | URGENT HITL |
| Превышен лимит ревизий | revision_count >= 2 | HITL (обсудить с клиентом) |
| Баг-репорты от клиента | Negative feedback keywords | Приоритетный fix → HITL если сложный |

### Capacity Management

Лимит параллельных проектов: Phase 1 = 5, Phase 2 = 10, Phase 3+ = 15-20.

При выигрыше бида, когда лимит достигнут — HITL с вариантами:
- Accept anyway (overload)
- Negotiate delayed start
- Decline politely

Минимальный порог качества: `MIN_QUALITY_THRESHOLD = 0.85`. Если средний quality score за 30 дней ниже порога — новые проекты блокируются до восстановления.

### Communication Analytics

| Метрика | Описание | Целевое значение |
|---------|----------|-----------------|
| Response Time | Время ответа клиенту | < 2 часов |
| Conversion Rate | Вопросы → нанят | > 60% |
| Counter Accept Rate | Контрпредложения приняты | < 30% |
| Follow-up Effectiveness | Ответы после follow-up | > 15% |
| Scope Creep Rate | Проекты с расширением scope | Только tracking |

### Таблицы БД для переговоров

```sql
-- Все сообщения с клиентами
CREATE TABLE client_messages (
    id              UUID PRIMARY KEY,
    bid_id          UUID REFERENCES bids(id),
    project_id      UUID REFERENCES projects(id),
    direction       VARCHAR(10) NOT NULL,  -- 'inbound', 'outbound'
    message_type    VARCHAR(30),           -- 'question', 'counter_offer', 'scope_change', 'general'
    content         TEXT NOT NULL,
    platform        VARCHAR(50),
    external_id     VARCHAR(255),          -- platform message ID
    auto_generated  BOOLEAN DEFAULT FALSE,
    hitl_reviewed   BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- История переговоров
CREATE TABLE negotiations (
    id              UUID PRIMARY KEY,
    bid_id          UUID REFERENCES bids(id),
    original_amount DECIMAL(10,2),
    final_amount    DECIMAL(10,2),
    rounds          INTEGER DEFAULT 0,
    outcome         VARCHAR(20),           -- 'accepted', 'declined', 'scope_adjusted'
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE
);
```

---

## Ключевые файлы

| Компонент | Файл |
|-----------|------|
| Scout Agent | `src/agents/scout.py` |
| Bid Agent | `src/agents/bid.py` |
| Planner | `src/agents/planner.py` |
| Dev/Content/Design/Critic/Packager | `src/agents/*.py` |
| Graph (pipeline) | `src/core/graph.py` |
| State model | `src/core/state.py` |
| HITL API | `src/api/routes/hitl.py` |
| Orchestrator | `src/api/services/orchestrator.py` |
| Dashboard Jobs | `dashboard/app/routes/_app.jobs.tsx` |
| Dashboard Job Detail | `dashboard/app/routes/_app.jobs.$id.tsx` |
| Dashboard HITL | `dashboard/app/routes/_app.hitl.tsx` |
| Dashboard Home | `dashboard/app/routes/_app.tsx` |
| Negotiation design (doc) | `docs/negotiation_flows.md` |
| Self-improvement design (doc) | `docs/Full_work/archive/self-improvement-agent.md` |
| Portfolio Agent design (doc) | `docs/Full_work/portfolio/portfolio-agent.md` |
| Design workflow design (doc) | `docs/Full_work/archive/design-workflow.md` |
