# Dev Cycle Engine: Единая спецификация (source of truth)

> Как система превращает заказ в готовый продукт.
> При противоречии с pipeline-a-spec/pipeline-b-spec -- приоритет здесь для routing/revision.
> Дата: 2026-03-06

---

## Полный flow

```
BID → [HITL bid_approval] → bid_submission → [HITL dev_launch] →
PLANNER → [HITL plan_review?] → agent_sequence[0] → ... → agent_sequence[N] → CRITIC
→ [revision?] → PACKAGER → [HITL final_review] → СДАЧА
```

**HITL dev_launch** — обязательная точка между bid submission и Planner. Оператор явно решает, запускать ли разработку. Dev-цикл НИКОГДА не стартует автоматически.

---

## Фаза 1: Планирование (Planner)

**Что получает:**
- `requirements` -- ТЗ от клиента (из job + bid context)
- `client_messages` -- переписка если была (Pipeline A после диалога, Pipeline B после SalesAgent)
- `capability_report` -- что MAS умеет для этого заказа (от Scout)
- RAG-память -- похожие проекты (сроки, подходы, что пошло не так)

**Что возвращает:**

| Поле | Тип | Описание |
|------|-----|----------|
| `agent_sequence` | `list[str]` | Порядок агентов: `["design", "dev", "content"]` |
| `tasks` | `list[dict]` | Задачи с agent assignment, описанием, часами (≤ 4ч каждая) |
| `delivery_type` | `str` | `files` / `credentials` / `deploy` / `instructions` / `mixed` |
| `estimated_hours` | `float` | Суммарная оценка |

**Пустой sequence** = консалтинг. План сам является deliverable → сразу к Packager.

**HITL Plan Review** если:
- `estimated_hours > 20`
- Re-plan после Critic major revision
- Оператор вручную запросил ревью

**LLM:** Claude Opus 4.6 (Tier 1 — Reasoning).

**Файл:** `src/agents/planner.py`

---

## Фаза 2: HITL Plan Review

**Когда:** `estimated_hours > 20` или re-plan.

**Что показывает оператору:**
- `agent_sequence` -- какие агенты и в каком порядке
- `tasks[]` -- декомпозиция с часами
- `delivery_type` -- как будем сдавать
- `estimated_hours` -- общая оценка

**Действия оператора:**

| Действие | Результат |
|----------|-----------|
| Approve | → execution (agent_sequence[0]) |
| Edit | Убрать/добавить агентов, скорректировать задачи, поменять delivery_type → approve |
| Reject | Planner переделывает план (replan_count++) |

**Канал:** Dashboard + TG-бот.

**Файл:** `src/core/graph.py` (hitl_review_node + `_apply_plan_review`)

---

## Фаза 3: Execution (agent_sequence)

Агенты работают последовательно по `agent_sequence`.

**State поля:**
```
state["agent_sequence"] = ["design", "dev", "content"]
state["delivery_type"] = "deploy"
state["current_sequence_index"] = 0
```

**Поток:**
1. `_route_next_in_sequence()` читает `agent_sequence[current_sequence_index]`
2. Запускает соответствующий агент (design_node / dev_node / content_node)
3. Агент отрабатывает, инкрементирует `current_sequence_index`
4. Если есть ещё агенты → следующий. Если нет → critic_node

**Агенты НЕ хардкодят `next_agent`** -- только инкрементируют index.

**Каждый агент получает:**
- Артефакты предыдущих агентов (`artifacts`)
- План (`artifacts["planner"]`)
- Свои задачи из `tasks[]` (отфильтрованные по `agent` == self.agent_name)

**Dashboard:** pipeline progress bar в реальном времени (WebSocket), текущий агент, задачи (done/total).

### Типы проектов (routing)

| Тип проекта | agent_sequence | delivery_type | Пример |
|-------------|----------------|---------------|--------|
| Лендинг / сайт | [design, dev, content] | deploy | "React лендинг для стоматологии" |
| TG-бот / API | [dev] | files / credentials | "Telegram бот для записи" |
| Брендбук / концепт | [design] | files | "Логотип + фирменный стиль" |
| Контент-проект | [content] | files | "Тексты для сайта" |
| Консалтинг | [] | instructions | "Аудит + рекомендации" |
| Полный продукт | [design, dev, content] | mixed | "CRM под ключ" |
| Кастомный | Planner решает | varies | "AI-инфографика" |

### Файлы агентов

