# Агенты Pipeline A: Спецификация

## Общая архитектура агентов

Все агенты наследуют `ConstrainedAgent` из `src/agents/base.py`. Базовый класс обеспечивает:

- **Lifecycle management** через `invoke(state) -> AgentState` -- единая точка входа
- **Heartbeat** -- пинг `HeartbeatMonitor` перед каждым выполнением
- **Loop detection** -- `LoopDetector.check()` по `thread_id` + `current_step`
- **Role constraints** -- валидация `forbidden_tools`, проверка `max_iterations`
- **LLM delegation** -- `_call_llm(messages, temperature, max_tokens)` через `LLMClient`
- **Retry logic** -- до `max_retries=3` попыток при `LLMException`/`AgentException`
- **Timeout** -- `asyncio.wait_for` с лимитом 600 сек (10 мин) на вызов
- **Sentry tracing** -- автоматическая транзакция при наличии `sentry_sdk`
- **Prometheus metrics** -- `record_agent_run(agent, status, duration)` через `get_metrics()`
- **User credentials** -- `_load_user_credentials(user_id)` загружает OpenRouter API key из БД

Каждый конкретный агент реализует `async _execute(self, state: AgentState) -> AgentState`.
На уровне модуля экспортируется `async def {name}_node(state) -> AgentState` для подключения к `StateGraph`.

### Паттерн state transitions

```python
update_state(state, current_agent="xxx", next_agent="yyy", artifacts=artifacts, status="active")
```

Агент устанавливает `current_agent=self`, `next_agent=target`, `status` = `"active"` | `"paused"` | `"failed"`.
При HITL: `requires_hitl=True`, `hitl_request_id=uuid`, `status="paused"`, `next_agent=None`.

---

## Scout Agent

**Файлы:** `src/agents/scout.py`, `src/prompts/scout.py`
**LLM:** Gemini 2.5 Flash (Tier 5 — Extraction)
**Температура:** 0.2
**Класс:** `ScoutAgent(ConstrainedAgent)`

### Назначение

Мониторинг 5 фриланс-платформ, оценка вакансий по capability matrix, маршрутизация квалифицированных заказов к Bid Agent.

### Allowed Tools

`fetch_freelancer_jobs`, `fetch_flru_rss`, `fetch_kwork_jobs`, `fetch_upwork_jobs`, `analyze_client_profile`

### Платформы и адаптеры

| Платформа | Интеграция | Адаптер |
|-----------|-----------|---------|
| Freelancer.com | REST API | `FreelancerClient(client_id, client_secret)` |
| FL.ru | RSS (публичный) | `FlRuClient()` |
| Kwork | Playwright stealth | `KworkClient(browser_pool)` |
| Upwork | Playwright (read-only!) | `UpworkClient(browser_pool)` |
| Telegram | Valkey queue | `container.telegram_adapter` |

### Flow

1. `_fetch_all_platforms()` -- параллельный `asyncio.gather` по всем адаптерам
2. `_deduplicate(jobs)` -- SELECT по `(platform, external_id)` из таблицы `jobs`
3. `_score_jobs(batch)` -- LLM-скоринг батчами по `_MAX_JOBS_PER_BATCH=10`
4. Классификация: `qualified` (score >= 0.7, bid), `review` (0.5-0.7), `rejected` (< 0.5)
5. `_store_jobs()` -- upsert в `jobs` (ON CONFLICT DO NOTHING)
6. `_create_hitl_review()` -- HITLQueue type=`job_review` для borderline
7. `_log_decision_summary()` -- запись в `agent_logs`

### Скоринг (из промпта)

- **0.9-1.0** -- идеальное совпадение: ясный скоуп, адекватный бюджет, наш стек
- **0.7-0.89** -- сильное совпадение с минорными пробелами
- **0.5-0.69** -- пограничный, на ревью (HITL)
- **0.0-0.49** -- авто-реджект

**Бюджет:** $20-$5,000. **Категории:** Web Dev, UI/UX, Copywriting, WordPress, React/Next.js, Landing Pages, HTML/CSS, Tailwind, Shopify, Webflow.
**Reject:** мобильные приложения, AI/ML, Blockchain, ERP, клиент с < 3 отзывами или < 50% hire rate.

### State выход

- `artifacts["scout"]` = `list[str]` (UUID квалифицированных jobs)
- `next_agent="bid"` если есть квалифицированные, иначе `None`
- `status="active"`

---

## Bid Agent

**Файлы:** `src/agents/bid.py`, `src/prompts/bid.py`
**LLM:** Gemini 3.1 Pro (Tier 2 — Client-facing)
**Температура:** 0.7
**Класс:** `BidAgent(ConstrainedAgent)`

### Назначение

Генерация персонализированных предложений (proposals) для квалифицированных заказов. HITL обязателен перед отправкой.

### RAG контекст

`KnowledgeRetriever` (vector search по `knowledge_base`, тип `proposal_template`):
- Запрос = title + description[:500] + skills
- `top_k=3`, категория определяется через `_infer_category()` (web_development, wordpress, design, copywriting, landing_pages)
- Fallback: SQL-запрос по `category + success_rate DESC`

### Структура предложения (5 частей)

1. **Hook** (1 предложение) -- ссылка на конкретную деталь из описания заказа
2. **Credibility** (1-2 предложения) -- релевантный прошлый проект с цифрами
3. **Solution** (2-3 предложения) -- подход к ЭТОМУ проекту, стек, quick win
4. **Timeline** -- реалистичная оценка с milestones (при бюджете > $200)
5. **CTA** (1 предложение) -- низкобарьерный следующий шаг

