# Тестирование: Спецификация

> Полная стратегия тестирования MAS -- 2330+ тестов, пирамида unit/integration/e2e/load/property/golden_set, pytest + pytest-asyncio. Обеспечивает регрессионную защиту 10 агентов, 2 пайплайнов, API, Telegram-бота, browser-стека и всей инфраструктуры.

## Назначение

Гарантировать корректность, стабильность и безопасность системы на всех уровнях: от отдельных агентов до полного end-to-end пайплайна. Каждый PR проходит ruff + pytest; coverage gate 70% для unit-тестов. Тесты изолированы от внешних сервисов через моки Valkey, DB, LLM, Semgrep и Sandbox.

## Стратегия тестирования

### Пирамида

```
         ┌──────────────┐
         │   E2E (2%)   │  ← Playwright, полный стек
         ├──────────────┤
         │ Integration  │  ← 10% (multi-agent flows, DB, граф)
         │   (10%)      │
         ├──────────────┤
         │  Unit (88%)  │  ← Быстрые, изолированные, мокнутые
         └──────────────┘
```

### Уровни

| Уровень | Файлов | Тестов | Coverage Gate | Инфраструктура |
|---------|-------:|-------:|---------------|----------------|
| Unit | 99 | ~2100+ | 70% min | Моки (нет сети, нет БД) |
| Integration | 22 | 76+ | -- | PostgreSQL + Valkey (тестовые) |
| E2E | 3 | 39 | -- | Полный стек + Playwright |
| Load | 4 | n/a | -- | Locust, 50 concurrent users |
| Property | отд. папка | -- | -- | Hypothesis |
| Golden Set | отд. папка | -- | -- | LLM regression benchmarks |

### Цели покрытия

| Модуль | Target | Приоритет |
|--------|--------|-----------|
| `agents/scout.py` | 90% | Critical |
| `agents/bid.py` | 90% | Critical |
| `agents/planner.py` | 80% | High |
| `agents/dev.py` | 80% | High |
| `core/` (state, heartbeat, llm_client) | 85% | Critical |
| `api/` | 70% | Medium |
| `utils/` | 60% | Low |

## Паттерны тестов

### Unit-тесты

Каждый unit-тест изолирован от внешних сервисов. LLM, Valkey, DB, Semgrep, Sandbox -- всё мокнуто через `conftest.py`.

**Паттерн агента:**

```python
# tests/unit/test_scout_agent.py
class TestScoutAgent:
    @pytest.mark.asyncio
    async def test_filters_low_budget_jobs(self, mock_llm_client, mock_valkey):
        agent = ScoutAgent(llm_client=mock_llm_client)
        state = create_initial_state(project=sample_project, first_agent="scout")
        result = await agent._execute(state)
        assert result["next_agent"] == "bid"
        assert result["status"] == "active"
```

**Паттерн API route:**

```python
# tests/unit/test_api_routes_jobs.py
class TestJobRoutes:
    async def test_list_jobs_with_filter(self, test_client, mock_db_session):
        mock_db_session.execute.return_value.scalars.return_value.all.return_value = [...]
        response = await test_client.get("/api/jobs?platform=freelancer")
        assert response.status_code == 200
```

**Паттерн state-проверки:**

```python
# Создание state через create_initial_state(), НЕ вручную
state = create_initial_state(project=sample_project, first_agent="scout", thread_id="test-001")
# Мутация ТОЛЬКО через update_state()
result = update_state(state, next_agent="bid", status="active")
# Проверка: next_agent, status, artifacts, requires_hitl
assert result["requires_hitl"] is False
```

### Integration-тесты

Тестируют взаимодействие нескольких компонентов: граф LangGraph, checkpoint persistence, multi-agent flows.

```python
# tests/integration/test_full_pipeline.py
class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_scout_to_bid_handoff(self):
        graph = _build_pipeline_graph(nodes={"scout": mock_scout, "bid": mock_bid})
        compiled = graph.compile()
        # Используй astream, НЕ ainvoke (баг LangGraph 1.0.8)
        async for chunk in compiled.astream(initial_state, config, stream_mode="values"):
            final = chunk
        assert final["current_agent"] in ["bid", "hitl"]
```