| Агент | Файл | LLM |
|-------|------|-----|
| Dev | `src/agents/dev.py` | DeepSeek V3.2 |
| Content | `src/agents/content.py` | DeepSeek V3.2 |
| Design | `src/agents/design.py` | NanoBanana Pro (Gemini 3 Pro) |

---

## Фаза 4: Critic Review

Critic оценивает **ВСЕ** артефакты (не только последнего агента).

**Процесс:**
1. Semgrep scan (fail-closed) -- если semgrep недоступен → blocked
2. LLM ревью: код, контент, дизайн -- всё в одном запросе
3. Score + verdict + issues

**Определяет:**

| Поле | Тип | Описание |
|------|-----|----------|
| `score` | `float` | 0.0 -- 1.0 |
| `revision_severity` | `str` | `minor` / `major` |
| `revision_target` | `str` | `dev` / `design` / `content` (конкретный агент) |
| `issues` | `list[dict]` | Описание проблем |

**Routing:**

| Условие | Действие |
|---------|----------|
| `score >= 0.85` | → Packager (approve) |
| `0.60 <= score < 0.85`, minor | → `revision_target` напрямую (макс 2 попытки) |
| `0.60 <= score < 0.85`, major | → Planner (re-plan + HITL) |
| `score < 0.60` | → HITL эскалация |
| 2+ ревизий одного агента | → HITL эскалация |

**LLM:** Claude Sonnet 4.6 (Tier 3 — Content+Review).

**Файл:** `src/agents/critic.py`

---

## Фаза 4.5: Execution Cloaking (маскировка сроков)

> Источник: `docs/Full_work/vision/pipeline-a/ideas/A` (Time-Value Arbitrage)

Клиенты оценивают работу через призму человеко-часов. AI делает за часы то, что человек делает за дни. Отдать мгновенно = убить доверие. Задача — имитировать ритм работы профессионала.

### Двойная эстимация (Planner + Bid Agent)

| Шаг | Кто | Что делает |
|-----|-----|-----------|
| 1 | Planner | Рассчитывает реальное время AI: `real_hours` (напр. 2ч) |
| 2 | Bid Agent | Оценивает как Middle-разработчик: `human_days` (напр. 10 дней) |
| 3 | Bid Agent | Предлагает клиенту: `human_days / 2..3` (напр. 3-4 дня) |

**Правила:**
- Минимум 24 часа — никогда не предлагать меньше
- Bid Agent **не знает** реальный `real_hours` — в промпте жёстко: "Оценивай как человек"
- Только Fixed Price (не почасовая оплата)

### Ценовое позиционирование

- Себестоимость (API токены) → ~$0.5-5 за проект
- Прайс: 10-20% ниже рынка (выигрываем тендер, но не демпингуем)
- Маржа: 90%+ на каждом проекте

### Delivery Throttling (Orchestrator + Packager)

Даже если код написан за 45 минут — отдаём по расписанию:

```
[Заявленный срок: 3 дня]

+2ч:    Scheduled Message → клиенту:
        "Развернул архитектуру, настроил базу. Вопрос по ТЗ:
         [LLM генерирует умный технический вопрос]"

+1 день: Scheduled Message → клиенту:
         "Бэкенд готов, натягиваю фронт. Завтра пришлю на ревью."

+2 дня:  Packager → HITL → сдача клиенту
         (≥ 70% заявленного срока должно пройти)
```

**Реализация:**
- `Scheduler` node в LangGraph — замораживает доставку до timestamp
- `scheduled_messages[]` в state — очередь промежуточных сообщений
- `min_delivery_at` = `created_at + proposed_days * 0.7` — Packager не отдаёт раньше
- Сообщения генерируются LLM с контекстом проекта (не шаблонные)

### Легенда

Если клиент спрашивает "как так быстро?":
> "У нас собственная проприетарная библиотека компонентов, CLI-инструменты и CI/CD пайплайны, которые мы собирали годами. Это позволяет пропускать рутину и фокусироваться на бизнес-логике."

Технически — чистая правда: MAS = этот инструмент.

---

## Фаза 5: Сдача (Packager)

Packager получает `delivery_type` от Planner.

| delivery_type | Что собирает | Что получает клиент |
|---------------|-------------|---------------------|
| `files` | Код, дизайн, контент → архив | ZIP / GitHub repo |
| `credentials` | Настроенный сервис + доступы | Логин/пароль + инструкция |
| `deploy` | Деплой на хостинг клиента | Ссылка + настройки |
| `instructions` | Документация + гайды | PDF / страница с инструкцией |
| `mixed` | Комбинация выше | Пакет: файлы + доступы + инструкция |