### Pricing strategy

| Размер | Бюджет | Стратегия |
|--------|--------|-----------|
| Micro | $20-100 | Fixed, minimal scope |
| Small | $100-500 | Fixed, 2-3 milestones |
| Medium | $500-2,000 | Fixed, 3-5 milestones |
| Large | $2,000-5,000 | Milestone-based |

Первые проекты: -10-15% от рынка (наработка репутации). После 10+ отзывов -- рыночная цена.

### HITL интеграция

- Bid сохраняется в таблицу `bids` со `status='hitl_pending'`
- HITLQueue: `type='bid_approval'`, `available_actions=["approve", "edit", "skip", "later"]`
- State: `requires_hitl=True`, `status="paused"`, `next_agent=None`
- **INVARIANT:** `requires_hitl` ВСЕГДА `True` -- проверяется в 3 местах

### Валидация

- `bid_amount`: float, диапазон $5-$50,000
- `delivery_days`: int (default 7)
- `confidence_score`: float 0.0-1.0 (default 0.5)
- `milestones`: list (default [])

---

## Planner Agent

**Файлы:** `src/agents/planner.py`, `src/prompts/planner.py`
**LLM:** Claude Opus 4.6
**Температура:** 0.3
**Класс:** `PlannerAgent(ConstrainedAgent)`

### Назначение

Декомпозиция проекта на фазы и задачи, назначение агентов, оценка сроков.

### Allowed Tools

`create_project_plan`, `estimate_task_duration`, `assign_task_to_agent`, `update_project_status`, `detect_blockers`

### LLM Output Format

Planner вызывает LLM и парсит ответ в следующую структуру:

```python
class PlannerOutput(TypedDict):
    agent_sequence: list[str]       # ["design", "dev", "content"] — порядок execution-агентов
    tasks: list[PlannerTask]        # Задачи с agent assignment
    delivery_type: DeliveryType     # "files" | "credentials" | "deploy" | "instructions" | "mixed"
    estimated_hours: float          # Суммарная оценка
    risks: list[str]                # Выявленные риски

class PlannerTask(TypedDict):
    id: str                         # Уникальный ID задачи
    description: str                # Что делать
    assigned_to: str                # "dev" | "content" | "design"
    estimated_hours: float          # Max 4h на задачу
    dependencies: list[str]         # ID зависимых задач
    deliverables: list[str]         # Что должно быть на выходе
```

**Routing по типу проекта:**

| Тип проекта | agent_sequence | delivery_type | Пример |
|-------------|----------------|---------------|--------|
| Лендинг / сайт | `["design", "dev", "content"]` | `deploy` | "React лендинг для стоматологии" |
| TG-бот / API | `["dev"]` | `files` или `credentials` | "Telegram бот для записи" |
| Брендбук | `["design"]` | `files` | "Логотип + фирменный стиль" |
| Контент-проект | `["content"]` | `files` | "Тексты для сайта" |
| Консалтинг | `[]` (пустой) | `instructions` | "Аудит + рекомендации" |
| Полный продукт | `["design", "dev", "content"]` | `mixed` | "CRM под ключ" |

**Пустой `agent_sequence`** = консалтинг. План сам является deliverable → сразу к Packager.

### HITL Plan Review

Триггеры для HITL-ревью плана:
1. `estimated_hours >= 20` -- комплексный проект
2. `_critic_revision_type == "major"` -- крупная ревизия от Critic (re-plan)
3. `replan_count >= _MAX_REPLANS (3)` -- превышен лимит перепланирований
4. Оператор вручную запросил ревью

**Payload для HITL:**

```json
{
  "agent_sequence": ["design", "dev", "content"],
  "tasks": [{"id": "t1", "description": "Mockup главной", "assigned_to": "design", "estimated_hours": 2}],
  "delivery_type": "deploy",
  "estimated_hours": 14,
  "risks": ["Client requirements are vague"]
}
```

**Действия оператора:**
- `approve` → execution (agent_sequence[0])
- `edit` → изменить sequence / задачи / delivery_type через `edited_payload` → approve
- `reject` → Planner переделывает план (`replan_count++`)

### State выход

Planner записывает в state:

```python
update_state(state,
    current_agent="planner",
    artifacts={**artifacts, "planner": [plan_json]},
    agent_sequence=["design", "dev", "content"],    # NEW: dynamic routing
    delivery_type="deploy",                          # NEW: delivery type
    current_sequence_index=0,                        # NEW: reset index
    revision_target=None,                            # NEW: clear revision
    revision_severity=None,                          # NEW: clear revision
    next_agent=None,                                 # Routing через _route_next_in_sequence
    status="active",
)
```

- `artifacts["planner"]` = `list[str]` (JSON-сериализованные планы)
- При HITL: `requires_hitl=True`, `status="paused"`

---

## Dev Agent

**Файлы:** `src/agents/dev.py`, `src/prompts/dev.py`
**LLM:** Claude Opus 4.6 (T1, complex 30%) / Claude Sonnet 4.6 (T3, standard 70%)
**Температура:** 0.3, `max_tokens=16000`
**Класс:** `DevAgent(ConstrainedAgent)`

### Назначение

