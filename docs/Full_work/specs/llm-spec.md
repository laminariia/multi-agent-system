# LLM Client и модели: Спецификация

## Архитектура LLM Client

### Класс `LLMClient` (`src/core/llm_client.py`)

Единый интерфейс для всех LLM-вызовов в системе. Каждый агент делегирует вызовы через `LLMClient.call(agent_name, messages, **kwargs)`.

**Конструктор:**

```python
LLMClient(
    cost_tracker: CostTracker | None = None,
    max_retries: int = 5,
    base_backoff_seconds: float = 1.0,
    request_timeout: float = 120.0,
    api_key: str | None = None,
    base_url: str | None = None,
    semantic_cache: SemanticCache | None = None,
)
```

**Основной метод — `call()`:**

```python
async def call(
    agent_name: str,
    messages: list[BaseMessage],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    force_model: str | None = None,
) -> tuple[BaseMessage, CallMetrics]
```

Логика вызова:
1. Проверка semantic cache (если агент кэшируемый) — при hit возвращает `AIMessage` без LLM-вызова
2. Определение цепочки моделей из `AGENT_MODEL_REGISTRY` (primary -> fallback1 -> fallback2)
3. Вызов `_invoke_with_retries()` с exponential backoff (1s -> 2s -> 4s -> 8s, до 5 попыток)
4. При успехе — сохранение в semantic cache
5. При rate-limit / timeout — переход к следующей модели в цепочке
6. При исчерпании всех моделей — `LLMException`

**Кэширование моделей:**

`_get_or_create_model()` создаёт `ChatOpenAI` инстансы с ленивой инициализацией. Ключ кэша: `{model_key}:{temperature}:{max_tokens}`. Все модели роутятся через OpenRouter с OpenAI-совместимым API.

### OpenRouter интеграция

- Все LLM-вызовы идут через единый `OPENROUTER_API_KEY`
- Base URL: `https://openrouter.ai/api/v1` (переопределяется через `OPENROUTER_BASE_URL`)
- Заголовки: `HTTP-Referer: https://multi-agent-service.local`, `X-Title: Multi-Agent Service`
- Клиент: `langchain_openai.ChatOpenAI` с кастомным `base_url`

### DB-first credential loading

`load_platform_credentials(platform)` из `src/core/credential_loader.py`:
- Приоритет: БД (таблица `PlatformAccount`) > переменные окружения
- Расшифровка через `decrypt_credentials()` (Fernet)
- `update_credentials(api_key, base_url)` — обновляет ключ в runtime, очищает `_chat_model_cache`

## Модели и назначение

### Реестр в коде (`AGENT_MODEL_REGISTRY`)

> Синхронизировано с `MASTER-VISION.md` Section 4 (canonical source). Дата: 2026-03-10.
> Speculative fallback models removed — only verified OpenRouter models.

**6-Tier LLM System:**

| Tier | Роль | Model | OpenRouter ID | Agents |
|:----:|------|-------|---------------|--------|
| 1 | Reasoning | Claude Opus 4.6 | `anthropic/claude-opus-4-6` | Planner, Dev (complex), SalesAgent [PLAN] |
| 2 | Client-facing | Gemini 3.1 Pro | `google/gemini-3.1-pro` | Bid, Outreach |
| 3 | Content+Review | Claude Sonnet 4.6 | `anthropic/claude-sonnet-4-6` | Content, Dev (standard), Critic |
| 4 | Design | NanoBanana Pro | `google/gemini-3-pro-image-preview` | Design |
| 5 | Extraction | Gemini 2.5 Flash | `google/gemini-2.5-flash` | Scout, GeoScout |
| 6 | Simple | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Packager, Portfolio Agent [PLAN] |

Dev Agent routing: Planner помечает задачу `complexity: "complex"` → Opus (T1), `"standard"` → Sonnet (T3).

### Supplementary Models

| Задача | Модель | Провайдер | Примечание |
|--------|--------|-----------|------------|
| Embeddings | OpenAI text-embedding-3-large (3072 dim) | OpenAI API direct | Vector search, semantic cache |
| Fast image drafts | Gemini 3 Flash | OpenRouter | Quick concept exploration |

### Стоимость моделей (OpenRouter pricing — used models only)

| Модель | OpenRouter ID | Tier | Контекст |
|--------|---------------|:----:|----------|
| Claude Opus 4.6 | `anthropic/claude-opus-4-6` | 1 | 1M |
| Gemini 3.1 Pro | `google/gemini-3.1-pro` | 2 | 1M |
| Claude Sonnet 4.6 | `anthropic/claude-sonnet-4-6` | 3 | 1M |
| NanoBanana Pro | `google/gemini-3-pro-image-preview` | 4 | 65K |
| Gemini 2.5 Flash | `google/gemini-2.5-flash` | 5 | 1M |
| DeepSeek V3.2 | `deepseek/deepseek-v3.2` | 6 | 164K |

