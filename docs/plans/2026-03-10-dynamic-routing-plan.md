# Dynamic Routing Implementation Plan

> **Branch:** `auto/2026-03-10/full-implementation`
> **Priority:** #1 (первая задача после Docs-First Gate)
> **Spec sources:** `dev-cycle-spec.md`, `agents-spec.md` Section "Dynamic Routing", `MASTER-VISION.md` Resolution #27

## Цель

Заменить хардкоженную цепочку `dev → content → design → critic` на динамическую последовательность `agent_sequence`, управляемую Planner. Это позволяет:
- API/backend проекты: `["dev"]` (без design/content)
- Design-first: `["design", "dev", "content"]`
- Консалтинг: `[]` → сразу Packager

## Архитектура (до и после)

### До (текущее)
```
Planner →(next_agent="dev")→ Dev →(next_agent="content")→ Content →(next_agent="design")→ Design →(next_agent="critic")→ Critic
```
Каждый агент хардкодит `next_agent`. 4 отдельных routing-функции: `_route_after_dev`, `_route_after_content`, `_route_after_design` + часть `_route_after_planner`.

### После (новое)
```
Planner →(sets agent_sequence)→ _route_next_in_sequence() → agent_sequence[0] → ... → agent_sequence[N] → Critic
```
Одна routing-функция для всех execution-агентов. Агенты инкрементируют `current_sequence_index`, routing читает sequence[index].

## Изменения по файлам

### 1. `src/core/state.py` — Новые поля

**Добавить в `AgentState` TypedDict:**
```python
# Dynamic routing (Dev Cycle)
agent_sequence: list[str]          # ["design", "dev", "content"]
current_sequence_index: int        # 0, 1, 2, ...
delivery_type: str                 # "files" | "credentials" | "deploy" | "instructions" | "mixed"
revision_target: str | None        # "dev" | "design" | "content"
revision_severity: str | None      # "minor" | "major"
```

**Обновить `create_initial_state()`:**
```python
"agent_sequence": [],
"current_sequence_index": 0,
"delivery_type": "files",
"revision_target": None,
"revision_severity": None,
```

**Решение:** Значения по умолчанию — пустые/нулевые. Planner заполняет при выполнении.

### 2. `src/core/graph.py` — Routing

#### 2a. Добавить `_route_next_in_sequence()`
```python
def _route_next_in_sequence(state: dict[str, Any]) -> str:
    """Единая routing функция для execution phase."""
    if state.get("status") == "failed":
        return END

    # HITL escalation
    if state.get("requires_hitl"):
        return "hitl_review_node"

    # Revision routing (safety check — обычно через _route_after_critic)
    revision_target = state.get("revision_target")
    if revision_target:
        target_node = f"{revision_target}_node"
        if target_node in {"dev_node", "content_node", "design_node"}:
            return target_node
        logger.error("invalid_revision_target", target=revision_target)
        return END

    sequence = state.get("agent_sequence", [])
    index = state.get("current_sequence_index", 0)

    # Пустой sequence (консалтинг) → сразу Packager
    if not sequence:
        return "packager_node"

    # Bounds guard
    if index < 0:
        logger.error("negative_sequence_index", index=index)
        return END

    # Все агенты отработали → Critic
    if index >= len(sequence):
        return "critic_node"

    # Следующий агент
    agent = sequence[index]
    node_name = f"{agent}_node"
    valid = {"dev_node", "content_node", "design_node"}
    if node_name not in valid:
        logger.error("invalid_agent_in_sequence", agent=agent)
        return END

    return node_name
```

#### 2b. Обновить `_route_after_planner()`
```python
def _route_after_planner(state: dict[str, Any]) -> str:
    if state.get("status") == "failed":
        return END
    if state.get("requires_hitl"):
        return "hitl_review_node"
    # Делегируем в unified routing
    return _route_next_in_sequence(state)
```

#### 2c. Обновить `_route_after_critic()` — добавить `revision_target`
```python
# Minor revision → конкретный агент (revision_target)
revision_target = state.get("revision_target")
if revision_target:
    target_node = f"{revision_target}_node"
    if target_node in {"dev_node", "content_node", "design_node"}:
        return target_node
```

#### 2d. Удалить `_route_after_dev`, `_route_after_content`, `_route_after_design`

#### 2e. Обновить graph builders
Заменить 3 individual conditional_edges на loop:
```python
for agent_node in ["dev_node", "content_node", "design_node"]:
    graph.add_conditional_edges(
        agent_node,
        _route_next_in_sequence,
        {"dev_node": "dev_node", "content_node": "content_node",
         "design_node": "design_node", "critic_node": "critic_node",
         "packager_node": "packager_node", "hitl_review_node": "hitl_review_node",
         END: END},
    )
```