Генерация production-quality кода. Поддерживает первичную генерацию и revision cycle (по фидбэку Critic).

### Allowed Tools

`generate_code`, `run_in_sandbox`, `run_tests`, `analyze_with_semgrep`, `deploy_to_preview`

### Flow

1. `_extract_task_context(state)` -- из `current_task` или `artifacts["planner"]`
2. Проверка `_extract_critic_feedback(state)` -- если есть, строит revision prompt
3. `_call_llm()` -- генерация кода
4. `_parse_code_response()` -- валидация: `files[]` (path + content обязательны), `dependencies[]`, `build_commands[]`, `test_commands[]`, `deployment_notes`
5. **Semgrep scan** -- `SemgrepGate().scan_files(files)`, результат в `artifacts["_dev_semgrep_warnings"]`
6. **Sandbox execution** -- `SandboxManager().execute_code(files)`, результат в `artifacts["_dev_execution"]`
7. `artifacts["dev"]` = `[artifact_id, serialized_json]`

### Стек (из промпта)

Frontend: React, Next.js, Vue, Svelte, HTML/CSS, Tailwind. Backend: Node.js, Python, Litestar, Express. CMS: WordPress, Shopify, Webflow. DB: PostgreSQL, MySQL, MongoDB. Deploy: Docker, Vercel, Netlify.

### Revision cycle

При наличии `artifacts["critic"]` с `verdict` -- строит `_build_revision_prompt()` с:
- Предыдущий код из `artifacts["dev"]`
- Issues от Critic (severity, location, suggestion)
- `revision_instructions`, `failed_checks`
- Номер ревизии из `_critic_revision_count`

### State выход

```python
# Revision check: НЕ инкрементировать index при revision cycle
is_revision = state.get("revision_severity") is not None
new_index = state["current_sequence_index"] if is_revision else state["current_sequence_index"] + 1

update_state(state,
    current_agent="dev",
    artifacts={**artifacts, "dev": [code_artifact_id, serialized_code_json]},
    current_sequence_index=new_index,
    revision_target=None,      # Очищаем после обработки
    revision_severity=None,    # Очищаем после обработки
    next_agent=None,   # routing через _route_next_in_sequence, НЕ хардкод
    status="active",
)
```

- `artifacts["dev"]` = `[code_artifact_id, serialized_code_json]`
- **НЕ хардкодит `next_agent`** — инкрементирует `current_sequence_index`, routing решает `_route_next_in_sequence()`

---

## Content Agent

**Файлы:** `src/agents/content.py`, `src/prompts/content.py`
**LLM:** Claude Sonnet 4.6 (Tier 3 — Content+Review)
**Температура:** 0.7
**Класс:** `ContentAgent(ConstrainedAgent)`

### Назначение

Генерация текстового контента: копирайтинг, документация, email, UI-текст.

### Типы контента (автоопределение через `_infer_content_type()`)

`landing_page`, `email`, `documentation`, `ui_text`, `blog_post`, `product_description`

Ключевые слова определяют тип: "landing page" -> `landing_page`, "readme" -> `documentation`, "button" -> `ui_text` и т.д. Default: `landing_page`.

### Формат выхода

```json
{"content_type": "...", "deliverables": [{"name": "hero_headline", "content": "...", "alternatives": [...], "notes": "..."}], "word_count": 150, "reading_time_seconds": 45}
```

Каждый deliverable содержит 2 альтернативы для A/B тестирования.

### State выход

```python
# Revision check: НЕ инкрементировать index при revision cycle
is_revision = state.get("revision_severity") is not None
new_index = state["current_sequence_index"] if is_revision else state["current_sequence_index"] + 1

update_state(state,
    current_agent="content",
    artifacts={**artifacts, "content": [serialized_json]},
    current_sequence_index=new_index,
    revision_target=None,      # Очищаем после обработки
    revision_severity=None,    # Очищаем после обработки
    next_agent=None,   # routing через _route_next_in_sequence
    status="active",
)
```

- `artifacts["content"]` = `[serialized_json]`
- **НЕ хардкодит `next_agent`** — инкрементирует `current_sequence_index`

---

## Design Agent

**Файлы:** `src/agents/design.py`, `src/prompts/design.py`
**LLM:** NanoBanana Pro (Tier 4 — Design)
**Температура:** 0.7
**Класс:** `DesignAgent(ConstrainedAgent)`

### Назначение

Генерация дизайн-спецификаций (JSON specs) -- цвета, шрифты, layout, responsive notes. В MVP фазе -- без генерации изображений.

### Типы дизайна (автоопределение через `_infer_design_type()`)

`ui_mockup`, `graphic`, `icon_set`, `design_system`, `wireframe`. Default: `ui_mockup`.

### Default Style Guide (из промпта)

- Цвета: #F8FAFC (фон), #0F172A (текст), один accent
- Типографика: Inter, SF Pro, system-ui, max 2 семейства
- Spacing: 8px grid
- Border radius: 8px (карточки), 4px (кнопки)
- Тени: 2 уровня elevation

### State выход

```python
# Revision check: НЕ инкрементировать index при revision cycle
is_revision = state.get("revision_severity") is not None
new_index = state["current_sequence_index"] if is_revision else state["current_sequence_index"] + 1

update_state(state,
    current_agent="design",
    artifacts={**artifacts, "design": [serialized_json]},
    current_sequence_index=new_index,
    revision_target=None,      # Очищаем после обработки
    revision_severity=None,    # Очищаем после обработки
    next_agent=None,   # routing через _route_next_in_sequence
    status="active",
)
```