**HITL (ОБЯЗАТЕЛЬНЫЙ):** approve / request_changes / reject. Показывает preview того что получит клиент.

**После approve:**
- Доставка клиенту через адаптер платформы
- (Будущее) Portfolio Agent автосохраняет в портфолио

**LLM:** Claude Haiku 4.5.

**Файл:** `src/agents/packager.py`

---

## Dynamic Routing (техническое)

### State поля (новые)

```python
# В AgentState / state dict:
"agent_sequence": list[str]        # ["design", "dev", "content"]
"delivery_type": str                # "files" | "credentials" | "deploy" | "instructions" | "mixed"
"current_sequence_index": int       # 0, 1, 2, ...
"revision_target": str | None       # "dev" | "design" | "content"
"revision_severity": str | None     # "minor" | "major"
```

### Routing функция (заменяет 4 отдельных _route_after_*)

```python
def _route_next_in_sequence(state: dict[str, Any]) -> str:
    """Единая routing функция для execution phase."""
    if state.get("status") == "failed":
        return END

    sequence = state.get("agent_sequence", [])
    index = state.get("current_sequence_index", 0)

    # Все агенты отработали → Critic
    if index >= len(sequence):
        return "critic_node"

    # Пустой sequence (консалтинг) → сразу Packager
    if not sequence:
        return "packager_node"

    agent = sequence[index]
    return f"{agent}_node"
```

### Граф (изменения)

**Было:** `planner → dev → content → design → critic` (4 conditional edges с жёсткими маршрутами).

**Стало:** `planner → [unified routing] → critic` (1 conditional edge + loop).

```python
# Каждый агент (dev, content, design) → _route_next_in_sequence
for agent_name in ["dev_node", "content_node", "design_node"]:
    graph.add_conditional_edges(
        agent_name,
        _route_next_in_sequence,
        {"dev_node": "dev_node", "content_node": "content_node",
         "design_node": "design_node", "critic_node": "critic_node",
         "packager_node": "packager_node", END: END},
    )
```

---

## Revision Loop (техническое)

### Critic output

```python
# Critic sets in state:
"revision_target": "design"     # конкретный агент
"revision_severity": "minor"    # minor | major
```

### Minor revision

```
Critic → _route_after_critic → revision_target_node → _route_next_in_sequence
    (current_sequence_index = len(sequence) → critic_node again)
```

Агент получает `artifacts["_critic_feedback"]` -- что именно исправить.
После исправления → назад к Critic (`current_sequence_index` уже за пределами sequence → critic).

### Major revision

```
Critic → _route_after_critic → planner_node
    (Planner получает feedback + existing artifacts → новый план)
    → HITL Plan Review → execution заново
```

### Escalation

```
revision_count >= 2 ИЛИ score < 0.60
    → requires_hitl=True → hitl_escalation_node
    Оператор: "Critic 2 раза вернул к Dev. Issues: [...]"
    Действия: Force approve / Manual fix / Re-plan
```

---

## Partial Failure Recovery

### Что происходит

Агент бросает exception → `status="failed"` в state. Артефакты предыдущих агентов **сохранены** в `artifacts`.

### HITL

```
"Design Agent failed at task 'Mockup главной'.
 Ошибка: LLM timeout after 90s.
 Артефакты Dev: сохранены (4 файла).
 Артефакты Content: не начаты."
```

### Действия

| Действие | Результат | State changes |
|----------|-----------|---------------|
| Resume | Перезапуск агента с тем же контекстом | `status="active"`, same index |
| Skip | Переход к следующему в sequence | `current_sequence_index++`, `status="active"` |
| Manual | Оператор берёт задачу, помечает done | `current_sequence_index++`, `status="active"`, manual artifacts |

---

## Pipeline B -> Dev Cycle

### Trigger

`deal.status='won'` (SalesAgent закрыл сделку, клиент утвердил концепцию).

### API

`POST /api/v1/deals/{id}/start-development`

### Процесс

1. API загружает deal + client_context + design_versions
2. Создаёт `ProjectContext` с:
   - `requirements` = `deal.agreed_scope`
   - `client_messages` = из deal переписки
   - `design_versions` = если дизайн уже утверждён на фазе 5 Pipeline B