> **Note:** Pricing fluctuates — verify on openrouter.ai/models before budgeting.
> Removed: DeepSeek R1, Claude Haiku 3.5, GPT-5.2/Codex, Grok 4.1 Fast, Qwen3-Coder-Next (speculative/unused).

### NanoBanana Pro

**NanoBanana Pro** = Gemini 3 Pro Image Generation (`google/gemini-3-pro-image-preview`).
Используется Design Agent для генерации изображений: логотипы с текстом, инфографика, маркетинговые материалы.
- 2K/4K output resolution, flexible aspect ratios
- Industry-leading text rendering in images
- Контекст: 65K tokens

### Месячная стоимость (оценка при 24/7 нагрузке): ~$391/мес

| Агент | Модель | $/мес |
|-------|--------|-------|
| Агент | Модель (Tier) | $/мес |
|-------|---------------|-------|
| Scout | Gemini 2.5 Flash (T5) | TBD |
| Bid | Gemini 3.1 Pro (T2) | TBD |
| Planner | Claude Opus 4.6 (T1) | ~$41 |
| Dev (complex 30%) | Claude Opus 4.6 (T1) | ~$41 |
| Dev (standard 70%) | Claude Sonnet 4.6 (T3) | TBD |
| Content | Claude Sonnet 4.6 (T3) | TBD |
| Design | NanoBanana Pro (T4) | ~$30 |
| Critic | Claude Sonnet 4.6 (T3) | TBD |
| Packager | DeepSeek V3.2 (T6) | ~$1 |
| GeoScout | Gemini 2.5 Flash (T5) | TBD |
| Outreach | Gemini 3.1 Pro (T2) | TBD |
| Embeddings | text-embedding-3-large | ~$5 |
| **Total** | | **Recalculate after migration** |

> **Note:** Previous estimate (~$391/mo) was based on DeepSeek V3.2 for most agents.
> New tier system uses different models — costs to be recalculated after implementation.

## Промпты

Все промпты хранятся в `src/prompts/` и экспортируются через `__init__.py`.

| Файл | Переменная | Назначение |
|------|-----------|------------|
| `scout.py` | `SCOUT_SYSTEM_PROMPT` | Анализ вакансий на 4 платформах; скоринг 0.0-1.0; JSON с match_score, recommendation (bid/skip/review); критерии: бюджет $20-$5000, длительность <4 недель |
| `bid.py` | `BID_SYSTEM_PROMPT` | 5-частная структура предложения (Hook, Credibility, Solution, Timeline, CTA); `requires_hitl=true` всегда; pricing strategy по размеру проекта; лимит 200 слов для <$500 |
| `planner.py` | `PLANNER_SYSTEM_PROMPT` | Декомпозиция проекта на задачи MAX 4ч каждая; назначение на dev/content/design/critic/hitl; JSON с phases, dependencies, critical_path, risks; MAX 3 re-plans |
| `dev.py` | `DEV_SYSTEM_PROMPT` | Full-stack разработка; JSON с files/dependencies/build_commands; security-правила (запрет eval/exec/os.system); MAX 5 итераций; весь код проходит через Critic |
| `content.py` | `CONTENT_SYSTEM_PROMPT` | Копирайтинг и документация; JSON с deliverables + 2 альтернативы для A/B; MAX 500 слов на секцию; запрет клише и placeholder-текста |
| `design.py` | `DESIGN_SYSTEM_PROMPT` | UI/UX спецификации в JSON (не изображения в MVP); 8px grid, WCAG 2.1 AA; обязательно dark/light mode + responsive; default стайлгайд с нейтральной палитрой |
| `critic.py` | `CRITIC_SYSTEM_PROMPT` | Quality gate: score >= 0.85 approve, 0.60-0.84 revise, <0.60 reject; Semgrep обязателен перед approve кода; revision classification: minor/major/scope_creep; MAX 3 цикла ревизии |
| `packager.py` | `PACKAGER_SYSTEM_PROMPT` | Сборка delivery-пакета; README, структура папок, delivery message; `requires_hitl=true` всегда; проверка комплектности артефактов |
| `outreach.py` | `EMAIL_OUTREACH_PROMPT`, `TELEGRAM_OUTREACH_PROMPT` | Multi-channel outreach: email (<120 слов) и Telegram DM (<60 слов); тон "сосед, не продавец"; `get_outreach_prompt(channel)` API; запрет формальных приветствий и buzzwords |