- `artifacts["design"]` = `[serialized_json]`
- **НЕ хардкодит `next_agent`** — инкрементирует `current_sequence_index`

### Pencil.dev MCP (не реализовано)

Планируется интеграция через MCP для автоматической генерации визуальных макетов из JSON-спек.

---

## Critic Agent

**Файлы:** `src/agents/critic.py`, `src/prompts/critic.py`
**LLM:** Claude Sonnet 4.6 (Tier 3 — Content+Review)
**Температура:** 0.2, `max_tokens=8000`
**Класс:** `CriticAgent(ConstrainedAgent)`

### Назначение

Финальный автоматизированный gate качества. Ревьюит артефакты dev, content, design.

### Semgrep интеграция (fail-closed)

1. `_run_semgrep_scan()` -- сканирует `files[]` из `artifacts["dev"]`
2. Если `blocked=True` (critical findings) -- verdict `reject`, score 0.0, route к `dev`
3. Score penalties: CRITICAL = -0.3, WARNING = -0.1 за каждый finding
4. `SemgrepGate` при отсутствии бинарника возвращает `blocked=True` (fail-closed)

### Scoring (из промпта)

Старт с 1.0, вычеты: critical -0.15, major -0.08, minor -0.03. Minimum 0.0.

### Decision thresholds и routing

| Verdict | Score | Routing |
|---------|-------|---------|
| APPROVE | >= 0.85 | → Packager |
| REVISE minor | 0.60-0.84 | → `revision_target` напрямую (макс 2 попытки) |
| REVISE major | 0.60-0.84 | → Planner (re-plan + HITL) |
| REVISE scope_creep | 0.60-0.84 | → HITL (обсуждение скоупа с клиентом) |
| REJECT | < 0.60 | → HITL escalation |
| revision_count >= 2 для одного агента | любой | → HITL escalation |

### Revision classification

- `minor` -- опечатка, цвет, alt-тег → обратно к **конкретному агенту** (`revision_target`)
- `major` -- редизайн секции, новый функционал → обратно к Planner
- `scope_creep` -- запрос за пределами ТЗ → HITL немедленно
- `none` -- при approve или reject

**ВАЖНО:** Critic определяет `revision_target` — конкретный агент для minor revision. Если баг в дизайне — возвращает к `design`, а не к `dev`. Это заменяет старый хардкод `next_agent="dev"` для всех revision.

### State выход

```python
# При APPROVE:
update_state(state,
    current_agent="critic",
    artifacts={**artifacts, "critic": [review_json]},
    next_agent="packager",
    revision_target=None,
    revision_severity=None,
    status="active",
)

# При REVISE minor:
update_state(state,
    current_agent="critic",
    artifacts={**artifacts,
        "critic": [review_json],
        "_critic_revision_count": [str(count)],
        "_critic_feedback": [feedback_json],     # Что именно исправить
    },
    next_agent=None,          # routing через _route_after_critic
    revision_target="design", # КОНКРЕТНЫЙ агент (не всегда dev!)
    revision_severity="minor",
    status="active",
)

# При REVISE major:
update_state(state,
    current_agent="critic",
    artifacts={**artifacts, "critic": [review_json], "_critic_revision_type": ["major"]},
    next_agent="planner",
    revision_target=None,
    revision_severity="major",
    status="active",
)
```

- `artifacts["critic"]` = `[review_json]`
- `artifacts["_critic_revision_count"]` = `[str(count)]`
- `artifacts["_critic_feedback"]` = `[feedback_json]` (при minor revision — инструкции для target agent)

---

## Packager Agent

**Файлы:** `src/agents/packager.py`, `src/prompts/packager.py`
**LLM:** DeepSeek V3.2 (Tier 6 — Simple)
**Температура:** 0.3
**Класс:** `PackagerAgent(ConstrainedAgent)`

### Назначение

Сборка утверждённых артефактов в delivery package, генерация README и сообщения клиенту. HITL обязателен.

### Allowed Tools

`collect_project_artifacts`, `generate_readme`, `create_delivery_archive`, `generate_delivery_message`

### Flow

1. `_collect_execution_artifacts()` -- собирает из `artifacts["dev"]`, `artifacts["content"]`, `artifacts["design"]`, `artifacts["critic"]`
2. `_generate_delivery_package(project, collected)` -- LLM генерирует описание
3. Fallback: `_build_fallback_delivery()` при сбое LLM
4. HITLQueue: `type='final_review'`, `priority='high'`, `available_actions=["approve_delivery", "request_changes", "reject"]`

### Формат delivery

```json
{"delivery_id": "...", "project_id": "...", "files_count": 25, "includes": ["source_code", "documentation"], "delivery_message": "...", "readme_content": "...", "missing_artifacts": [], "quality_notes": "...", "requires_hitl": true}
```

### State выход

- `artifacts["packager"]` = `[delivery_json]`
- `requires_hitl=True`, `status="paused"`, `next_agent=None`
- **INVARIANT:** `requires_hitl` ВСЕГДА `True`

---

## State Transitions (сводная таблица)