**Критично:** передавай mock-функции при построении графа ДО `compile()`. После компиляции ноды -- объекты `PregelNode`, их нельзя заменить.

### E2E-тесты

Playwright-тесты против запущенного стека (API + Dashboard + DB).

```python
# tests/e2e/test_hitl.py
class TestHITLDashboard:
    @pytest.mark.asyncio
    async def test_approve_bid(self, browser):
        page = await browser.new_page()
        await page.goto("http://localhost:3000/dashboard/hitl")
        await page.click(".hitl-card >> button:has-text('Approve')")
        response = await page.wait_for_response("**/api/hitl/*/resolve")
        assert response.status == 200
```

### Property-based тесты

Hypothesis для проверки инвариантов LLM-выходов. Используй при изменении промптов.

```python
# tests/property/test_llm_outputs.py
@given(budget=st.integers(min_value=100, max_value=10000))
@settings(max_examples=10)
@pytest.mark.asyncio
async def test_bid_never_exceeds_budget(self, budget):
    proposal = await bid_agent.generate_proposal({"budget": budget})
    assert proposal["bid_amount"] <= budget
```

### Golden Set -- регрессия промптов

Канонические примеры для каждого агента. Запускай после изменения prompt template.

```python
# tests/golden_set/test_golden_regression.py
@pytest.mark.parametrize("case", GOLDEN_SET_BID_AGENT, ids=lambda c: c["id"])
@pytest.mark.asyncio
async def test_bid_agent_golden(self, case, bid_agent):
    result = await bid_agent.generate_proposal(case["input"])
    props = case["expected_properties"]
    assert props["bid_amount_range"][0] <= result["bid_amount"] <= props["bid_amount_range"][1]
```

Наборы: `GOLDEN_SET_BID_AGENT`, `GOLDEN_SET_CONTENT_AGENT`, `GOLDEN_SET_SCOUT_AGENT`.

### Load-тесты

Locust-сценарии для API, WebSocket, HITL endpoints.

```bash
cd tests/load && locust -f locustfile.py --headless -u 50 -r 10 -t 5m
```

Файлы: `locustfile.py`, `conftest_load.py`, `validate_results.py`.

## Фикстуры и моки

Все ключевые фикстуры определены в `tests/conftest.py`. Используй их вместо создания собственных.

### Инфраструктурные фикстуры

| Фикстура | Тип | Описание |
|----------|-----|----------|
| `mock_valkey` | `AsyncMock` | Redis/Valkey-клиент. Предконфигурированы: `set`, `get`, `delete`, `publish`, `incr`, `expire`, `hset`, `hget`, `scan_iter`, `ft()` (RediSearch) |
| `mock_db_pool` | `AsyncMock` | asyncpg Pool. `acquire()` возвращает async context manager с `execute`, `fetch`, `fetchrow`. Доступ к conn: `pool._test_conn` |
| `mock_llm_client` | `AsyncMock(spec=LLMClient)` | LLM-клиент. `call()` возвращает `(AIMessage, CallMetrics)`. Дефолтный ответ: `{"result": "ok"}`, модель `gemini-3-flash` |
| `mock_heartbeat` | `HeartbeatMonitor` | Реальный HeartbeatMonitor с mock Valkey/DB. Config: interval=90s, timeout=180s, max_restarts=3 |
| `mock_loop_detector` | `LoopDetector` | Реальный LoopDetector. Лимиты: max_iterations=50, max_identical_steps=5 |

### State-фикстуры