3. Вызывает `build_planner_pipeline_graph()` → запускает Pipeline A
4. Planner строит план с учётом того что уже согласовано:
   - Если дизайн утверждён → `agent_sequence` без design
   - Если scope чёткий → меньше часов → без HITL Plan Review

### Связь

`deal.pipeline_a_thread_id` -- ссылка на Pipeline A run из Pipeline B deal.

---

## Категории заказов (Scout)

### Settings UI

Два столбца чеклистов:

**"AI берёт сам"** (автономная обработка):
- Лендинги
- TG/Discord боты
- API/Backend
- Дизайн (баннеры, логотипы)
- Контент (тексты, переводы)
- Мелкие правки (фиксы, доработки)

**"Предложить мне"** (HITL):
- Мобильные приложения
- ML/AI проекты
- DevOps/инфраструктура
- Консалтинг
- E-commerce (интернет-магазины)
- Системная интеграция

### Свободные текстовые запросы

Помимо чеклистов — поле для произвольных правил на естественном языке:

```
"Если в заказе Figma — брать"
"Всё что связано с парсерами — предложить мне"
"Заказы до $50 — пропускать"
"React + TypeScript — всегда брать"
"Если клиент просит мобильное приложение и бюджет > $500 — предложить мне"
```

**Реализация:** Scout передаёт свободные запросы в LLM вместе с описанием заказа. LLM интерпретирует правила и возвращает `action: "take" | "suggest" | "skip"` с объяснением какое правило сработало.

Хранятся в `settings` таблице (key=`scout_custom_rules`, value=JSONB array of strings).

### RAG-обучение

Scout дополняет настройки из RAG: какие заказы оператор исторически берёт/отклоняет. Со временем категории мигрируют между столбцами, а свободные запросы уточняются.

### Реализация категорий

Настройки хранятся в `settings` таблице:
- `scout_categories` (JSONB) — чеклисты двух столбцов
- `scout_custom_rules` (JSONB) — массив свободных текстовых правил

Scout проверяет при скоринге: если категория в "предложить мне" → score_modifier -0.2 → попадает в HITL. Свободные правила применяются через LLM и могут override категории.

---

## Параллельность

### Текущее

- 3-5 заказов одновременно
- `thread_id` isolation (каждый заказ -- отдельный граф)
- `HybridCheckpointSaver` (Valkey + PostgreSQL)

### Ограничения

- OpenRouter rate limits (per-model RPM)
- Docker sandbox slots (Dev Agent)
- LLM context window (один агент -- один LLM call за раз)

### Будущее: LLM Request Queue

| Priority | Задачи | Ожидание |
|----------|--------|----------|
| HIGH | HITL-зависимые (оператор ждёт ответа) | < 5с |
| NORMAL | Обычная работа агентов | < 30с |
| LOW | Batch (Scout scan, email кампании) | < 5мин |

---

## Dashboard

### Job Detail

```
┌──────────────────────────────────────────────────────┐
│  Заказ: "React Landing"   Platform: Freelancer        │
│  Status: В работе  │  delivery_type: deploy           │
│                                                       │
│  Pipeline: [Design ✓] → [Dev ██░] → [Content ░░]     │
│  Текущий агент: Dev  │  Revision: 0/2                 │
│                                                       │
│  Задачи:                                             │
│  ✓ Mockup главной (Design, 2ч)                       │
│  ► React компоненты (Dev, 3ч) -- 60%                 │
│  ○ API endpoints (Dev, 2ч)                           │
│  ○ Тексты для лендинга (Content, 1ч)                 │
└──────────────────────────────────────────────────────┘
```

### Home

```
┌──────────────────────────────────────────────────────┐
│  HITL PENDING: 2                                      │
│  🔴 Plan Review: "CRM под ключ" (32ч)               │
│  🔴 Final Delivery: "TG-бот записи"                 │
│                                                       │
│  АКТИВНЫЕ ЗАКАЗЫ: 3                                  │
│  "React landing"  → Dev ████░░    [Pipeline A]       │
│  "Брендбук"       → Design ██░░   [Pipeline A]       │
│  "Улыбка"         → SalesAgent    [Pipeline B]       │
│                                                       │
│  АГЕНТЫ:                                             │
│  Scout: idle | Dev: coding | Design: working          │
│  Content: idle | Critic: idle | Packager: idle        │
└──────────────────────────────────────────────────────┘
```

---

## HITL точки