Отдельного файла `geo_scout.py` нет — GeoScout использует промпт, встроенный в агент (`src/agents/geo_scout.py`).

## Semantic Cache

### Архитектура

Двухуровневый семантический кэш (`src/core/semantic_cache.py`):

- **Hot layer (Valkey):** RediSearch vector index (HNSW, FLOAT32, DIM=3072, COSINE). Sub-millisecond lookup.
- **Cold layer (PostgreSQL):** pgvector с HNSW (pgvector) индексом (`<=>` оператор). Persistent fallback.

### Параметры

- **Embedding:** OpenAI `text-embedding-3-large` (3072 dim), через `OPENAI_API_KEY` (прямой API, не OpenRouter)
- **Порог similarity:** 0.92 (cosine) — настраивается через `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`
- **Cache key:** SHA-256 хэш текста запроса (первые 20 символов для Valkey, 32 для PostgreSQL)

### TTL по типу запроса

| Тип | TTL | Описание |
|-----|-----|----------|
| `proposal` | 24ч (86400с) | Предложения на проекты |
| `code` | 1ч (3600с) | Сгенерированный код |
| `content` | 12ч (43200с) | Контент и тексты |
| `translation` | 7 дней (604800с) | Переводы |
| `default` | 6ч (21600с) | Всё остальное |

### Кэширование по агентам

| Агент | Cache TTL | query_type |
|-------|-----------|------------|
| Scout | 6ч | `agent_scout` |
| Planner | 1ч | `agent_planner` |
| Dev | 1ч | `agent_dev` |
| Content | 12ч | `agent_content` |
| Design | 12ч | `agent_design` |
| GeoScout | 6ч | `agent_geoscout` |
| Outreach | 6ч | `agent_outreach` |

**Никогда не кэшируются:** Bid, Critic, Packager — их output должен быть уникальным.

### Инвалидация

- `invalidate_by_type(query_type)` — удаляет все записи по типу из обоих слоёв
- `flush()` — полная очистка обоих слоёв + сброс hit/miss счётчиков
- TTL-based expiry: Valkey (`EXPIRE`), PostgreSQL (`expires_at > NOW()`)
- Промоция: cold hit в PostgreSQL автоматически записывается в Valkey для будущих быстрых hit'ов

## Cost Tracking

### Класс `CallMetrics`

Каждый LLM-вызов записывает:
- `agent_name`, `model_id`, `provider`
- `tokens_input`, `tokens_output`
- `cost_usd` (вычисляется из `ModelSpec.cost_input_per_1k` / `cost_output_per_1k`)
- `latency_ms`, `was_fallback`, `attempt`, `cache_hit`

### Класс `CostTracker`

Аккумулирует все `CallMetrics` за время жизни процесса:
- `total_cost_usd` — суммарная стоимость
- `by_agent()` -> `{agent_name: total_cost}`
- `by_model()` -> `{model_id: total_cost}`
- Интеграция с Prometheus через `get_metrics().record_llm_call()`

## Fallback стратегия

1. **Primary model** вызывается с exponential backoff: 1s -> 2s -> 4s -> 8s -> 16s (до `max_retries=5`)
2. При исчерпании retries (rate-limit или timeout) — пауза 1с, переход к **Fallback 1**
3. Аналогично для Fallback 2
4. Если все 3 модели исчерпаны — `LLMException("All LLM providers exhausted")`

### Обработка ошибок

| Ошибка | Действие |
|--------|---------|
| `RateLimitError` (429) | Retry с backoff, затем fallback |
| `APITimeoutError` | Retry с backoff, затем fallback |
| Context overflow | `LLMContextOverflowError` — немедленный выброс |
| Invalid response / JSON parse | `LLMInvalidResponseError` — немедленный выброс |
| Прочие | `LLMException` — немедленный выброс |

Provider SDK exceptions загружаются лениво через `_load_provider_errors()` — OpenAI, Anthropic, Google.

## Ключевые файлы

- `src/core/llm_client.py` — LLMClient, ModelSpec, MODELS, AGENT_MODEL_REGISTRY, CostTracker
- `src/core/semantic_cache.py` — SemanticCache (dual-layer Valkey + PostgreSQL)
- `src/core/credential_loader.py` — load_platform_credentials, get_api_key
- `src/prompts/*.py` — системные промпты для всех агентов
- `src/core/config.py` — Settings с OPENROUTER_API_KEY, OPENAI_API_KEY
- `src/monitoring/metrics.py` — Prometheus-метрики LLM-вызовов
- `TECH_STACK.md` — каноническая таблица agent -> model mapping