| Фикстура | Описание |
|----------|----------|
| `sample_project` | `ProjectContext` -- freelancer, budget=500, client rating=4.8 |
| `sample_state` | `AgentState` от `create_initial_state()`, first_agent="scout" |
| `planner_state` | State после bid approval, artifacts от scout+bid |
| `dev_state` | State после planning, artifacts с plan JSON (tasks, phases) |
| `critic_state` | State после dev+content+design, все artifacts заполнены |
| `packager_state` | State после critic approval, verdict="APPROVE", score=0.92 |

### Autouse-фикстуры (применяются ко ВСЕМ тестам)

| Фикстура | Назначение |
|----------|------------|
| `_restore_langgraph_compile` | Восстанавливает оригинальный `StateGraph.compile` после каждого теста. Sentry monkey-patches его, что ломает `StateGraph(dict)` графы |
| `_mock_semgrep_gate` | Мокает `SemgrepGate` в critic/dev агентах + `SandboxManager` в dev. Без этого тесты требуют установленный semgrep binary и Docker |

### Правила мокирования

- **AsyncMock для async-интерфейсов.** `ChannelsPlugin`, любые async-методы -- только `AsyncMock()`. `MagicMock` на async вызовет `TypeError` на `await`.
- **WebSocket publish guard.** Оборачивай `publish_event()` в `try/except (OSError, ConnectionError)` в тестируемом коде.
- **Prometheus singleton.** Между тестами: `mod._metrics = None` + unregister из `REGISTRY._names_to_collectors`. Иначе `ValueError: Duplicated timeseries`.
- **APScheduler v3.** После `stop()` нужен `await asyncio.sleep(0)` -- shutdown через `call_soon_threadsafe`.
- **LLM-ответы.** Strip markdown code fences, `json.loads()` с try/except, clamp scores, default missing fields.

## CI интеграция

### Pre-commit

```bash
pytest tests/unit/ -x          # Быстрый прогон, стоп на первом fail
ruff check src/                # Линтер
```

### CI pipeline (GitHub Actions)

```yaml
- name: Run tests
  run: |
    pytest tests/ -v --cov=src --cov-report=xml --cov-fail-under=70 --junitxml=results.xml

- name: Upload test results
  uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: results.xml
```

### Pre-PR checklist

- [ ] `pytest tests/unit/` -- pass
- [ ] `pytest tests/integration/` -- pass (требует postgres + valkey)
- [ ] `ruff check src/` -- 0 errors
- [ ] Coverage >= 70% для модифицированных файлов

## Текущее покрытие

**Общее: 2330+ тестов, 0 failed, 15 skipped** (по состоянию на 2026-02-26).

### Распределение по категориям

| Категория | Файлов | Тестов |
|-----------|-------:|-------:|
| Unit | 99 | ~2100+ |
| Integration | 22 | 76+ |
| E2E | 3 | 39 |
| Load | 4 | n/a |
| Property | отд. | -- |
| Golden Set | отд. | -- |

### Распределение unit-тестов по подсистемам

| Подсистема | Файлов | Тестов | Ключевые файлы |
|------------|-------:|-------:|----------------|
| API Layer | 13 | 321 | routes (hitl 55, settings 47, orchestrator 35, agents 28, jobs 21, auth 20), websocket 20, guards 25, health 10 |
| Agents | 14 | 242 | base_extended 41, pipeline_b 40, scout_extended 24, bid_extended 21, critic 15, planner 13 |
| Core | 12 | 327 | models 89, encryption 55, config 31, json_repair 31, exceptions 29, state 26, database 24 |
| Graph & HITL | 6 | 155 | routing 47, builders 34, hitl_worker 29, hitl_resume 25, bulk_resolve 8 |
| Platform Adapters | 4 | 104 | fl_ru 40, freelancer 35, kwork 15, upwork 14 |
| Telegram Bot | 7 | 196 | orchestrator 51, notifications 38, commands 35, keyboards 34, handler 17 |
| Security & Sandbox | 5 | 145 | sandbox 57, sentry 31, security_headers 23, env_validation 22, semgrep 12 |
| Enrichment & Geo | 3 | 79 | enrichment 28, geo 28, email 23 |
| Browser | 3 | 33 | stealth 14, session 10, pool 9 |
| Knowledge Base | 4 | 88 | cli 28, embedding 26, ingestion 26, retrieval 8 |
| Workers & CLI | 9 | 194 | user_reg 61, cli_seed 26, workers 72, credentials 20 |
| Other | 4 | 70 | bugfix_501 26, llm_client 19, parsers 17, safety 8 |