| Agent | sets `artifacts[key]` | Routing | HITL? | Условие HITL |
|-------|----------------------|---------|-------|-------------|
| Scout | `scout` = job UUIDs | `→ bid` или `→ END` | Нет (создает HITL для review-band) | score 0.5-0.7 → job_review |
| Bid | `bid` = bid UUIDs | `→ hitl_bid` (paused) | **Да, всегда** | bid_approval |
| *(bid_submission)* | `bid_submitted` = bool | `→ hitl_dev_launch` | -- | -- |
| **HITL dev_launch** | -- | `→ planner` или `→ END` | **Да, всегда** | dev_launch (оператор решает запускать ли разработку) |
| Planner | `planner` = plan JSON, sets `agent_sequence`, `delivery_type` | `→ _route_next_in_sequence` | Условно | hours >= 20, replan >= 3, major revision |
| Dev | `dev` = [id, code_json], `current_sequence_index++` | `→ _route_next_in_sequence` | Нет | -- |
| Content | `content` = [json], `current_sequence_index++` | `→ _route_next_in_sequence` | Нет | -- |
| Design | `design` = [json], `current_sequence_index++` | `→ _route_next_in_sequence` | Нет | -- |
| Critic | `critic` = [review_json], sets `revision_target`, `revision_severity` | `→ packager` / `→ revision_target` / `→ planner` / `→ HITL` | Условно | reject, scope_creep, revision >= 2 |
| Packager | `packager` = [delivery_json] | `→ hitl_review` (paused) | **Да, всегда** | final_review |

### Ключевое изменение: Dynamic Routing

**Было (старая модель):** Dev → Content → Design → Critic (хардкод `next_agent` в каждом агенте).

**Стало (новая модель):** Planner устанавливает `agent_sequence`. Каждый execution-агент инкрементирует `current_sequence_index`. Единая routing-функция `_route_next_in_sequence()` решает куда дальше.

```
Planner → agent_sequence[0] → agent_sequence[1] → ... → agent_sequence[N-1] → Critic
```

Это позволяет:
- Проекты только с Dev (API/бот): `["dev"]`
- Проекты без кода (брендбук): `["design"]`
- Консалтинг (пустой sequence): `[]` → сразу Packager

---

## Агенты Pipeline B

### GeoScout Agent

**Файлы:** `src/agents/geo_scout.py`, `src/prompts/geo_scout.py`
**LLM:** Gemini 2.5 Flash (Tier 5 — Extraction, no LLM in MVP)
**Класс:** `GeoScoutAgent(ConstrainedAgent)`

#### Назначение

Поиск локальных бизнесов, нуждающихся в цифровизации, через географические данные (Overpass API / OpenStreetMap).

#### Allowed Tools

`search_overpass`, `search_2gis`, `check_website_status`, `estimate_business_value`, `dedupe_with_crm`

#### Источники данных

| Источник | Тип | Лимит |
|----------|-----|-------|
| Overpass API (OSM) | Бесплатный | 10,000 req/day |
| 2GIS API | RU/CIS регионы | -- |
| Google Places | Premium niches | Платный |

#### Целевые категории

**HIGH VALUE** (стоит Apollo enrichment): dental clinics, law firms, medical practices, real estate agencies, automotive services.
**MEDIUM VALUE:** restaurants/cafes, beauty salons, fitness studios, local retail.

#### Фильтрация

**ACCEPT:** нет сайта, устаревший сайт (< 2020), не mobile-friendly, в целевом городе/регионе.
**REJECT:** современный сайт, сеть/франшиза (корпоративные решения), уже в CRM.

#### Output формат

```json
{
  "business_name": "Стоматология Улыбка",
  "category": "dental",
  "address": "ул. Ленина, 15, Москва",
  "phone": "+7...",
  "has_website": false,
  "google_rating": 4.5,
  "review_count": 45,
  "estimated_value": "high",
  "enrichment_tier": "apollo"
}
```

#### State выход

- Данные сохраняются в таблицу `leads`
- `next_agent="outreach"` при наличии enriched leads

---

### Outreach Agent

**Файлы:** `src/agents/outreach.py`, `src/prompts/outreach.py`
**LLM:** Gemini 3.1 Pro (Tier 2 — Client-facing)
**Класс:** `OutreachAgent(ConstrainedAgent)`

#### Назначение

Cold outreach к локальным бизнесам для проектов цифровизации. Генерация персонализированных писем, управление email-последовательностями.

#### Allowed Tools

`enrich_lead_waterfall`, `generate_cold_email`, `check_email_warmup_status`, `schedule_email`, `track_email_metrics`

#### Enrichment Waterfall (3 уровня)

| Tier | Источник | Стоимость | Когда |
|------|----------|-----------|-------|
| 1 | OSINT (2GIS, Instagram, web scraping) | Бесплатно | Всегда |
| 2 | Hunter.io | $0.01/контакт | Если Tier 1 не дал email |
| 3 | Apollo.io | $0.05/контакт | Только premium niches |

#### Email правила

- Subject: max 50 символов, персонализированный
- Body: max 150 слов
- Обязательно: упоминание конкретной детали бизнеса
- Запрещено: spam-триггеры ("FREE", "ACT NOW", "LIMITED TIME")
- Всегда включать unsubscribe
- Follow-up sequence: 5-7 дней, max 3 письма

#### Email Warm-up Compliance

| Неделя | Лимит писем/день | Назначение |
|--------|-----------------|------------|
| 1-2 | 5-10 | Внутренний warm-up network |
| 3 | 20 | Расширение |
| 4 | 30 | Наращивание |
| 5-6 | 50 | Production ready |

