# RAG и Knowledge Base: Спецификация

## Назначение

Долгосрочная память системы — накопление опыта на каждом завершённом заказе и семантический поиск для повышения качества будущих решений. Два компонента:

1. **Semantic Cache** — кэширование LLM-ответов для снижения затрат (реализовано)
2. **Experience Store** — накопление знаний из завершённых проектов (не реализовано)

## Semantic Cache (реализовано)

### Отличие от Experience Store

Semantic Cache — это **кэш LLM-ответов** для экономии: если новый запрос семантически похож на предыдущий (cosine similarity >= 0.92), возвращается кэшированный ответ без вызова LLM. Experience Store — это **долгосрочная база знаний** из завершённых проектов, используемая как RAG-контекст при генерации новых ответов.

### Класс `SemanticCache` (`src/core/semantic_cache.py`)

```python
SemanticCache(
    valkey: AsyncRedis,
    db_pool: asyncpg.Pool,
    similarity_threshold: float = 0.92,
)
```

### Двухуровневая архитектура

**Hot layer — Valkey (RediSearch):**
- Индекс: HNSW, FLOAT32, DIM=3072, COSINE
- Sub-millisecond lookup
- Ключ: `sem_cache:{sha256_hash[:20]}`
- Поля: `query`, `response`, `query_type`, `embedding` (bytes), `timestamp`
- TTL через `EXPIRE` на уровне ключа

**Cold layer — PostgreSQL (pgvector):**
- Таблица: `semantic_cache`
- Поля: `id`, `query_hash` (unique), `query`, `response`, `query_type`, `embedding` (vector(3072)), `expires_at`, `hit_count`
- Индекс: DiskANN (`<=>` cosine operator)
- Upsert: `ON CONFLICT (query_hash) DO UPDATE` — обновляет response и сбрасывает hit_count

### Поток запроса (get)