Обновить Planner edges:
```python
graph.add_conditional_edges(
    "planner_node",
    _route_after_planner,
    {"dev_node": "dev_node", "content_node": "content_node",
     "design_node": "design_node", "packager_node": "packager_node",
     "hitl_review_node": "hitl_review_node", END: END},
)
```

Обновить Critic edges:
```python
graph.add_conditional_edges(
    "critic_node",
    _route_after_critic,
    {"packager_node": "packager_node", "dev_node": "dev_node",
     "content_node": "content_node", "design_node": "design_node",
     "planner_node": "planner_node", "hitl_review_node": "hitl_review_node",
     END: END},
)
```

### 3. Execution-агенты — убрать хардкод `next_agent`

#### `src/agents/dev.py`
- Убрать `next_agent="content"`
- Добавить: `current_sequence_index` инкремент (если не revision)
- Добавить: `revision_target=None`, `revision_severity=None` (очистка после revision)

#### `src/agents/content.py`
- Убрать `next_agent="design"`
- Добавить: `current_sequence_index` инкремент

#### `src/agents/design.py`
- Убрать `next_agent="critic"`
- Добавить: `current_sequence_index` инкремент

#### `src/agents/planner.py`
- Убрать `next_agent="dev"`
- Добавить: `agent_sequence` fallback `["dev", "content", "design"]` (backward compat)
- Добавить: `delivery_type` default `"files"`

#### `src/agents/critic.py`
- Добавить: `revision_target` при minor revision (вместо только `next_agent="dev"`)
- Добавить: `revision_severity` в state

### 4. Backward Compatibility

**Стратегия:** Если `agent_sequence` отсутствует в state — Planner fallback на `["dev", "content", "design"]` (текущее поведение).

**Существующие tests:** Тесты, которые создают state без `agent_sequence`, будут работать через fallback в Planner.

**`build_planner_pipeline_graph`:** Также обновляется (использует те же routing-функции).

## Порядок имплементации (TDD)

### Step 1: Tests RED
1. Написать тесты для `_route_next_in_sequence()` (12+ тестов)
2. Обновить тесты `_route_after_planner` (delegation)
3. Обновить тесты `_route_after_critic` (revision_target)
4. Написать тесты для новых state полей
5. Удалить тесты `_route_after_dev/content/design`

### Step 2: State GREEN
Добавить поля в `state.py` → тесты state проходят

### Step 3: Routing GREEN
Добавить `_route_next_in_sequence`, обновить `_route_after_planner`, `_route_after_critic` → тесты routing проходят

### Step 4: Graph GREEN
Обновить graph builders → тесты компиляции проходят

### Step 5: Agents GREEN
Обновить execution-агенты → все тесты проходят

### Step 6: Integration
Проверить `build_full_pipeline_graph`, `build_planner_pipeline_graph` компилируются и работают

## Риски и митигация

| Риск | Митигация |
|------|-----------|
| Planner ещё не генерирует `agent_sequence` | Fallback на `["dev", "content", "design"]` в Planner `_execute()` |
| Бесконечный цикл если агент не инкрементирует index | Max iterations guard: `_last_sequence_agent` в state для loop detection |
| Integration tests ломаются | Обновить mock state в integration tests с новыми полями |
| `_route_after_hitl_review` → `"dev_node"` при plan_review | Обновить на `_route_next_in_sequence` delegation |

## Файлы для изменения (итого)

| Файл | Действие | Строк ~change |
|------|----------|---------------|
| `src/core/state.py` | Добавить 5 полей | +15 |
| `src/core/graph.py` | Новая routing func + обновить 3 routing + обновить 3 graph builders | +60, -80 |
| `src/agents/planner.py` | `agent_sequence` fallback, убрать `next_agent="dev"` | +10, -2 |
| `src/agents/dev.py` | Index increment, убрать `next_agent="content"` | +8, -2 |
| `src/agents/content.py` | Index increment, убрать `next_agent="design"` | +8, -2 |
| `src/agents/design.py` | Index increment, убрать `next_agent="critic"` | +8, -2 |
| `src/agents/critic.py` | `revision_target`, `revision_severity` | +10, -2 |
| `tests/unit/test_graph_routing.py` | Переписать routing tests | +80, -40 |
| `tests/unit/test_dynamic_routing.py` | Новый файл с 15+ тестов | +200 |

## NOT in scope (отдельные задачи)

- Planner prompt update (генерация `agent_sequence`)
- HITL `dev_launch` node (#2 HITL gate)
- `delivery_type` logic в Packager
- Execution Cloaking
- Dashboard updates для sequence visualization