**CRITICAL:** Новые домены требуют 6 недель warm-up перед production outreach.

#### Multi-Channel Outreach (MVP)

Реализован выбор канала через `_select_channel()`:
- **Email** -- основной канал, требует enriched email
- **Telegram** -- альтернативный канал через `TelegramDMSender`

Channel-specific промпты адаптируют тон и формат под канал. `message_dispatch_node` маршрутизирует сообщение к нужному каналу доставки.

#### State выход

- `artifacts["outreach"]` = данные кампании
- HITL обязателен на первой кампании

---

## Notification System (инфраструктурный компонент)

Уведомления -- детерминистическая система маршрутизации алертов, НЕ отдельный LLM-агент.

### Каналы уведомлений

| Канал | Назначение | Реализация |
|-------|------------|------------|
| Telegram | Реалтайм алерты | Bot API |
| Dashboard | Все уведомления | WebSocket |
| Email | Дневные саммари | SMTP |

### Маршрутизация алертов

| Severity | Действие |
|----------|----------|
| `critical` | Telegram немедленно + phone call |
| `error` | Telegram немедленно |
| `warning` | Batch каждые 30 минут |
| `info` | Только dashboard log |

---

## LangGraph State Management

### AgentState TypedDict

```python
Platform = Literal["freelancer", "upwork", "flru", "kwork", "internal", "outreach"]
Status = Literal["active", "paused", "completed", "failed"]
DeliveryType = Literal["files", "credentials", "deploy", "instructions", "mixed"]

class ProjectContext(TypedDict):
    project_id: str
    job_id: str
    platform: Platform
    client: dict[str, Any]
    requirements: str
    budget: float
    deadline: datetime

class AgentState(TypedDict):
    # Identity
    thread_id: str              # Уникальный экземпляр workflow
    mas_checkpoint_id: str      # Текущий checkpoint (НЕ checkpoint_id -- зарезервировано LangGraph!)

    # Project context
    project: ProjectContext

    # Current execution
    current_agent: str          # scout, bid, planner, dev, etc.
    current_task: dict[str, Any] | None

    # Artifacts produced by agents (keyed by agent name)
    artifacts: dict[str, list[str]]

    # Conversation / message history
    messages: list[BaseMessage]

    # ── Control flow ──────────────────────────────────────────────
    next_agent: str | None
    requires_hitl: bool
    hitl_request_id: str | None

    # ── Dynamic routing (Dev Cycle) ──────────────────────────────
    # Устанавливаются Planner, читаются _route_next_in_sequence()
    agent_sequence: list[str]           # ["design", "dev", "content"] — порядок агентов
    delivery_type: DeliveryType | None  # Как сдаём результат клиенту
    current_sequence_index: int         # Текущая позиция в agent_sequence (0-based)

    # ── Revision routing (Critic → targeted agent) ───────────────
    # Устанавливаются Critic, читаются _route_after_critic()
    revision_target: str | None         # "dev" | "design" | "content" — кому вернуть
    revision_severity: str | None       # "minor" | "major"

    # Error handling
    retry_count: int
    errors: list[str]

    # Metadata
    created_at: datetime
    updated_at: datetime
    status: Status
```

### Новые поля: семантика и lifecycle

| Поле | Кто устанавливает | Кто читает | Default | Описание |
|------|-------------------|------------|---------|----------|
| `agent_sequence` | Planner | `_route_next_in_sequence()`, Dashboard | `[]` | Порядок execution-агентов. Пустой = консалтинг (→ Packager) |
| `delivery_type` | Planner | Packager, Dashboard | `None` | Тип сдачи. Определяет логику сборки артефактов |
| `current_sequence_index` | Каждый execution-агент (инкремент) | `_route_next_in_sequence()` | `0` | Позиция в sequence. Когда `>= len(agent_sequence)` → Critic |
| `revision_target` | Critic | `_route_after_critic()` | `None` | Конкретный агент для minor revision (не всегда dev!) |
| `revision_severity` | Critic | `_route_after_critic()` | `None` | `minor` → revision_target, `major` → Planner re-plan |

### create_initial_state() — обновлённая фабрика

```python
def create_initial_state(
    *,
    project: ProjectContext,
    first_agent: str = "scout",
    thread_id: str | None = None,
    user_id: str | None = None,
) -> AgentState:
    now = datetime.now(tz=UTC)
    state: dict[str, Any] = {
        # ... existing fields ...
        "thread_id": thread_id or uuid.uuid4().hex,
        "mas_checkpoint_id": uuid.uuid4().hex,
        "project": project,
        "current_agent": first_agent,
        "current_task": None,
        "artifacts": {},
        "messages": [],
        "next_agent": None,
        "requires_hitl": False,
        "hitl_request_id": None,
        # Dynamic routing (new)
        "agent_sequence": [],
        "delivery_type": None,
        "current_sequence_index": 0,
        # Revision routing (new)
        "revision_target": None,
        "revision_severity": None,
        # Error handling
        "retry_count": 0,
        "errors": [],
        # Metadata
        "created_at": now,
        "updated_at": now,
        "status": "active",
    }
    if user_id is not None:
        state["user_id"] = user_id
    return state
```

### State Size Discipline

**INVARIANT:** `artifacts` хранят **ссылки** (IDs), не контент.