## Конвенции

### Именование

- Файлы: `test_{module_name}.py` или `test_{module_name}_extended.py` для расширенных наборов
- Классы: `Test{ClassName}` (группировка связанных тестов)
- Методы: `test_{what_is_tested}` -- описательное имя, snake_case
- Фикстуры: `mock_{service}` для моков, `sample_{entity}` для тестовых данных, `{agent}_state` для state

### Структура файлов

```
tests/
├── conftest.py              # Глобальные фикстуры (ЧИТАЙ ПЕРВЫМ)
├── unit/                    # 99 файлов, изолированные тесты
│   ├── test_scout_agent.py
│   ├── test_bid_agent.py
│   ├── test_api_routes_*.py
│   └── ...
├── integration/             # 22 файла, multi-component
│   ├── test_full_pipeline.py
│   ├── test_api_endpoints.py
│   └── ...
├── e2e/                     # 3 файла, Playwright
│   ├── test_auth.py
│   ├── test_pages.py
│   └── test_hitl.py
├── load/                    # 4 файла, Locust
│   ├── locustfile.py
│   └── validate_results.py
├── golden_set/              # LLM regression benchmarks
│   └── bid_agent_golden.py
└── property/                # Hypothesis property-based
```

### Запуск

```bash
# Все unit-тесты
pytest tests/unit/ -v

# С покрытием
pytest tests/unit/ -v --cov=src --cov-report=html:htmlcov --cov-fail-under=70

# Конкретный файл
pytest tests/unit/test_scout_agent.py -v

# Integration (требует postgres + valkey)
pytest tests/integration/ -v --timeout=300

# E2E (требует полный стек)
pytest tests/e2e/ -v --timeout=300

# Параллельный запуск
pytest tests/ -n auto

# Property-тесты с фиксированным seed
pytest tests/property/ --hypothesis-seed=42

# Load-тесты
cd tests/load && locust -f locustfile.py --headless -u 50 -r 10 -t 5m

# Полная проверка
bash scripts/run_all_checks.sh
```

### Критические правила (из debugging.md)

1. **`StateGraph(dict)`** -- используй `dict`, не `AgentState` TypedDict, для schema графа
2. **Routing functions** -- аннотация `state: dict[str, Any]`, НЕ `state: AgentState` (баг LangGraph 1.0.8, теряются artifacts)
3. **`astream` вместо `ainvoke`** -- `ainvoke()` может вернуть начальный state вместо финального
4. **Semgrep fail-closed** -- без мока `SemgrepGate` тесты critic/dev сломаются
5. **asyncio.Event** -- не создавай на уровне модуля, только внутри async-функции
6. **`playwright_stealth`** -- мокай `src.browser.stealth._stealth`, не импорт

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `tests/conftest.py` | Глобальные фикстуры: mock_valkey, mock_db_pool, mock_llm_client, state fixtures, autouse mocks |
| `docs/testing_strategy.md` | Стратегия, пирамида, примеры кода, golden set дизайн |
| `docs/test_inventory.md` | Полный инвентарь: файлы, количество тестов, описания |
| `scripts/run_all_checks.sh` | Скрипт полной проверки (ruff + pytest + coverage) |
| `.claude/rules/patterns.md` | Паттерны кода: test pattern (unit), test pattern (integration) |
| `.claude/rules/debugging.md` | Известные gotchas: LangGraph, Sentry, Prometheus, APScheduler |
| `tests/golden_set/bid_agent_golden.py` | Golden set datasets для LLM regression |
| `tests/load/locustfile.py` | Locust-сценарии нагрузочного тестирования |
