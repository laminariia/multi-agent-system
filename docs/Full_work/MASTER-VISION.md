# MASTER-VISION: MAS — Мульти-Агентная Система

> **Статус:** Абсолютный закон для будущей разработки.
> **Версия:** 1.0 | Дата: 2026-03-07
> **Цель:** Единый непротиворечивый документ, описывающий чистое видение продукта.
> Все конфликты между предыдущими документами разрешены в Секции I.

---

# СЕКЦИЯ I: РАЗРЕШЁННЫЕ КОНФЛИКТЫ ДОКУМЕНТАЦИИ

> Все 31 конфликтов найдены при Cross-Document Audit 24 документов в `docs/Full_work/`.
> Для каждого: что было, что решено, почему.

## I.1 Количество агентов

| Источник | Утверждение |
|----------|-------------|
| MAS.md заголовок | "11 ИИ-агентов" |
| MAS.md таблица | 12 строк (#1-#12) |
| agents-spec.md | 10 агентов (без SalesAgent, Portfolio) |
| CLAUDE.md | "10 ИИ-агентов" |

**Решение:** **10 реализованных + 2 проектируемых = 12 в реестре.**
- Агенты 1-10: Scout, Bid, Planner, Dev, Content, Design, Critic, Packager, GeoScout, Outreach — **[IMPLEMENTED]**
- Агент 11: SalesAgent — **[PLANNED]** (спецификация готова в `specs/sales-agent-spec.md`, код не написан)
- Агент 12: Portfolio Agent — **[PLANNED]** (дизайн утверждён, код не написан)

## I.2 LLM-модели: 4 версии правды

| Источник | Scout | Design | Critic |
|----------|-------|--------|--------|
| CLAUDE.md | "Gemini 3 Flash" | "Gemini 3 Pro" | Claude Sonnet 4.5 |
| MAS.md | DeepSeek V3.2 | Gemini 3 Pro | Claude Sonnet 4.5 |
| agents-spec.md | DeepSeek V3.2 | Claude Sonnet 4.5 | Claude Sonnet 4.5 |
| llm-spec.md / TECH_STACK.md | DeepSeek V3.2 | NanoBanana Pro | Claude Sonnet 4.5 |

**Решение:** **MASTER-VISION.md Section 4 = Single Source of Truth** для LLM assignments (6-tier система).
- Scout: **Gemini 2.5 Flash** (Tier 5, extraction — дешёвый batch-scoring)
- Bid: **Gemini 3.1 Pro** (Tier 2, client-facing — качество заявки)
- Design: **NanoBanana Pro** (Tier 4, визуальные задачи)
- Critic: **Claude Sonnet 4.6** (Tier 3, content+review)
- Content: **Claude Sonnet 4.6** (Tier 3)
- Outreach: **Gemini 3.1 Pro** (Tier 2, client-facing)
- Packager: **DeepSeek V3.2** (Tier 6, simple)
- Все предыдущие fallback-модели (Grok 4.1 Fast, Qwen3-4B, GPT-5.2 Codex, Qwen3-Coder-Next) удалены — только реальные модели на OpenRouter

## I.3 Количество платформ

| Источник | Утверждение |
|----------|-------------|
| CLAUDE.md | "4 платформы" |
| MAS.md | 4 (Freelancer, FL.ru, Kwork, Upwork) |
| platform-adapters-spec.md | 6 (+ Telegram, Fiverr) |
| Код (`VALID_PLATFORMS`) | 6 (включая fiverr) |

**Решение:** **6 платформ: 5 активных + 1 запланированная.**
- Freelancer.com (API) — активна
- FL.ru (RSS) — активна
- Kwork (Playwright scraper) — активна
- Upwork (Playwright, READ-ONLY) — активна
- Telegram Channels (Valkey queue) — активна
- Fiverr (Playwright scraper) — **не реализована**, адаптер в планах

## I.4 HITL Pipeline B: 3 vs 6 точек

| Источник | Утверждение |
|----------|-------------|
| MAS.md | 6 HITL точек Pipeline B |
| hitl-spec.md | 3 точки (lead_card, concept_review, email_approval) |
| sales-agent-spec.md | 3 SalesAgent + 3 Design = 6 |

**Решение:** **6 HITL точек Pipeline B** (MAS.md корректен).
hitl-spec.md описывал только 3 из 6 — пропущены design_review, design_client, concept_approved. Полный список:
1. `lead_card` — lead обнаружен (mandatory hot/warm)
2. `outreach_approval` — перед отправкой email/DM (mandatory)
3. `concept_review` — SalesAgent сгенерировал концепцию (mandatory)
4. `concept_approved` — клиент согласился (mandatory)
5. `design_review` — Design Agent итерация (mandatory, max 3 раунда)
6. `design_client` — финальный макет клиенту (mandatory)

## I.5 Viewport: фиксированный vs рандомизированный

| Источник | Утверждение |
|----------|-------------|
| platform-adapters-spec (StealthConfig) | 1920×1080 фиксированный |
| security-spec (Anti-Detection) | 1280-1400 × 800-900 рандомизация |

**Решение:** **Рандомизация 1280-1920 × 800-1080.**
Фиксированный viewport = fingerprinting сигнал. Security-spec корректнее, но диапазон расширен до разумных десктопных разрешений.

## I.6 Cookie Valkey keys и TTL

| Источник | Key | TTL |
|----------|-----|-----|
| platform-adapters-spec (SessionManager) | `browser:session:{platform}:cookies` | 72 часа |
| security-spec (Cookie Encryption) | `browser:cookies:{session_name}` | 7 дней |

**Решение:** **`browser:session:{platform}:cookies`, TTL = 72 часа.**
72 часа — оптимальный баланс между частотой перелогина и защитой от stale-сессий. Key layout из platform-adapters-spec более структурирован (включает platform в ключ).

## I.7 Email warm-up: расхождение по числам

| Источник | Неделя 1 | 2 | 3 | 4 | 5 | 6 |
|----------|:--------:|:-:|:-:|:-:|:-:|:-:|
| enrichment-spec | 5 | 10 | 15 | 25 | 35 | 50 |
| legal-compliance-spec | 5 | 10 | 20 | 50 | 100 | — |

**Решение:** **Консервативный план из enrichment-spec: 5→10→15→25→35→50.**
Агрессивное наращивание до 100/день (legal-spec) рискует спровоцировать bounce rate > 5%. Потолок 50 писем/день совпадает с `MAX_EMAILS_PER_DAY=50` в коде.

## I.8 Upwork RPM: 60 vs 3-6

| Источник | Утверждение |
|----------|-------------|
| api-spec (Rate Limiting Layer 2) | 60 req/min |
| platform-adapters-spec (AdaptiveRateLimiter) | initial 3, min 1, max 6 RPM |

**Решение:** **3-6 RPM (AdaptiveRateLimiter).**
Upwork = Playwright scraping с агрессивной anti-bot защитой. 60 RPM — гарантированный бан. api-spec описывал теоретический API лимит, не scraping reality.

## I.9 Email лимит: 50/день vs 100/час

| Источник | Утверждение |
|----------|-------------|
| enrichment-spec (EmailSender) | `MAX_EMAILS_PER_DAY = 50` |
| api-spec (Business Targets) | "до 100/hour" |

**Решение:** **50 писем/день (enrichment-spec).**
api-spec описывал aspirational target "после полного warm-up + выделенный SMTP + multiple domains". Для MVP с одним доменом — 50/день = потолок. Масштабирование через дополнительные домены.

## I.10 HITL авторизация: role-based vs linked-account

| Источник | Утверждение |
|----------|-------------|
| api-spec | "HITL actions требуют роль owner" |
| telegram-bot-spec | "не role-based, только linked account" |

**Решение:** **Двойная проверка: linked_account + role ≥ moderator.**
- Dashboard API: JWT → role check (owner, co_owner, moderator)
- Telegram Bot: linked_account (telegram_chat_id → User) + проверка role на бэкенде
- viewer не может resolve HITL ни через Dashboard, ни через Telegram

## I.11 JWT Token Expiry

| Источник | Утверждение |
|----------|-------------|
| security-spec | configurable через Settings |
| api-spec | `timedelta(hours=24)` hardcoded |

**Решение:** **Configurable через Settings, default 24h access / 7d refresh.**
- `JWT_ACCESS_TOKEN_EXPIRE_HOURS = 24`
- `JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7`
- Settings fallback к defaults при отсутствии env var

## I.12 Bounce rate target

| Источник | Утверждение |
|----------|-------------|
| enrichment-spec | target <3%, красная линия >5% |
| legal-compliance-spec | target <2% |

**Решение:** **Target <2%, alert >3%, hard stop >5%.**
Legal более строгий — принимаем его как нижнюю границу. 3-этапная эскалация обеспечивает реакцию до попадания в blacklist.

## I.13 Phantom-агент WebScout

| Источник | Утверждение |
|----------|-------------|
| pipeline-b-spec | WebScout: "2GIS, Яндекс.Бизнес, VK, Instagram, Google Search API" |
| agents-spec | Нет агента WebScout |
| Код | Нет `src/agents/web_scout.py` |

**Решение:** **WebScout — будущее расширение GeoScout, не отдельный агент.**
WebScout — набор дополнительных data sources для GeoScout (2GIS API, Яндекс.Карты, VK API). Реализация: расширение `GeoScoutAgent.allowed_tools` новыми инструментами, не отдельный агент.

## I.14 Delivery Agent: gap в конце Pipeline A

| Источник | Утверждение |
|----------|-------------|
| MAS.md диаграмма | "Packager → [HITL] → Delivery" |
| pipeline-a-spec / agents-spec | Packager → HITL → submit delivery через Platform Adapter |

**Решение:** **Delivery = функция Packager, не отдельный агент.**
После HITL approve, Packager вызывает Platform Adapter для доставки результата клиенту. Отдельный Delivery Agent не нужен — это API-вызов, не LLM-задача.

## I.15 Knowledge Base таблица: расхождение schema

| Источник | Таблица |
|----------|---------|
| database-spec | `knowledge_base` (type, category, title, content, embedding) |
| rag-memory-spec | `experience_store` (project_id, domain, patterns, success_score) |

**Решение:** **Единая таблица `knowledge_base`** с расширенными полями.
`experience_store` — это подмножество knowledge_base с `type='experience'`. Не нужна отдельная таблица.

## I.16 Checkpoint таблица: schema mismatch

| Источник | PK |
|----------|-----|
| database-spec | `(thread_id, checkpoint_id)` composite |
| infrastructure-spec | `HybridCheckpointSaver` с `thread_id` + `mas_checkpoint_id` |

**Решение:** **PK = `(thread_id, checkpoint_id)`** — стандарт LangGraph.
`mas_checkpoint_id` — это alias для `checkpoint_id` в state dict (workaround reserved name bug). В таблице колонка называется `checkpoint_id`.

## I.17-I.31 Мелкие конфликты (резюме)

| # | Конфликт | Решение |
|---|----------|---------|
| 17 | Orchestrator: "6 endpoints" в тексте, 9 в списке | **9 endpoints** (текст ошибочен) |
| 18 | Session TTL: 72h vs 7d | **72 часа** (см. I.6) |
| 19 | SalesAgent pipeline: on-graph vs off-graph | **Off-graph** (cron-based APScheduler, отдельный thread_id) |
| 20 | Negotiation: часть Bid vs отдельная подсистема | **Отдельная** (off-graph state machine, 9 состояний) |
| 21 | RAG write-back: documented but not coded | **Дизайн утверждён**, stub в Packager, полная реализация — Phase 2 |
| 22 | Design Agent в sequence: параллельно vs последовательно | **Последовательно** перед Dev (Design → Dev → Content) |
| 23 | Semantic Cache TTL разброс | Единая таблица: Scout 6h, Planner 1h, Dev 1h, Content 12h, Design 12h |
| 24 | Never-cache list неполный | **Never cache:** Bid, Critic, Packager, SalesAgent, Negotiation |
| 25 | GeoScout LLM "не используется" vs agents-spec DeepSeek | **LLM не используется** (pure data pipeline), DeepSeek — fallback |
| 26 | Portfolio Agent: #11 vs #12 | **#12** (SalesAgent = #11) |
| 27 | Agent state `next_agent` vs dynamic routing | **Dynamic routing** via `_route_next_in_sequence()`, `next_agent` только для inter-pipeline |
| 28 | Max state size: 512KB vs unlimited | **512KB** (`MAX_STATE_SIZE_BYTES = 524288`) |
| 29 | Heartbeat: 90s interval / 180s timeout vs other values | **90s/180s** (canonical from agents-spec) |
| 30 | Enrichment order: OSINT→Hunter→Apollo vs reversed | **OSINT→Hunter→Apollo** (дешёвые первые) |
| 31 | Email warm-up bounce target: <3% vs <2% | **<2%** (см. I.12) |

---

# СЕКЦИЯ II: THE CLEAN FINAL VISION

---

## 1. Executive Summary

**MAS** — автономное цифровое агентство из **12 ИИ-агентов**, работающих в **3 пайплайнах** с **15 HITL-точками**. Находит заказы на 6 платформах, ведёт переговоры, выполняет работу (код, контент, дизайн), сдаёт результат — под контролем оператора.

### Ключевые числа

| Метрика | Значение |
|---------|----------|
| Агентов (реализовано / проект) | 10 / 2 |
| Платформ фриланса | 6 (5 активных + 1 planned) |
| HITL-точек | 15 (9 mandatory, 6 conditional) |
| LLM-стоимость/мес (24/7) | ~$391 |
| Тестов | 2435+ (0 failed) |
| API endpoints | 62+ |
| DB таблиц | 20+ |
| Telegram-каналов мониторинга | 22 |

### Философия

1. **AI делает 80%+ работы** — от поиска до сдачи
2. **HITL = safety net** — человек одобряет критические шаги (бид, запуск разработки, доставка)
3. **Execution Cloaking** — клиент видит "команду разработчиков", не AI-систему
4. **Сосед, не продавец** — тон коммуникации: конкретика, польза, без давления
5. **Never auto-submit on Upwork** — ToS compliance первична

---

## 2. Архитектура: 3 пайплайна

```
┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE A: Биржи фриланса                                        │
│  Scout → Bid → [HITL] → Negotiation → [HITL dev_launch] →          │
│  → Dev Cycle Engine                                                  │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE B: Прямые продажи                                         │
│  GeoScout → Outreach → [HITL] → SalesAgent → [HITL concept] →      │
│  → Design → [HITL design] → Dev Cycle Engine                        │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DEV CYCLE ENGINE (общая фабрика исполнения)                        │
│  Planner → Dynamic Sequence [Design→Dev→Content] → Critic →        │
│  → [HITL revision?] → Packager → [HITL final_review] →             │
│  → Platform Delivery → RAG Experience Store                         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE C: Client Growth (ДИЗАЙН, не реализован)                  │
│  Auto-Monitor → Snapshot Report → Upsell Recommender → [HITL] →    │
│  → Managed Services / Новый проект                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Стек технологий

| Слой | Технология | Примечание |
|------|------------|------------|
| Оркестрация | LangGraph 1.0 | StateGraph(dict), astream not ainvoke |
| Backend | **Litestar** + Python 3.12 | НЕ FastAPI |
| Database | PostgreSQL 16 + pgvector | HNSW индексы (pgvector ANN) |
| Cache | **Valkey 8.1** | Redis-compatible, BSD-3, `VALKEY_URL` |
| Browser | Playwright + playwright_stealth | StealthBrowser, BrowserPool |
| Frontend | Remix + shadcn/ui | Dashboard |
| LLM Router | OpenRouter | Единый API для всех LLM |
| Embeddings | OpenAI text-embedding-3-large | 3072 dim, direct API |
| Deploy | Railway (Docker) | API + Dashboard = 2 сервиса |
| Proxy | BrightData Residential | brd.superproxy.io:22225 |
| Monitoring | Prometheus + Grafana | /metrics endpoint |

---

## 4. Реестр агентов

### 4.1 Полная таблица

| # | Агент | Pipeline | LLM Primary | Tier | Temp | Статус | Роль |
|---|-------|----------|-------------|:----:|:----:|:------:|------|
| 1 | Scout | A | Gemini 2.5 Flash | 5 | 0.2 | [IMPL] | Поиск заказов на 5 платформах, LLM-скоринг 0.0-1.0 |
| 2 | Bid | A | Gemini 3.1 Pro | 2 | 0.7 | [IMPL] | Генерация 5-частной заявки, RAG, HITL mandatory |
| 3 | Planner | Dev Cycle | Claude Opus 4.6 | 1 | 0.3 | [IMPL] | Декомпозиция на задачи ≤4ч, dynamic routing |
| 4 | Dev | Dev Cycle | Opus 4.6 (30%) / Sonnet 4.6 (70%) | 1/3 | 0.3 | [IMPL] | Генерация кода, Semgrep, sandbox exec |
| 5 | Content | Dev Cycle | Claude Sonnet 4.6 | 3 | 0.7 | [IMPL] | Тексты, документация, переводы |
| 6 | Design | Dev Cycle | NanoBanana Pro | 4 | 0.7 | [IMPL] | UI/UX спеки (JSON, не изображения) |
| 7 | Critic | Dev Cycle | Claude Sonnet 4.6 | 3 | 0.2 | [IMPL] | Semgrep + LLM-ревью, quality gate |
| 8 | Packager | Dev Cycle | DeepSeek V3.2 | 6 | auto | [IMPL] | Финальная упаковка, HITL mandatory |
| 9 | GeoScout | B | Gemini 2.5 Flash | 5 | — | [IMPL] | H3 гексы, Overpass API, lead discovery (no LLM in MVP) |
| 10 | Outreach | B | Gemini 3.1 Pro | 2 | 0.7 | [IMPL] | Email/Telegram DM черновики |
| 11 | SalesAgent | B | Claude Opus 4.6 | 1 | 0.7 | [PLAN] | Multi-turn переговоры, concept generation |
| 12 | Portfolio Agent | — | DeepSeek V3.2 | 6 | auto | [PLAN] | Автозаполнение портфолио |

**LLM Tier System:**
| Tier | Роль | Модель | Агенты |
|:----:|------|--------|--------|
| 1 | Reasoning | Claude Opus 4.6 | Planner, Dev (complex), SalesAgent |
| 2 | Client-facing | Gemini 3.1 Pro | Bid, Outreach |
| 3 | Content+Review | Claude Sonnet 4.6 | Content, Dev (standard), Critic |
| 4 | Design | NanoBanana Pro | Design |
| 5 | Extraction | Gemini 2.5 Flash | Scout, GeoScout |
| 6 | Simple | DeepSeek V3.2 | Packager, Portfolio Agent |

### 4.2 LLM-стоимость ($391/мес при 24/7)

| Агент | Стоимость/мес | Доля |
|-------|:------------:|:----:|
| Critic | $176 | 45% |
| Dev (standard) | $57 | 15% |
| Planner | $41 | 10% |
| Dev (complex) | $41 | 10% |
| Design | $30 | 8% |
| Scout | $14 | 4% |
| Outreach | $11 | 3% |
| Bid | $6 | 2% |
| GeoScout | $6 | 2% |
| Embeddings | $5 | 1% |
| Content | $3 | <1% |
| Packager | $1 | <1% |

### 4.3 Контракт агента (обязательный паттерн)

Каждый агент следует единому шаблону:

```python
class XxxAgent(ConstrainedAgent):
    def __init__(self):
        super().__init__(
            agent_name="xxx",
            allowed_tools=[...],
            llm_client=LLMClient(...),
            heartbeat=HeartbeatMonitor(interval=90, timeout=180),
            loop_detector=LoopDetector(max_iterations=50, max_identical_steps=5),
        )

    async def _execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # ... agent logic ...
        return update_state(state,
            current_agent="xxx",
            artifacts={"xxx": [serialized_output]},
            status="active",  # or "paused" for HITL
        )

# Module-level node for LangGraph
async def xxx_node(state: dict[str, Any]) -> dict[str, Any]:
    agent = XxxAgent()
    return await agent.run(state)
```

**Критические инварианты:**
- `state: dict[str, Any]` — НИКОГДА `AgentState` TypedDict (баг LangGraph 1.0.8)
- `update_state()` — НИКОГДА прямая мутация dict
- `requires_hitl=True` — ОБЯЗАТЕЛЬНО для Bid, Packager, dev_launch
- Heartbeat: 90s interval, 180s timeout, max 3 restarts

---

## 5. Pipeline A: Биржи фриланса

### 5.1 Полный Flow

```
SCOUT ──── Scan 5 platforms ────────────────────────────────────
  │         Freelancer.com (API, max 100)
  │         FL.ru (RSS, category feeds)
  │         Kwork (Playwright scraper)
  │         Upwork (Playwright, READ-ONLY)
  │         Telegram (Valkey queue, 22 каналов)
  │
  ├── LLM Batch Scoring (0.0-1.0)
  │     Budget filter: $20-$5,000
  │     Auto-reject: mobile apps, AI/ML, blockchain, ERP
  │     Auto-reject: client <3 reviews OR <50% hire rate
  │
  ├── score ≥ 0.7 ──────────► BID AGENT
  ├── 0.5 ≤ score < 0.7 ──► [HITL #1: job_review] ──► approve? → BID
  └── score < 0.5 ──────────► disqualify
                                    │
BID AGENT ── Generate 5-part proposal ──────────────────────────
  │  1. Hook (attention grabber)
  │  2. Credibility (relevant experience, RAG)
  │  3. Solution (technical approach)
  │  4. Timeline (milestones, deliverables)
  │  5. CTA (call to action)
  │
  │  Pricing: 10-20% below market (first 10: extra -10-15%)
  │  RAG: knowledge_base + semantic_cache for similar past bids
  │
  └── [HITL #2: bid_approval] ── MANDATORY ──────────────────────
        Actions: approve / edit / reject / skip / later
        approve → Platform Adapter → submit bid
                                    │
NEGOTIATION ENGINE ── Off-graph (APScheduler cron) ────────────
  │  State Machine: initial → qualifying → proposing →
  │                  negotiating → closing → ACCEPTED/REJECTED
  │
  │  Message Classification:
  │    Clarification → AI auto-response
  │    Technical     → AI + Bid Agent
  │    Timeline      → auto from original bid
  │    Portfolio     → auto with link
  │    PRICE         → ALWAYS HITL
  │    Scope change  → AI + optional HITL
  │
  │  Counter-Offer Logic:
  │    ≤10% diff    → accept possible
  │    11-25% diff  → negotiate (adjust timeline)
  │    >25% diff    → decline or reduce scope
  │
  │  [HITL #3: negotiation_dispute] ── CONDITIONAL ──────────
  │    Trigger: price counter >25%, scope change, AI uncertain
  │
  └── CLIENT ACCEPTS
                                    │
[HITL #4: dev_launch] ── MANDATORY ─────────────────────────────
  │  "Start development?" → operator decides
  │  approve → new thread_id → PLANNER
  │
  └── DEV CYCLE ENGINE (see Section 7)
```

### 5.2 Platform Adapters

| Платформа | Тип | Rate Limit (RPM) | Submit Bid? | Block Detection |
|-----------|-----|:-----------------:|:-----------:|-----------------|
| Freelancer.com | REST API | 15 init, 2-30 adaptive | Да (API, после HITL) | 429→rate_limit, 403→ban |
| FL.ru | RSS Feed | 5 init, 1-10 adaptive | Нет (ручной) | — |
| Kwork | Playwright | 3 init, 1-6 adaptive | Нет (ручной) | Captcha, Cloudflare, `.blocked-user` |
| Upwork | Playwright | 3 init, 1-6 adaptive | **ЗАПРЕЩЕНО** (ToS) | Captcha, CF, login redirect, `.account-suspended` |
| Telegram | Valkey Queue | 30 init, 5-60 adaptive | N/A | — |
| Fiverr | Playwright (planned) | TBD | Нет (ручной) | CF + device fingerprinting |

### 5.3 Adaptive Rate Limiter

```
Success x5 подряд → rate × 1.1
429 / timeout     → rate × 0.5
Captcha / ban     → min_rpm + 30мин пауза + Telegram alert
```

Persistence: Valkey `mas:rate_limiter:{platform}` HSET, TTL=24h.

### 5.4 Circuit Breaker

```
CLOSED ──(5 failures)──► OPEN ──(300s cooldown)──► HALF_OPEN ──(2 success)──► CLOSED
                                                        └──(failure)──► OPEN
```

State в Valkey: `circuit:{platform}`. Dashboard: CLOSED=зелёный, OPEN=красный, HALF_OPEN=жёлтый.

### 5.5 Execution Cloaking

Клиент НИКОГДА не узнаёт об AI:

1. **Planner** рассчитывает `real_hours` (AI-скорость)
2. **Bid Agent** предлагает `human_hours = real_hours × 2-3` (но ≥24ч)
3. **Packager** throttles delivery: `min_delivery_at = bid_creation + (proposed_days × 0.7)`
4. **Scheduled Messages**: День 1, 2, ... — LLM-генерированные апдейты ("прогресс")
5. **Легенда**: "У нас собственные компонентные библиотеки и CI/CD пайплайны"

---

## 6. Pipeline B: Прямые продажи

### 6.1 Полный Flow

```
GEOSCOUT ── Spatial scanning ───────────────────────────────────
  │  City → Nominatim geocoding → BoundingBox
  │  → H3 hexagons (resolution 8, ~461м edge, max 50 per scan)
  │  → Overpass API (10 amenities + 12 shops)
  │  → Filter: НЕТ website/contact:website tag, ЕСТЬ name tag
  │  → Dedup: osm_id (in-memory set + DB unique)
  │  → leads table (status="new")
  │
  │  GeoScout НЕ использует LLM — чистая data pipeline.
  │
ENRICHMENT WATERFALL ── Cascade cheap→expensive ────────────────
  │  1. OSINT (DuckDuckGo, $0.00) → email regex, confidence 0.4-0.5
  │  2. Hunter.io ($0.01/req) → domain-search, confidence 0-1.0
  │  3. Apollo.io ($0.05/req) → people search, confidence 0.75
  │  Stop on first email found.
  │
OUTREACH AGENT ── Draft messages ───────────────────────────────
  │  Channel selection:
  │    telegram_username? → Telegram DM
  │    else              → Email
  │
  │  Тон: "сосед, не продавец" (7 принципов)
  │  Email: subject ≤60 символов, body ≤120-180 слов
  │  Telegram: ≤60 слов, без ссылок, "ты" вместо "вы"
  │
  │  [HITL #1: outreach_approval] ── MANDATORY ────────────────
  │    Actions: approve all / edit / reject individual
  │
SALESAGENT ── Multi-turn conversation (off-graph) ──────────────
  │  Stage 1: First contact (discovery tone)
  │  Stage 2: Discovery (needs, pain points)
  │  Stage 3: Analysis (market insights, competitor OSINT)
  │  Stage 4: Concept generation (project proposal)
  │  Stage 5: Negotiation (adapt, refine, agree)
  │
  │  [HITL #2: concept_review] ── MANDATORY ─────────────────
  │    AI concept + draft message → approve/edit/reject
  │
  │  [HITL #3: concept_approved] ── MANDATORY (client agrees) ─
  │
DESIGN PHASE ── Design Agent (before Dev) ──────────────────────
  │  Mockups, UI specs, responsive notes
  │  [HITL #4: design_review] ── MANDATORY (max 3 rounds) ────
  │  [HITL #5: design_client] ── MANDATORY ────────────────────
  │
  └── DEV CYCLE ENGINE (see Section 7)
        └── [HITL #6: final_review] ── MANDATORY ──────────────
```

### 6.2 Lead Scorer

```
Criteria                    | Points
No website at all          | +3
Website no mobile version  | +2
Many reviews + high rating | +2
Dead social (>3m no post)  | +1
High-value category        | +1 (clinic/restaurant/auto)
Good website (Light>80)    | -2
Few reviews (<10)          | -1

Temperature: ≥5 = HOT | 3-4 = WARM | <3 = COLD

Analysis depth:
  HOT  → deep    (2-3 min): Lighthouse + SimilarWeb + tech stack + competitors
  WARM → medium  (30 sec):  + social activity + basic SEO
  COLD → quick   (5 sec):   website status, rating, social
```

### 6.3 Touch Sequence (дизайн, не реализован)

```
День 1:  TG/Email (первый контакт)
День 3:  Follow-up (тот же канал)
День 5:  Alt-channel (если есть)
День 10: Final attempt
→ STOP

Стоп-правила:
  "нет" от клиента → STOP навсегда
  4 касания без ответа → STOP
  bounce / privacy_restricted → STOP
```

### 6.4 Anti-Ban стратегия

**Telegram DM:**
- 5 DM/час (порог бана ~20-30/час)
- 12 мин минимальный интервал
- FloodWait >300s → полная остановка 24ч
- PeerFlood → остановка 48ч + Telegram alert
- Warm-up: 2-3 недели органической активности

**Email:**
- 50/день (MAX_EMAILS_PER_DAY)
- 30-60s stagger между письмами
- SPF/DKIM/DMARC обязательны
- 6-недельный warm-up: 5→10→15→25→35→50
- RFC 8058 unsubscribe headers
- Bounce <2%, spam <0.1%

---

## 7. Dev Cycle Engine

### 7.1 Unified Execution Flow

```
PLANNER ── Decompose project into tasks ≤4h ────────────────────
  │  Inputs: project requirements, artifacts from previous agents
  │  Outputs:
  │    agent_sequence: ["design", "dev", "content"] (ordered)
  │    delivery_type: files | credentials | deploy | instructions | mixed
  │    estimated_hours: sum of all tasks
  │    needs_design: bool (API/backend → false)
  │
  │  [HITL: plan_review] ── CONDITIONAL (estimated_hours > 20) ─
  │
DYNAMIC EXECUTION ── _route_next_in_sequence() ─────────────────
  │  current_sequence_index = 0
  │  while current_sequence_index < len(agent_sequence):
  │      agent = agent_sequence[current_sequence_index]
  │      invoke agent (design_node / dev_node / content_node)
  │      if NOT in revision:
  │          current_sequence_index += 1
  │      → route via _route_next_in_sequence()
  │
  │  CRITICAL: Never hardcode next_agent in Dev/Content/Design.
  │  Let routing function decide based on sequence index.
  │
CRITIC ── Quality Gate ─────────────────────────────────────────
  │  Step 1: Semgrep scan (fail-closed: binary missing → blocked)
  │  Step 2: LLM review (code + content + design holistically)
  │
  │  Routing:
  │    score ≥ 0.85         → PACKAGER (approve)
  │    0.60-0.84 + minor    → revision_target (max 2 attempts)
  │    0.60-0.84 + major    → PLANNER (re-plan)
  │    < 0.60               → [HITL: escalation]
  │    revision_count ≥ 2   → [HITL: escalation]
  │
  │  Revision flow:
  │    Critic sets: revision_target="dev", revision_severity="minor"
  │    Dev Agent gets _critic_feedback in state
  │    Dev does NOT increment current_sequence_index
  │    After revision → Critic re-runs (index already >= len(sequence))
  │
PACKAGER ── Assemble delivery ──────────────────────────────────
  │  Switch by delivery_type:
  │    files:        ZIP / GitHub repo
  │    credentials:  login + password + instructions
  │    deploy:       hosted link + setup notes
  │    instructions: PDF documentation
  │    mixed:        all of above
  │
  │  Execution Cloaking: throttle delivery timing
  │
  │  [HITL: final_review] ── MANDATORY ─────────────────────────
  │    Show preview → approve / request_changes / reject
  │
  └── Platform Delivery → Experience Store (RAG) → Portfolio Agent
```

### 7.2 Partial Failure Recovery

```
Agent fails → status="failed"
  → artifacts from previous agents PRESERVED
  → [HITL: partial_failure]
      Actions:
        resume  → retry same agent
        skip    → next in sequence
        manual  → operator fixes, then continue
```

### 7.3 Dev Agent: Complexity Routing

Planner устанавливает `task.complexity`:
- **complex** (30% задач): Claude Opus 4.6 (Tier 1) — архитектурные решения, сложная логика
- **standard** (70% задач): Claude Sonnet 4.6 (Tier 3) — типовые задачи, CRUD, рефакторинг

---

## 8. HITL-система: мастер-план

### 8.1 Полная матрица (15 точек)

| # | Type | Pipeline | Момент | Mandatory? | Trigger | Actions |
|---|------|----------|--------|:----------:|---------|---------|
| 1 | `job_review` | A | Scout scored job | Conditional | 0.5 ≤ score < 0.7 | approve/reject/skip |
| 2 | `bid_approval` | A | Bid generated | **YES** | Always | approve/edit/reject/skip/later |
| 3 | `negotiation_dispute` | A | Price/scope conflict | Conditional | counter >25%, scope change | approve/edit/escalate |
| 4 | `dev_launch` | A | Client confirmed | **YES** | Always | start_dev/negotiate_more/decline |
| 5 | `plan_review` | Dev | Planner finished | Conditional | hours >20 OR major revision | approve/edit/reject |
| 6 | `code_review` | Dev | Critic escalation | Conditional | score <0.60 OR 2+ revisions | approve/reject/edit |
| 7 | `scope_creep` | Dev | Critic found discrepancy | Conditional | scope change detected | approve/reject/edit |
| 8 | `partial_failure` | Dev | Agent exception | Conditional | agent.status == "failed" | resume/skip/manual |
| 9 | `final_review` | Dev | Packager assembled | **YES** | Always | approve/request_changes/reject |
| 10 | `lead_card` | B | Lead scored | **YES** (hot/warm) | score ≥ 3 | approve/manual/skip |
| 11 | `outreach_approval` | B | Outreach drafted | **YES** | messages_drafted > 0 | approve/edit/reject |
| 12 | `concept_review` | B | SalesAgent concept | **YES** | Before sending to client | approve/edit/reject |
| 13 | `concept_approved` | B | Client agreed | **YES** | Client confirmed | proceed/hold |
| 14 | `design_review` | B | Design iteration | **YES** | After each round (max 3) | approve/revise/reject |
| 15 | `design_client` | B | Final design | **YES** | Before client approval | approve/request_changes |

### 8.2 HITLQueue Schema

```sql
CREATE TABLE hitl_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            VARCHAR(30) NOT NULL,  -- 'bid_approval', 'dev_launch', etc.
    priority        VARCHAR(10) DEFAULT 'normal',  -- urgent/normal/low
    bid_id          UUID REFERENCES bids(id),
    project_id      UUID REFERENCES projects(id),
    task_id         UUID REFERENCES tasks(id),
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    payload         JSONB NOT NULL,  -- full context
    available_actions TEXT[] NOT NULL,  -- ["approve","edit","reject"]
    status          VARCHAR(20) DEFAULT 'pending',  -- pending/resolved/expired
    resolution      VARCHAR(30),
    resolved_by     UUID REFERENCES users(id),
    expires_at      TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    telegram_sent   BOOLEAN DEFAULT FALSE,
    email_sent      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

### 8.3 Каналы доставки

1. **Dashboard WebSocket** — real-time карточки, inline actions
2. **Telegram Bot** — push notifications с inline buttons, `/approve <id>`, `/skip <id>`
3. **Soft Lock** — Valkey `hitl:viewing:{id}` TTL 5min, показывает кто смотрит

### 8.4 HITL Invariants

- `bid_approval` → `requires_hitl=True` ВСЕГДА (проверяется в 3 местах)
- `final_review` → `requires_hitl=True` ВСЕГДА
- `dev_launch` → `requires_hitl=True` ВСЕГДА
- Resumable types: bid_approval, plan_review, email_approval, final_review
- job_review approve → auto `job.status=qualified` + запуск Bid pipeline

---

## 9. Data & State: единый источник правды

### 9.1 Database Schema (20+ таблиц)

| # | Таблица | Назначение | Ключевые поля |
|---|---------|------------|---------------|
| 1 | `users` | Системные пользователи | email, password_hash, role, telegram_chat_id |
| 2 | `platform_accounts` | Credentials платформ | platform, credentials (encrypted Fernet) |
| 3 | `jobs` | Обнаруженные заказы | platform, external_id, title, budget_*, score, status |
| 4 | `bids` | Сгенерированные заявки | job_id, proposal_text, bid_amount, status |
| 5 | `projects` | Проекты (из выигранных бидов) | bid_id, type, status, progress, deadline |
| 6 | `tasks` | Декомпозиция Planner | project_id, assigned_agent, estimated_hours, depends_on[] |
| 7 | `artifacts` | Выходы агентов | project_id, task_id, type, content, storage_url, version |
| 8 | `hitl_queue` | Очередь решений | type, priority, payload, status, resolution |
| 9 | `revisions` | Ревизии от клиента | project_id, client_feedback, status |
| 10 | `leads` | Лиды Pipeline B | name, category, city, h3_index, osm_id (unique), status |
| 11 | `deals` | Сделки Pipeline B | lead_id, sales_stage, concept_json, status |
| 12 | `email_campaigns` | Outreach кампании | subject_template, target_cities[], status |
| 13 | `campaign_leads` | Campaign ↔ Lead join | campaign_id+lead_id PK, channel_type, sent_at |
| 14 | `agent_logs` | Логи агентов + LLM costs | agent_name, event_type, llm_model, tokens_*, cost_usd |
| 15 | `agent_heartbeats` | Здоровье агентов | agent_name PK, status, last_heartbeat, restart_count |
| 16 | `langgraph_checkpoints` | State persistence | thread_id+checkpoint_id PK, state_data JSONB |
| 17 | `langgraph_checkpoint_history` | История checkpoint | thread_id, checkpoint_id, timestamp |
| 18 | `knowledge_base` | RAG-память | type, category, content, embedding(3072), usage_count |
| 19 | `semantic_cache` | LLM-кэш | query_hash (unique), response, embedding(3072), TTL |
| 20 | `orchestrator_goals` | Цели оркестратора | goal_id, title, priority, category, status |
| 21 | `telegram_channels` | Каналы мониторинга | channel_username, pipeline_type, status |
| 22 | `client_messages` | Сообщения клиентов | thread_id, direction, content, classified_as |
| 23 | `negotiations` | Переговоры | bid_id, state, counter_offers[], outcome |
| 24 | `telegram_user_profiles` | Профили Telegram | user_id, messages[], score, needs, status |

### 9.2 Embedding Strategy

- **Model:** OpenAI `text-embedding-3-large` (3072 dimensions)
- **Index:** pgvector HNSW (vector_cosine_ops)
- **Operator:** `<=>` (cosine distance)
- **Tables with vectors:** `knowledge_base`, `semantic_cache`

### 9.3 Semantic Cache

**Dual-layer architecture:**

```
Query → hash → Valkey HNSW (HOT, <10ms)
              ↓ miss
         → PostgreSQL HNSW (COLD, <100ms)
              ↓ miss
         → LLM call → save to both layers
```

**Cache policy:**

| Agent | TTL | Cacheable? |
|-------|-----|:----------:|
| Scout | 6h | Yes |
| GeoScout | 6h | Yes |
| Outreach | 6h | Yes |
| Planner | 1h | Yes |
| Dev | 1h | Yes |
| Content | 12h | Yes |
| Design | 12h | Yes |
| Bid | — | **NEVER** (unique per job) |
| Critic | — | **NEVER** (must be fresh) |
| Packager | — | **NEVER** (unique per delivery) |
| SalesAgent | — | **NEVER** (unique per conversation) |
| Negotiation | — | **NEVER** (unique per message) |

**Similarity threshold:** 0.92 (cosine).

### 9.4 Agent State (LangGraph)

```python
# Core state fields
{
    "status": "active" | "paused" | "failed",
    "current_agent": str,           # "scout", "bid", "dev", ...
    "next_agent": str | None,       # only for inter-pipeline routing
    "artifacts": dict[str, list[str]],  # {agent_name: [serialized_outputs]}

    # Dynamic routing (Dev Cycle)
    "agent_sequence": list[str],    # ["design", "dev", "content"]
    "current_sequence_index": int,
    "delivery_type": str,           # "files" | "credentials" | ...

    # Revision tracking
    "revision_target": str | None,
    "revision_severity": str | None,  # "minor" | "major"
    "revision_count": int,

    # HITL
    "requires_hitl": bool,
    "hitl_request_id": str,         # UUID
    "hitl_response": dict | None,

    # Checkpointing
    "mas_checkpoint_id": str,       # NOT "checkpoint_id" (reserved!)
    "thread_id": str,
}
```

**State size limit:** `MAX_STATE_SIZE_BYTES = 524,288` (512KB).
Artifacts stored as references (IDs), not full content.

### 9.5 Update Pattern (CRITICAL)

```python
# ALWAYS:
state = update_state(state,
    current_agent="xxx",
    artifacts={...},
    status="active",
)

# NEVER:
state["current_agent"] = "xxx"  # ← ЗАПРЕЩЕНО
```

`update_state()` auto-bumps `mas_checkpoint_id` и `updated_at`.

---

## 10. Security

### 10.1 Encryption

- **Algorithm:** Fernet (AES-128-CBC), `cryptography.fernet.Fernet`
- **Key:** `ENCRYPTION_KEY` env var → valid base64 → use directly; else → PBKDF2 (480K iterations)
- **Production guard:** `"change-me-in-production"` → `ValueError`
- **What's encrypted:** platform credentials, API keys (OpenRouter, OpenAI, Hunter, Apollo)
- **DB format:** `{"_encrypted": "<fernet-token>"}`

### 10.2 Semgrep Gate

- **27 rules** across 3 YAML files (dangerous_patterns, file_access, network_access)
- **Fail-closed:** binary missing → `blocked=True`
- **Blocking rule:** `critical_count > 0` → blocked. Warnings don't block.
- **Integration:** Critic runs before approve; Dev has `analyze_with_semgrep` tool

### 10.3 Sandbox

- **Primary:** DockerExecutor — one-shot container, no network, non-root, CPU/mem limits
- **Fallback:** E2BExecutor — cloud sandbox (optional dependency)
- **Router:** `estimated_time > 5min → Docker, else → E2B`

### 10.4 Browser Stealth

```
StealthBrowser
  ├── playwright_stealth.Stealth() patches on every page
  ├── Launch args: --disable-blink-features=AutomationControlled, --no-sandbox, ...
  ├── connect_over_cdp() NEVER used
  ├── Proxy: BrightData residential
  │
  ├── JS Injection:
  │   navigator.webdriver = undefined
  │   navigator.plugins mock (3 plugins)
  │   window.__playwright delete
  │   window.chrome mock
  │   Permissions API mock
  │
  ├── Human-like behavior:
  │   click(): bezier curve, 15-25 points
  │   type_text(): 50-150ms per keystroke
  │   scroll_to(): smooth
  │   wait_random(): 1-5s default
  │
  └── Viewport: randomized 1280-1920 × 800-1080
```

### 10.5 JWT Auth

- **Algorithm:** HS256, `JWT_SECRET_KEY`
- **Access token:** 24h (configurable), claims: sub, email, role, jti
- **Refresh token:** 7d (configurable), claims: sub, type="refresh"
- **Blacklist:** Valkey `token:blacklist:{jti}`, fail-closed
- **Passwords:** bcrypt, gensalt(rounds=12)
- **RBAC:** 4 roles — owner (единственный), co_owner, moderator, viewer

### 10.6 Rate Limiting (5 layers)

| Layer | Scope | Limits |
|-------|-------|--------|
| L1: API | Global | 300/min, Auth: 60/min, WS: 100/sec |
| L2: Platform | Per-adapter | Freelancer 15 RPM, FL.ru 5, Kwork 3, Upwork 3 |
| L3: LLM | Per-provider | Opus 50 RPM, Sonnet 200, Flash 300 |
| L4: Enrichment | Per-service | Hunter 500/day, Apollo 300/day, Overpass 10K/day |
| L5: Business | System-wide | Bids 150/day, Projects 3-4/day, Emails 50/day |

### 10.7 LLM Budget Control

- **Daily:** $50, **Monthly:** $500
- **Alerts:** 80% daily → warning, 90% monthly → HITL + pause, exhausted → error
- **PromptBatcher:** batch max 10, wait 500ms → 5-10x RPM reduction
- **ResponseCache:** hash prompt → Valkey TTL 24h

---

## 11. Infrastructure

### 11.1 WebSocket Events (9 default channels)

| Channel | Events |
|---------|--------|
| `agent:heartbeat` | Agent health pings |
| `agent:log` | Agent execution logs |
| `hitl:new` | New HITL item created |
| `hitl:resolved` | HITL resolved |
| `project:update` | Project status change |
| `notification` | System alerts |
| `orch:status` | Orchestrator start/stop |
| `orch:goal` | Goal CRUD |
| `orch:log` | Runner log streaming |

+ Dynamic: `project:{uuid}`, `agent:{name}`

**Publish Guard:** ALL `publish_event()` calls wrapped in `try/except (OSError, ConnectionError)`.

### 11.2 Orchestrator

- **Runner:** bash/powershell script, launched as subprocess with `start_new_session`
- **PID file:** `~/.claude/orchestrator/runner.pid`
- **Health Report:** YAML, 8 dimensions
- **Vision Milestones:** Markdown, `## Phase N — Title`, `- [x] done`
- **Goal Model:** `orchestrator_goals` table, auto-increment `g_NNN`, priorities: critical/high/medium/low
- **9 API endpoints:** status, start, stop, goals(GET/POST/DELETE), health, milestones, logs

### 11.3 Telegram Bot

- **Library:** python-telegram-bot 21.x, Long Polling
- **17 handlers:** 7 HITL + 8 Orchestrator + 2 callback
- **Dashboard Sync:** Valkey pub/sub `hitl:resolved:bot`, `orch:event:bot`
- **Link Flow:** `/start` → 6-char code (Valkey TTL 600s) → Dashboard input
- **Deep Links:** `t.me/MASBot?start=link_{code}`, `t.me/MASBot?start=hitl_{id}`
- **Auth:** linked_account + role ≥ moderator (dual check)

### 11.4 Deploy (Railway)

- **2 services:** API (Python/Docker) + Dashboard (Node/Remix)
- **Dashboard deploy:** `railway up --detach --service dashboard --path-as-root dashboard`
- **Migration retry:** `for i in 1 2 3 4 5; do alembic upgrade head && break || sleep 5; done`
- **Railway IP issue:** reverse proxy collapses all IPs → shared rate limit bucket

---

## 12. Legal & Compliance

### 12.1 Data Retention

| Данные | Срок | Действие |
|--------|------|----------|
| Активные клиенты | Срок отношений + 3 года | Архивация |
| Завершённые проекты | 5 лет (налоги) | Удаление |
| Email-лиды без ответа | 90 дней | Удаление |
| Email-лиды с ответом | 12 месяцев | Удаление если нет сделки |
| Telegram-профили | 12 месяцев без активности | Удаление |
| Контракты/инвойсы | 5 лет | Архивация |
| Suppression list | Бессрочно | Хранить всегда |

### 12.2 Platform ToS Compliance

| Платформа | Разрешено | Запрещено |
|-----------|-----------|-----------|
| Freelancer.com | API (safe), bid через HITL | Массовые биды без контроля |
| Upwork | Мониторинг (scraping) | **Auto-submit, боты, массовые сообщения** |
| FL.ru | RSS (публичный) | — |
| Kwork | Careful scraping (30 pages/hr) | Агрессивный scraping |
| Fiverr | Buyer Requests monitoring | Auto-submit (нет API) |

### 12.3 Email Compliance

- **B2B cold email:** Legitimate Interest (GDPR), согласие НЕ требуется
- **Каждое email содержит:** название ИП, физический адрес, unsubscribe link
- **CAN-SPAM:** точный From/Subject, обработка unsubscribe 10 рабочих дней
- **GPL/AGPL:** не использовать без HITL
- **AI-генерированный код:** не подлежит копирайту (US); весь код → собственность клиента

### 12.4 152-ФЗ (Россия)

- Данные граждан РФ = серверы в РФ (Yandex.Cloud / VK Cloud)
- Railway (текущий) — НЕ российский. Для compliance нужна миграция.
- B2B данные: согласие не требуется. Публичные данные: свободно.

---

## 13. Testing

### 13.1 Пирамида

| Уровень | Файлов | Тестов | Доля |
|---------|:------:|:------:|:----:|
| Unit | 105+ | ~2200+ | 88% |
| Integration | 25+ | 80+ | 10% |
| E2E | 3 | 39 | 2% |
| **Total** | **133+** | **2435+** | **100%** |

> **Примечание:** числа актуальны для ветки `auto/2026-02-26/multichannel-outreach-portfolio`. На `main` — ~2087 тестов.

### 13.2 Coverage Targets

| Модуль | Target | Priority |
|--------|:------:|:--------:|
| scout.py | 90% | Critical |
| bid.py | 90% | Critical |
| core/ | 85% | Critical |
| planner.py | 80% | High |
| dev.py | 80% | High |
| api/ | 70% | Medium |
| utils/ | 60% | Low |

### 13.3 Critical Test Rules

1. `StateGraph(dict)` — НЕ `StateGraph(AgentState)`
2. Routing functions: `state: dict[str, Any]` (LangGraph 1.0.8 bug)
3. `astream` вместо `ainvoke` (может вернуть начальный state)
4. SemgrepGate: fail-closed → мок обязателен
5. `asyncio.Event` — не на уровне модуля
6. `playwright_stealth`: мокать `src.browser.stealth._stealth`
7. AsyncMock для async interfaces (ChannelsPlugin)
8. Prometheus singleton: reset `_metrics` + unregister collectors
9. APScheduler v3: `await asyncio.sleep(0)` после stop()

---

## 14. Portfolio & Growth

### 14.1 Стратегия холодного старта

**6 платформ:** Kwork, FL.ru, Fiverr, Freelancer.com, YouDo, Telegram.

**10 проектов для портфолио:**
1. Лендинг ресторана (HTML/CSS/JS)
2. Telegram-бот для салона (Python, Bot API)
3. Интернет-магазин (Next.js, Stripe/ЮKassa)
4. Сайт стоматологии + ЛК (React, Node.js)
5. CRM для автосервиса (React, Python)
6. AI-чатбот поддержки (Python, OpenAI)
7. Дашборд аналитики (React, D3.js)
8. PWA доставки еды (React, PWA)
9. Корпоративный сайт IT (Next.js, Tailwind)
10. Бот автоматизации email (Python, SMTP)

**Принцип:** НЕ деплоить — верстать для скриншотов + мокапы.

**Фазы:**
- Нед 1-2: заполнение портфолио, кворки/gigs
- Нед 3-6: активные отклики, -20-30% от рынка, цель 5+ отзывов
- Нед 7-12: рыночные цены, 10+ отзывов, 4.8+
- Мес 4+: премиум, upselling, международный

### 14.2 Portfolio Agent (#12, дизайн)

```
Packager → HITL(final_review) → [approve] →
  Portfolio Agent: classify → screenshot → generate → adapt → save
  → [HITL: portfolio review] → публикация
```

- Auto-screenshot: Playwright (desktop 1440×900 + mobile 390×844)
- Manual: уведомление оператору для сложных проектов
- Platform adaptation: Kwork ≤500, FL.ru ≤1000, Fiverr ≤1200 символов
- Git folder `portfolio/` = source of truth
- Audit: месячный cron — свежесть, разнообразие, конверсия

### 14.3 Pipeline C: Client Growth (идея)

```
Завершённый проект →
  Auto-Monitor → Snapshot Report → Upsell Recommender → [HITL] →
  → Managed Services / Новый проект
```

**4 уровня эволюции клиента:**
1. Сайт + улучшения
2. Интеграции (онлайн-запись, оплата)
3. Кастомный софт (CRM/ERP)
4. Полная цифровизация (AI)

**Revenue target:** MRR 300k руб/мес при 20 клиентах (50% конверсия в managed services).

---

## 15. Конфигурация: все env vars

| Variable | Обязательна | Используется в |
|----------|:-----------:|----------------|
| `DATABASE_URL` | Yes | PostgreSQL connection (НЕ POSTGRES_URL) |
| `VALKEY_URL` | Yes | Cache, sessions, pub/sub (НЕ REDIS_URL) |
| `OPENROUTER_API_KEY` | Yes | Все LLM вызовы |
| `OPENAI_API_KEY` | Yes | Embeddings (text-embedding-3-large) |
| `JWT_SECRET_KEY` | Yes | JWT auth |
| `ENCRYPTION_KEY` | Yes | Fernet encryption |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot |
| `TELEGRAM_CHAT_ID` | Yes | Push notifications |
| `TELEGRAM_API_ID` | Yes* | Telethon listener |
| `TELEGRAM_API_HASH` | Yes* | Telethon listener |
| `TELEGRAM_SESSION_STRING` | Yes* | Telethon StringSession |
| `BRIGHTDATA_USERNAME` | No | Proxy for scraping |
| `BRIGHTDATA_PASSWORD` | No | Proxy for scraping |
| `BRIGHTDATA_HOST` | No | brd.superproxy.io |
| `HUNTER_API_KEY` | No | Email enrichment |
| `APOLLO_API_KEY` | No | People enrichment |
| `E2B_API_KEY` | No | Cloud sandbox |
| `LANGSMITH_API_KEY` | No | LangSmith tracing |

*Required for Pipeline B Telegram monitoring.

---

## 16. Критические инварианты (абсолютные правила)

> Нарушение любого из этих правил = системный баг.

1. **`state: dict[str, Any]`** — НИКОГДА `AgentState` TypedDict в routing/HITL functions
2. **`StateGraph(dict)`** — НИКОГДА `StateGraph(AgentState)`
3. **`astream(stream_mode="values")`** — НИКОГДА `ainvoke()` для финального state
4. **`update_state()`** — НИКОГДА прямая мутация state dict
5. **`mas_checkpoint_id`** — НИКОГДА `checkpoint_id` (reserved by LangGraph)
6. **`requires_hitl=True`** — ВСЕГДА для bid_approval, dev_launch, final_review
7. **Upwork auto-submit** — ЗАПРЕЩЕНО (ToS violation)
8. **WebSocket publish guard** — `try/except (OSError, ConnectionError)` на КАЖДОМ publish
9. **Semgrep fail-closed** — binary missing → `blocked=True`
10. **Heartbeat** — 90s interval, 180s timeout, max 3 restarts
11. **Email** — max 50/day, 6-week warm-up, SPF/DKIM/DMARC, RFC 8058 unsubscribe
12. **Budget** — $50/day, $500/month, HITL at 90%
13. **State size** — MAX 512KB, artifacts as references
14. **Encryption key** — `"change-me-in-production"` → ValueError
15. **Rate limit adaptive** — captcha/ban → min_rpm + 30min pause + alert

---

> **Этот документ — единый источник правды для MAS.**
> Все предыдущие спеки остаются как детальные reference, но при конфликте — MASTER-VISION.md побеждает.