```python
# ПРАВИЛЬНО: artifact = reference
artifacts["dev"] = [artifact_id, "ref:s3://bucket/code.json"]

# НЕПРАВИЛЬНО: artifact = full content
artifacts["dev"] = [artifact_id, json.dumps({"files": [{"path": "...", "content": "5000 lines..."}]})]
```

**Причина:** State передаётся в каждый LLM-вызов через промпт. Полный код в artifacts может overflow context window (128K-200K tokens). Ссылки занимают ~100 bytes вместо ~50KB.

**Max State Size Guard:**

```python
MAX_STATE_SIZE_BYTES = 512_000  # 512 KB

def _check_state_size(state: dict[str, Any]) -> None:
    """Проверка размера state перед checkpoint save."""
    size = len(json.dumps(state, default=str).encode())
    if size > MAX_STATE_SIZE_BYTES:
        logger.error("state_size_exceeded",
            size_bytes=size, limit=MAX_STATE_SIZE_BYTES,
            artifacts_keys=list(state.get("artifacts", {}).keys()),
        )
        raise StateSizeExceededError(
            f"State size {size} exceeds limit {MAX_STATE_SIZE_BYTES}. "
            f"Ensure artifacts contain references, not full content."
        )
```

**Агенты, которые генерируют крупные артефакты (Dev, Content, Design), ДОЛЖНЫ:**
1. Сохранить полный контент в PostgreSQL / S3
2. Записать в `artifacts` только `[artifact_id, reference_string]`
3. Следующий агент загружает контент по reference при необходимости

**CRITICAL:** Использовать `StateGraph(dict)` вместо `StateGraph(AgentState)` -- LangGraph интроспектирует TypedDict fields как channel names. Routing-функции и HITL-ноды должны иметь аннотацию `state: dict[str, Any]`, НЕ `state: AgentState` (иначе state реконструируется из TypedDict schema и accumulated values теряются).

### Dynamic Routing: `_route_next_in_sequence()`

Единая routing-функция, заменяющая 4 отдельных `_route_after_dev`, `_route_after_content`, `_route_after_design`, `_route_after_planner` (для execution phase).

```python
def _route_next_in_sequence(state: dict[str, Any]) -> str:
    """Единая routing функция для execution phase.

    Вызывается после каждого execution-агента (dev, content, design)
    и после Planner (для первого агента в sequence).

    Edge cases:
    - Пустой sequence → Packager (консалтинг)
    - index >= len(sequence) → Critic (все отработали)
    - Невалидный агент в sequence → END + error log
    - status == "failed" → END
    - requires_hitl == True → hitl_review_node (HITL escalation)
    - revision_target set → revision_target_node (Critic revision routing)
    """
    # 1. Terminal states
    if state.get("status") == "failed":
        return END

    # 2. HITL escalation (может быть установлен Planner при >20ч)
    if state.get("requires_hitl"):
        return "hitl_review_node"

    # 3. Revision routing (Critic вернул к конкретному агенту)
    #    Если revision_target установлен, routing идёт через _route_after_critic,
    #    НЕ через эту функцию. Но на случай прямого вызова — safety check:
    revision_target = state.get("revision_target")
    if revision_target:
        target_node = f"{revision_target}_node"
        valid_nodes = {"dev_node", "content_node", "design_node"}
        if target_node in valid_nodes:
            return target_node
        logger.error("invalid_revision_target", target=revision_target)
        return END

    sequence = state.get("agent_sequence", [])
    index = state.get("current_sequence_index", 0)

    # 4. Пустой sequence (консалтинг) → сразу Packager
    if not sequence:
        return "packager_node"

    # 5. Index out of bounds guard
    if index < 0:
        logger.error("negative_sequence_index", index=index)
        return END

    # 6. Все агенты отработали → Critic
    if index >= len(sequence):
        return "critic_node"

    # 7. Следующий агент в sequence
    agent = sequence[index]
    node_name = f"{agent}_node"

    # 8. Валидация: только допустимые execution-агенты
    valid_nodes = {"dev_node", "content_node", "design_node"}
    if node_name not in valid_nodes:
        logger.error("invalid_agent_in_sequence", agent=agent, index=index,
                     sequence=sequence)
        return END

    return node_name
```

**Граф (изменения в `build_full_pipeline_graph`):**

```python
# Planner → unified routing (вместо → dev_node)
graph.add_conditional_edges(
    "planner_node",
    _route_after_planner,   # handles HITL check, then delegates to _route_next_in_sequence
    {
        "dev_node": "dev_node",
        "content_node": "content_node",
        "design_node": "design_node",
        "packager_node": "packager_node",      # консалтинг
        "hitl_review_node": "hitl_review_node", # plan review HITL
        END: END,
    },
)

# Каждый execution-агент → unified routing
for agent_node in ["dev_node", "content_node", "design_node"]:
    graph.add_conditional_edges(
        agent_node,
        _route_next_in_sequence,
        {
            "dev_node": "dev_node",
            "content_node": "content_node",
            "design_node": "design_node",
            "critic_node": "critic_node",
            "packager_node": "packager_node",
            END: END,
        },
    )
```

**`_route_after_planner` (обновлённая):**

```python
def _route_after_planner(state: dict[str, Any]) -> str:
    """Route после Planner: HITL check → затем _route_next_in_sequence."""
    if state.get("status") == "failed":
        return END
    if state.get("requires_hitl"):
        return "hitl_review_node"   # Plan Review HITL
    # Без HITL — делегируем в unified routing
    return _route_next_in_sequence(state)
```