| # | Момент | Обязательна? | Trigger | Действия | Канал |
|---|--------|-------------|---------|----------|-------|
| 1 | **Dev Launch** | **ДА, всегда** | Бид отправлен | start_development / negotiate_more / decline | Dashboard + TG-бот |
| 2 | Plan Review | Условно | >20ч или re-plan | approve / edit / reject | Dashboard + TG-бот |
| 3 | Revision Escalation | Условно | 2+ ревизий или score < 0.60 | force approve / manual / re-plan | Dashboard + TG-бот |
| 4 | Partial Failure | Условно | Агент exception | resume / skip / manual | Dashboard + TG-бот |
| 5 | **Final Delivery** | **ДА, всегда** | Packager done | approve / request_changes / reject | Dashboard + TG-бот |

---

## Ключевые файлы

| Файл | Роль | Изменения |
|------|------|-----------|
| `src/agents/planner.py` | Генерация плана | + agent_sequence, delivery_type, existing artifacts awareness |
| `src/agents/dev.py` | Код | - убрать hardcoded next_agent, + index increment |
| `src/agents/content.py` | Контент | - убрать hardcoded next_agent, + index increment |
| `src/agents/design.py` | Дизайн | - убрать hardcoded next_agent, + index increment |
| `src/agents/critic.py` | Ревью | + revision_target, revision_severity |
| `src/agents/packager.py` | Сдача | + delivery_type handling |
| `src/core/graph.py` | Граф | Заменить 4 routing функции на 1 `_route_next_in_sequence` |
| `src/core/state.py` | State | + agent_sequence, delivery_type, current_sequence_index, revision_target |
| `src/api/routes/hitl.py` | HITL API | + plan_review edit (sequence editing) |
| `src/api/routes/jobs.py` | Jobs API | + pipeline progress endpoint |
| `src/prompts/planner.py` | Промпт | + agent_sequence + delivery_type в output schema |
| `src/prompts/critic.py` | Промпт | + revision_target + revision_severity в output schema |
| `dashboard/app/routes/_app.jobs_.$id.tsx` | Job Detail | + pipeline progress bar, task list |
| `dashboard/app/routes/_app.tsx` | Dashboard Home | + active orders widget, agent status |
| `dashboard/app/routes/_app.settings.tsx` | Settings | + Scout категории (2 столбца) |

---

## Приоритеты реализации

### P0 -- Routing (блокирует всё остальное)

| # | Задача | Effort | Impact |
|---|--------|--------|--------|
| 1 | `agent_sequence` + `_route_next_in_sequence()` в graph.py | 4ч | Критический -- без этого все заказы идут через dev→content→design |
| 2 | Убрать hardcoded `next_agent` из dev/content/design | 2ч | Необходимо для P0.1 |
| 3 | `revision_target` в Critic (targeted revision) | 3ч | Критический -- без этого баг в design → dev пытается чинить |

### P1 -- Ядро видения

| # | Задача | Effort | Impact |
|---|--------|--------|--------|
| 4 | `delivery_type` в Planner + Packager | 3ч | Высокий -- разные типы проектов = разные deliverables |
| 5 | Partial failure recovery (HITL: Resume/Skip/Manual) | 4ч | Высокий -- сейчас агент упал = END |
| 6 | Execution Cloaking: двойная эстимация + Delivery Throttling | 4ч | Высокий -- без этого клиент видит AI-скорость |
| 7 | HITL Plan Review edit (sequence editing) | 3ч | Средний -- оператор может убрать ненужного агента |
| 8 | Planner при re-plan видит existing artifacts | 2ч | Средний -- не переделывает то что уже сделано |

### P2 -- Dashboard + UX

| # | Задача | Effort | Impact |
|---|--------|--------|--------|
| 9 | Pipeline B → A (`POST /api/v1/deals/{id}/start-development`) | 4ч | Высокий -- склейка двух пайплайнов |
| 10 | Dashboard pipeline progress (bar, текущий агент, задачи) | 6ч | Высокий -- оператор видит что происходит |
| 11 | Scout категории + свободные текстовые запросы в Settings | 4ч | Средний -- настройка автоматизации |

### P3 -- Оптимизация

| # | Задача | Effort | Impact |
|---|--------|--------|--------|
| 12 | LLM request queue (priority routing) | 6ч | Средний -- нужен при 3+ заказах |
| 13 | RAG-память (паттерны решений) | 8ч | Средний -- улучшает качество со временем |
| 14 | Pipeline timeout (зависший агент → HITL) | 2ч | Низкий -- heartbeat частично покрывает |