1. Генерация embedding запроса через OpenAI `text-embedding-3-large`
2. KNN-поиск в Valkey (top-1) → проверка similarity >= 0.92 + TTL
3. При miss — поиск в PostgreSQL (`ORDER BY embedding <=> query_embedding LIMIT 1`)
4. При cold hit — промоция в Valkey (запись для будущих быстрых hit'ов)
5. Счётчик `cache:hits` / `cache:misses` в Valkey для мониторинга

### Поток записи (set)

1. Генерация embedding через OpenAI
2. Запись в Valkey: `HSET` + `EXPIRE` (TTL по типу запроса)
3. Запись в PostgreSQL: `INSERT ... ON CONFLICT DO UPDATE` с `expires_at = NOW() + TTL`

### TTL по типу запроса

| Тип | TTL | Описание |
|-----|-----|----------|
| `proposal` | 24ч | Тексты предложений |
| `code` | 1ч | Сгенерированный код (быстро устаревает) |
| `content` | 12ч | Копирайтинг и тексты |
| `translation` | 7 дней | Переводы (стабильны) |
| `default` | 6ч | Всё остальное |

### Типы, исключённые из кэширования

- `real_time_data` — данные в реальном времени
- `random_generation` — случайная генерация
- `personalized` — персонализированный контент

### Агенты и кэширование

Кэшируются (TTL задан в `LLMClient._AGENT_CACHE_TTL`):
- Scout (6ч), Planner (1ч), Dev (1ч), Content (12ч), Design (12ч), GeoScout (6ч), Outreach (6ч)

Никогда не кэшируются (`_NEVER_CACHE_AGENTS`):
- **Bid** — каждое предложение уникально
- **Critic** — ревью должно быть свежим
- **Packager** — delivery-пакет формируется под конкретный заказ

### Инвалидация

- `invalidate_by_type(query_type)` — удаление по типу из обоих слоёв
- `flush()` — полная очистка + сброс статистики
- Автоматическая: TTL expiry (Valkey key TTL + PostgreSQL `expires_at > NOW()`)

## Embeddings

### Модель

- **OpenAI text-embedding-3-large** — 3072 измерений
- Вызов через `langchain_openai.OpenAIEmbeddings` с `OPENAI_API_KEY` (прямой API, не OpenRouter)
- Метод: `aembed_query(text)` → `np.ndarray` (float32)

### Хранение в PostgreSQL

- Тип: `vector(3072)` (расширение pgvector)
- Индекс: DiskANN (pgvectorscale) — 11x быстрее HNSW на масштабе, 99% recall
- Оператор: `<=>` (cosine distance)

### Хранение в Valkey

- Формат: `np.float32.tobytes()` (raw bytes)
- Индекс: RediSearch HNSW с COSINE distance metric

### Стоимость

~$5/мес при полной нагрузке 24/7 (OpenAI embedding pricing).

## Experience Store (не реализовано)

### Что будет запоминать

| Категория | Кто использует | Как |
|-----------|---------------|-----|
| Успешные биды (текст + контекст) | Bid Agent | Similarity search для похожих проектов → RAG-контекст |
| Неуспешные биды + причина отказа | Bid Agent | Negative examples для улучшения предложений |
| Паттерны переговоров | (не реализовано) | Шаблоны успешных коммуникаций с клиентами |
| Технические решения / code snippets | Dev Agent | Повторное использование проверенных решений |
| Точность оценки сроков (план vs факт) | Planner | Калибровка оценок на основе истории |
| Capability reports | Scout | Обновление матрицы компетенций |
| Client feedback | Все агенты | Адаптация стиля под предпочтения клиента |

### Планируемый Ingestion Pipeline

1. **Trigger:** после HITL Final approve в Packager
2. Извлечение ключевых данных: bid text, project context, code snippets, client feedback
3. `save_experience()` → embedding через `text-embedding-3-large` → `INSERT` в таблицу experience
4. Vectorизация: bid text (полный текст), project context (описание + результат), code snippets (ключевые фрагменты)

### Планируемый Retrieval

- `retrieve_similar(query, top_k=5)` → контекст для LLM через RAG
- Similarity metric: cosine (`<=>` operator)
- Top-K: 3-5 результатов
- Фильтрация по типу: `bid`, `code`, `negotiation`, `estimation`
- MMR (Maximal Marginal Relevance) для разнообразия результатов (не реализовано)

## Knowledge Base (не реализовано)

Файл `src/core/knowledge_base.py` не существует.

Планируемая функциональность:
- Структурированные знания о платформах (лимиты, правила, best practices)
- Профили клиентов (предпочтения, стиль общения, история)
- Capability matrix (что система умеет и не умеет)
- Обновление через HITL feedback loop

### Архитектура Knowledge Base (из legacy-дизайна)

#### Файловая структура знаний

```
knowledge/
├── proposals/                  # Успешные бид-примеры
│   ├── web_development/        # React, Next.js, landing pages
│   ├── mobile/                 # Flutter, React Native
│   ├── backend/                # API, database design
│   └── design/                 # UI/UX, branding
├── portfolio/                  # Завершённые проекты
│   ├── case_studies/           # JSON: project_001.json, ...
│   └── screenshots/            # Визуальные assets
├── clients/                    # Паттерны коммуникации
│   ├── negotiation_templates.md
│   ├── common_questions.md
│   └── objection_handling.md
├── technical/                  # Техническая документация
│   ├── stack_guides/           # react_next.md, python_fastapi.md, flutter.md
│   └── best_practices/         # code_review.md, security.md
└── outreach/                   # Cold email шаблоны
    ├── industries/             # dental_clinics.md, real_estate.md, restaurants.md
    └── email_templates/        # initial_contact.md, follow_up.md
```

#### Vector Store: таблица `knowledge_embeddings`

```sql
CREATE TABLE knowledge_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,      -- 'proposal', 'portfolio', 'template', 'tech'
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,      -- SHA-256[:16] для change detection
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    embedding vector(3072),         -- OpenAI text-embedding-3-large
    metadata JSONB DEFAULT '{}',
    category TEXT,                  -- 'web_dev', 'mobile', 'backend', 'design'
    platform TEXT,                  -- 'freelancer', 'upwork', 'all'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_path, chunk_index)
);

-- Индексы
CREATE INDEX idx_embeddings_category ON knowledge_embeddings(category);
CREATE INDEX idx_embeddings_platform ON knowledge_embeddings(platform);
CREATE INDEX idx_embeddings_source ON knowledge_embeddings(source_type);
CREATE INDEX idx_embeddings_vector ON knowledge_embeddings
    USING diskann (embedding vector_cosine_ops);    -- DiskANN: 11x vs HNSW
```

#### Chunking Strategy

| Параметр | Значение | Описание |
|----------|----------|----------|
| `chunk_size` | 1000 | Символов на чанк |
| `chunk_overlap` | 200 | Перекрытие между чанками |
| `separators` | `["\n\n", "\n", ". ", " ", ""]` | Приоритет разделителей |
| Splitter | `RecursiveCharacterTextSplitter` (LangChain) | |

#### Retrieval API

- `find_similar_proposals(job_description, category?, platform?, k=5)` -- поиск похожих успешных бидов
- `find_portfolio_cases(requirements, k=3)` -- поиск релевантных портфолио-кейсов
- Фильтрация: по `source_type`, `category`, `platform` через `filter` dict
- Similarity metric: cosine (`<=>` operator), score_threshold >= 0.7
- Backend: `PGVector` (LangChain integration) + `asimilarity_search()`

#### Indexing Pipeline

1. **Startup**: полная индексация `knowledge/` при запуске приложения
2. **Nightly reindex**: cron `0 3 * * *` (3:00 UTC) -- переиндексация изменённых файлов
3. **Change detection**: `SHA-256[:16]` хэш файла; пропускает неизменённые
4. **Manual add**: `add_successful_proposal()` -- сохранение + немедленная индексация после завершённого заказа
5. **Удаление**: при изменении файла -- удаление старых чанков + вставка новых

#### Retrieval Quality Monitoring

- Таблица `retrieval_logs`: query, num_results, doc_ids, was_helpful (boolean)
- Метрики Dashboard: total documents, by category, retrieval accuracy (`AVG(was_helpful)`), index freshness
- Feedback loop: HITL оценка полезности retrieval результатов

#### Конфигурация Knowledge Base

```python
KNOWLEDGE_BASE_CONFIG = {
    "embedding": {"model": "text-embedding-3-large", "dimensions": 3072},
    "chunking": {"chunk_size": 1000, "chunk_overlap": 200},
    "retrieval": {"default_k": 5, "score_threshold": 0.7},
    "indexing": {"auto_reindex": True, "reindex_schedule": "0 3 * * *"},
}
```

## Детали Semantic Cache (из legacy-дизайна)

### Архитектурная диаграмма потока

```
User Query
    │
    ▼
┌──────────────────┐
│ Embed Query      │  OpenAI text-embedding-3-large
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌─────────────────┐
│ Vector Search    │────▶│ Similar found?  │
│ (Valkey → PG)    │     └────────┬────────┘
└──────────────────┘              │
                    ┌─────────────┼─────────────┐
                    ▼ YES (≥0.92) ▼ NO          │
         ┌────────────────┐  ┌────────────────┐ │
         │ Return Cached  │  │ Call LLM API   │ │
         │ Response       │  └───────┬────────┘ │
         └────────────────┘          │          │
                                     ▼          │
                          ┌────────────────┐    │
                          │ Cache Response │    │
                          │ + Embedding    │    │
                          └────────────────┘    │
```

### Ожидаемый экономический эффект

| Метрика | Без кэша | С кэшем | Улучшение |
|---------|----------|---------|-----------|
| API costs | $500/мес | $300/мес | -40% |
| Avg latency | 2-3 сек | 50 мс (hits) | -95% |
| Rate limit headroom | 60% used | 35% used | +25% |

### Стратегии инвалидации (расширенные)

| Стратегия | Когда применять | Реализация |
|-----------|----------------|------------|
| TTL-based | По умолчанию | Автоматический expiry через Valkey EXPIRE + PG expires_at |
| Manual | Обновление знаний | API endpoint `DELETE /api/cache/invalidate` |
| Pattern-based | Изменения категории | Фильтр по `query_type` |
| Full flush | Мажорные обновления | `flush()` → Valkey FLUSHDB + PG DELETE |
| Автоматические триггеры | Обновление KB | При `knowledge_updated` → инвалидация связанного типа кэша |

Примеры автоматических триггеров:
- Обновление `proposals/` → инвалидация `proposal_generation`
- Обновление `technical/` → инвалидация `code_generation`

### Scheduled Cleanup Job

Автоматическая очистка устаревших записей (заполняет пробел "Нет scheduled cleanup job"):

```python
# src/tasks/cache_cleanup.py

async def cleanup_expired_cache() -> dict:
    """Cron job: очистка устаревших записей semantic cache.

    Schedule: каждые 6 часов (0 */6 * * *)
    """
    # 1. PostgreSQL: удалить expired
    result = await db.execute(
        delete(SemanticCache)
        .where(SemanticCache.expires_at < utcnow())
        .returning(func.count())
    )
    pg_deleted = result.scalar() or 0

    # 2. Valkey: expired ключи удаляются автоматически (TTL),
    #    но проверяем orphaned ключи (есть в Valkey, нет в PG)
    valkey_keys = await valkey.keys("sem_cache:*")
    orphaned = 0
    for key in valkey_keys:
        query_hash = key.split(":")[-1]
        exists = await db.execute(
            select(SemanticCache.id)
            .where(SemanticCache.query_hash == query_hash)
            .limit(1)
        )
        if not exists.scalar():
            await valkey.delete(key)
            orphaned += 1

    # 3. Обновить статистику
    total_remaining = await db.scalar(
        select(func.count()).select_from(SemanticCache)
        .where(SemanticCache.expires_at > utcnow())
    )

    logger.info("cache_cleanup_completed",
        pg_deleted=pg_deleted,
        valkey_orphaned=orphaned,
        remaining=total_remaining,
    )

    return {"pg_deleted": pg_deleted, "valkey_orphaned": orphaned, "remaining": total_remaining}
```

**Capacity Management:**

```python
MAX_CACHE_ENTRIES = 10_000

async def enforce_cache_capacity() -> int:
    """Удаляет LRU записи при превышении лимита."""
    count = await db.scalar(select(func.count()).select_from(SemanticCache))

    if count <= MAX_CACHE_ENTRIES:
        return 0

    overflow = count - MAX_CACHE_ENTRIES
    # Удаляем записи с наименьшим hit_count и самые старые
    to_delete = await db.execute(
        select(SemanticCache.id)
        .order_by(SemanticCache.hit_count.asc(), SemanticCache.created_at.asc())
        .limit(overflow)
    )

    ids = [row.id for row in to_delete.scalars()]
    await db.execute(delete(SemanticCache).where(SemanticCache.id.in_(ids)))

    return len(ids)
```

**APScheduler Registration:**

```python
# src/api/main.py (lifespan)
scheduler.add_job(cleanup_expired_cache, "cron", hour="*/6", id="cache_cleanup")
scheduler.add_job(enforce_cache_capacity, "cron", hour="3", minute="30", id="cache_capacity")
```

### False Positives Prevention

Для критичных типов запросов (`code_generation`, `proposal_generation`) применять двойную проверку:
- Стандартный порог: similarity >= 0.92
- Дополнительный строгий порог: exact similarity >= 0.98
- При несовпадении строгого порога -- cache miss (вызов LLM)

### LangChain Integration

Кэш реализуется как `SemanticLLMCache(BaseCache)` с методами:
- `alookup(prompt, llm_string)` → `Optional[list[Generation]]`
- `aupdate(prompt, llm_string, return_val)` → кэширование ответа

Совместимость: подключается к любой LangChain LLM через параметр `cache=`.

### Мониторинг кэша

SQL-запросы для Dashboard:

```sql
-- Эффективность по типам
SELECT query_type, COUNT(*) as total, SUM(hit_count) as total_hits,
       AVG(hit_count) as avg_hits
FROM semantic_cache WHERE expires_at > NOW()
GROUP BY query_type;

-- Скоро истекающие записи
SELECT COUNT(*) FROM semantic_cache
WHERE expires_at BETWEEN NOW() AND NOW() + INTERVAL '1 hour';
```

Valkey counters: `cache:hits`, `cache:misses` → hit rate = hits / (hits + misses).

### Embedding Strategy (полная)

| Провайдер | Модель | Размерность | Стоимость | Назначение |
|-----------|--------|-------------|-----------|------------|
| OpenAI | text-embedding-3-large | 3072 | $0.13/1M tokens | **Primary** (production) |
| OpenAI | text-embedding-3-small | 1536 | $0.02/1M tokens | Альтернатива при ограниченном бюджете |
| Cohere | embed-multilingual-v3 | 1024 | $0.10/1M tokens | Мультиязычный fallback |
| Local | nomic-embed-text (Ollama) | 768 | Бесплатно | Offline/development |

Выбор: `text-embedding-3-large` -- лучшие MTEB scores, разумная цена (~$5/мес при 24/7), 3072 dim для высокого quality retrieval. Fallback на `nomic-embed-text` для offline-разработки (без API dependency).

Embeddings вызываются через OpenAI API напрямую (`OPENAI_API_KEY`), НЕ через OpenRouter.

## Статус реализации

| Компонент | Статус | Файл |
|-----------|--------|------|
| SemanticCache (dual-layer) | **Реализован** | `src/core/semantic_cache.py` |
| Valkey hot layer (RediSearch) | **Реализован** | `src/core/semantic_cache.py` |
| PostgreSQL cold layer (pgvector) | **Реализован** | `src/core/semantic_cache.py` |
| Интеграция с LLMClient | **Реализован** | `src/core/llm_client.py` |
| TTL и инвалидация | **Реализован** | `src/core/semantic_cache.py` |
| Hit/miss статистика | **Реализован** | Valkey counters `cache:hits`, `cache:misses` |
| Experience Store | Не реализован | — |
| Knowledge Base | Не реализован | — |
| RAG retrieval для агентов | Не реализован | — |
| Очистка устаревших векторов | Частично (TTL expiry) | Нет scheduled cleanup job |

## Ключевые файлы

- `src/core/semantic_cache.py` — SemanticCache класс, dual-layer архитектура
- `src/core/llm_client.py` — интеграция кэша с LLM-вызовами, `_AGENT_CACHE_TTL`, `_NEVER_CACHE_AGENTS`
- `src/core/config.py` — настройки `SEMANTIC_CACHE_TTL_*`, `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`
- `TECH_STACK.md` — спецификация pgvector/pgvectorscale/DiskANN