**`_route_after_critic` (обновлённая):**

```python
def _route_after_critic(state: dict[str, Any]) -> str:
    """Route после Critic: approve / revision / escalation."""
    if state.get("status") == "failed":
        return END

    # HITL escalation (reject, scope_creep, revision overflow)
    if state.get("requires_hitl"):
        return "hitl_review_node"

    next_agent = state.get("next_agent")

    # Approve → Packager
    if next_agent == "packager":
        return "packager_node"

    # Major revision → Planner re-plan
    if next_agent == "planner":
        return "planner_node"

    # Minor revision → конкретный агент (revision_target)
    revision_target = state.get("revision_target")
    if revision_target:
        target_node = f"{revision_target}_node"
        if target_node in {"dev_node", "content_node", "design_node"}:
            return target_node

    # Fallback
    return END
```

**Revision flow (minor):**

```
Critic (revision_target="design", revision_severity="minor")
  → design_node (получает artifacts["_critic_feedback"], НЕ инкрементирует index)
  → _route_next_in_sequence (index не изменился, >= len → critic_node)
  → Critic (проверяет заново)
```

**INVARIANT:** При revision (`revision_severity != None`) execution-агент **НЕ инкрементирует** `current_sequence_index`. Это предотвращает:
1. Смещение index за пределы sequence при каждой итерации revision
2. Нарушение семантики index (index должен отражать прогресс, не количество вызовов)

Проверка в каждом execution-агенте:

```python
# В Dev/Content/Design agent _execute():
is_revision = state.get("revision_severity") is not None
if not is_revision:
    new_index = state.get("current_sequence_index", 0) + 1
else:
    new_index = state.get("current_sequence_index", 0)  # НЕ инкрементируем

update_state(state,
    current_agent="dev",
    artifacts={**artifacts, "dev": [code_artifact_id, serialized_code_json]},
    current_sequence_index=new_index,
    revision_target=None,      # Очищаем после обработки
    revision_severity=None,    # Очищаем после обработки
    next_agent=None,
    status="active",
)
```

### Checkpoint Storage (Dual-Layer)

```
LangGraph -> CheckpointSaver -> Valkey (hot cache, TTL 1h) + PostgreSQL (persistent, permanent)
```

| Слой | Назначение | TTL | Use Case |
|------|-----------|-----|----------|
| Valkey | Hot cache | 1 час | Активные workflows, быстрый доступ |
| PostgreSQL | Persistent | Permanent | Recovery, audit, history |

`HybridCheckpointSaver(BaseCheckpointSaver)`:
- `aget()` -- сначала Valkey, fallback на PostgreSQL (cache miss -> warm cache)
- `aput()` -- запись в оба хранилища + checkpoint history для rollback

### Thread Isolation

Каждый проект/workflow получает уникальный `thread_id`. Агенты stateless -- могут работать над несколькими проектами одновременно. `HybridCheckpointSaver` изолирует state по `thread_id`.

### Recovery Procedures

#### Resume from HITL Pause

1. Получить latest checkpoint по `thread_id`
2. Обновить state с HITL response: `requires_hitl=False`
3. Возобновить `graph.astream()` с обновленным state

#### Recover from Crash

1. SELECT все workflows с `status NOT IN ('completed', 'failed')`
2. Для каждого -- resume `graph.ainvoke()` из последнего checkpoint

#### Rollback

1. SELECT checkpoint из `langgraph_checkpoint_history` с `OFFSET N`
2. Восстановить как текущий state через `saver.aput()`
3. History хранит последние 10 checkpoints на thread (cleanup trigger)

### CRITICAL: ainvoke vs astream

`ainvoke()` может вернуть НАЧАЛЬНЫЙ state вместо финального для `StateGraph(dict)` в LangGraph 1.0.8. Workaround: использовать `astream(stream_mode="values")` и брать последний emitted value.

### Конфигурация

```python
LANGGRAPH_CONFIG = {
    "checkpoint": {
        "valkey_url": "valkey://localhost:6379/1",
        "postgres_url": "postgresql://mas:password@localhost/mas",
        "valkey_ttl_hours": 1,
        "max_history_per_thread": 10,
    },
    "recovery": {
        "auto_recover_on_startup": True,
        "max_retry_count": 3,
    }
}
```

---

## Agent Summary Matrix

| Agent | LLM | Pipeline | Primary Role | HITL |
|-------|-----|----------|-------------|------|
| Scout | Gemini 2.5 Flash (T5) | A | Job discovery | Нет (HITL для review band) |
| Bid | Gemini 3.1 Pro (T2) | A | Proposal generation | **Да, всегда** |
| Planner | Claude Opus 4.6 | A | Task decomposition | Условно (hours >= 20) |
| Dev | Claude Opus 4.6 | A | Code generation | Нет |
| Content | Claude Sonnet 4.6 (T3) | A | Copywriting | Нет |
| Design | NanoBanana Pro (T4) | A | Graphics/UI specs | Нет |
| Critic | Claude Sonnet 4.6 (T3) | A | Quality gate | Условно (reject, scope_creep) |
| Packager | DeepSeek V3.2 (T6) | A | Final delivery | **Да, всегда** |
| GeoScout | Gemini 2.5 Flash (T5) | B | Lead discovery | Нет |
| Outreach | Gemini 3.1 Pro (T2) | B | Cold outreach | На первой кампании |
