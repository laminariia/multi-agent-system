# Database: Спецификация

## Стек

| Компонент | Технология |
|-----------|------------|
| СУБД | PostgreSQL 16 |
| Расширения | pgvector, pgvector |
| Docker image | `timescale/timescaledb-ha:pg16` |
| ORM | SQLAlchemy 2.0 (async, `Mapped` / `mapped_column`) |
| Миграции | Alembic (async, asyncpg) |
| Driver | asyncpg |
| Env var | `DATABASE_URL` (НЕ `POSTGRES_URL`) |

## Соглашения

- UUID первичные ключи (`gen_random_uuid()` server-side default)
- `Vector(3072)` для embedding-колонок (OpenAI text-embedding-3-large)
- HNSW (pgvector) индексы для vector-колонок (НЕ HNSW) -- создаются через raw DDL
- `datetime.now(timezone.utc)` для Python-side timestamp defaults
- JSONB для произвольных структур (`type_annotation_map = {dict[str, Any]: JSONB}`)
- `_escape_like()` хелпер из `src/api/routes/__init__.py` для защиты от SQL wildcard injection
- pgvector SQL с `$1`-style asyncpg params + `# noqa: S608` для обхода ruff false positive

## Таблицы (19 штук)

### 1. users
Пользователи системы (владелец, модераторы, зрители).

| Поле | Тип | Constraints |
|------|-----|-------------|
| id | UUID | PK, `gen_random_uuid()` |
| email | String(255) | UNIQUE, NOT NULL |
| password_hash | String(255) | nullable |
| name | String(100) | nullable |
| role | String(20) | default `owner` |
| status | String(20) | default `active` |
| telegram_chat_id | BigInteger | nullable |
| settings | JSONB | default `{}` |
| created_at | DateTime(tz) | |
| last_login_at | DateTime(tz) | nullable |

**Индексы:** `idx_users_email(email)`, `idx_users_status(status)`
**Relationships:** -> platform_accounts, hitl_resolutions

### 2. platform_accounts
Аккаунты на freelance-платформах (credentials зашифрованы Fernet).

| Поле | Тип | Constraints |
|------|-----|-------------|
| id | UUID | PK |
| user_id | UUID | FK -> users.id, CASCADE |
| platform | String(50) | NOT NULL |
| username | String(255) | nullable |
| credentials | JSONB | NOT NULL (зашифровано) |
| status | String(20) | default `active` |
| profile_url | Text | nullable |
| stats | JSONB | default `{}` |
| last_health_check | DateTime(tz) | nullable |
| created_at, updated_at | DateTime(tz) | |

**Индекс:** `idx_accounts_platform(platform, status)`

### 3. jobs
Обнаруженные фриланс-заказы со всех платформ.

| Поле | Тип | Constraints |
|------|-----|-------------|
| id | UUID | PK |
| platform | String(50) | NOT NULL |
| external_id | String(255) | NOT NULL |
| title | String(500) | NOT NULL |
| description | Text | nullable |
| budget_min, budget_max | Numeric(10,2) | nullable |
| budget_type | String(20) | nullable |
| currency | String(3) | default `USD` |
| client_info | JSONB | nullable |
| skills_required | ARRAY(Text) | nullable |
| deadline, platform_deadline | DateTime(tz) | nullable |
| status | String(30) | default `new` |
| score | Numeric(3,2) | nullable (0.00-1.00) |
| disqualify_reason | String(255) | nullable |
| discovered_at | DateTime(tz) | |
| url | Text | nullable |
| raw_data | JSONB | nullable |

**Constraints:** `UNIQUE(platform, external_id)`
**Индексы:** `idx_jobs_status(status, discovered_at)`, `idx_jobs_platform(platform, status)`, `idx_jobs_score(score) WHERE status='qualified'`
**Relationships:** -> bids, projects, agent_logs, ab_test_results

### 4. bids
Предложения (proposals), сгенерированные Bid Agent.

| Поле | Тип | Constraints |
|------|-----|-------------|
| id | UUID | PK |
| job_id | UUID | FK -> jobs.id, CASCADE |
| account_id | UUID | FK -> platform_accounts.id, nullable |
| proposal_text | Text | NOT NULL |
| bid_amount | Numeric(10,2) | NOT NULL |
| estimated_days | Integer | nullable |
| portfolio_items | ARRAY(Text) | nullable |
| status | String(30) | default `draft` |
| hitl_request_id | UUID | nullable |
| platform_bid_id | String(255) | nullable |
| created_at, sent_at, response_at | DateTime(tz) | |
| version | Integer | default 1 |
| generation_model | String(50) | nullable |

**Constraints:** `UNIQUE(job_id, account_id)`
**Индексы:** `idx_bids_status(status)`, `idx_bids_job(job_id)`

### 5. projects
Проекты, созданные из выигранных заказов.

| Поле | Тип | Constraints |
|------|-----|-------------|
| id | UUID | PK |
| bid_id, job_id | UUID | FK, nullable |
| title | String(500) | NOT NULL |
| type | String(30) | default `freelance` |
| client_name | String(255) | nullable |
| client_contact | JSONB | nullable |
| agreed_amount, paid_amount | Numeric(10,2) | |
| currency | String(3) | default `USD` |
| start_date, deadline | Date | nullable |
| completed_at | DateTime(tz) | nullable |
| status | String(30) | default `planning` |
| progress | Integer | default 0 (0-100) |
| revision_count | Integer | default 0 |
| kanban_column | String(30) | nullable |
| kanban_order | Integer | nullable |

**Индексы:** `idx_projects_status`, `idx_projects_type(type, status)`, `idx_projects_deadline WHERE status NOT IN ('completed','cancelled')`

### 6. tasks
Декомпозиция проекта на задачи (Planner Agent, до 4ч каждая).

Ключевые поля: `project_id` (FK), `parent_task_id` (FK self-ref), `title`, `assigned_agent`, `status`, `priority`, `estimated_hours`, `depends_on` (ARRAY), `artifact_ids` (ARRAY).

### 7. artifacts
Файлы, сгенерированные агентами (код, контент, дизайн).

Ключевые поля: `project_id`, `task_id` (FK), `type`, `filename`, `mime_type`, `size_bytes`, `content` (LargeBinary), `storage_url`, `version`, `generated_by`, `llm_model`, `quality_score`, `security_scan` (JSONB).

### 8. hitl_queue
Очередь Human-in-the-Loop для ручного утверждения.

Ключевые поля: `type` (bid_approval, code_review, delivery и др.), `priority` (urgent/normal/low), `bid_id`, `project_id`, `task_id` (FK), `title`, `payload` (JSONB), `available_actions` (ARRAY), `status` (pending/resolved/expired), `resolution`, `resolved_by` (FK -> users), `expires_at`, `telegram_sent`, `email_sent`.

**Индексы:** `idx_hitl_status(status, priority, created_at)`, `idx_hitl_type(type, status)`

### 9. revisions
Клиентские правки к проекту.

Ключевые поля: `project_id` (FK), `type`, `client_feedback`, `parsed_items` (JSONB), `status`, `assigned_agent`, `hitl_required`, `tasks_created` (ARRAY).

### 10. leads
Бизнес-лиды из Pipeline B (GeoScout + Overpass API).

Ключевые поля: `name`, `category`, `address`, `city`, `country`, `latitude`, `longitude`, `h3_index`, `phone`, `email`, `website`, `telegram_username`, `social_links` (JSONB), `enrichment_source`, `enrichment_cost`, `enrichment_data` (JSONB), `status`, `osm_id` (UNIQUE).

**Индексы:** `idx_leads_city(city, category, status)`, `idx_leads_h3(h3_index)`, `idx_leads_status`

### 11. email_campaigns
Outreach-кампании для Pipeline B.

Ключевые поля: `name`, `subject_template`, `body_template`, `target_cities` (ARRAY), `target_categories` (ARRAY), `status`, `total_leads`, `sent_count`, `open_count`, `reply_count`, `bounce_count`.

### 11b. campaign_leads
Связующая таблица: campaign <-> lead (composite PK).

Ключевые поля: `campaign_id` + `lead_id` (PK), `status`, `channel_type` (email/telegram), `personalized_subject`, `personalized_body`, `sent_at`, `opened_at`, `replied_at`.

### 12. agent_logs
Логи выполнения агентов с LLM-трекингом.

Ключевые поля: `agent_name`, `project_id`, `task_id`, `job_id` (FK), `event_type`, `message`, `details` (JSONB), `llm_model`, `tokens_input`, `tokens_output`, `cost_usd`, `latency_ms`.

### 13. agent_heartbeats
Heartbeat-мониторинг агентов (PK = `agent_name`).

Ключевые поля: `status` (idle/active/paused/error/dead), `current_task`, `last_heartbeat`, `restart_count`, `error_message`, `metadata` (JSONB).

### 14. langgraph_checkpoints
Чекпоинты LangGraph (composite PK: `thread_id` + `checkpoint_id`).

Ключевые поля: `state_data` (JSONB), `metadata` (JSONB), `current_agent`, `status`, `requires_hitl`.

### 14b. langgraph_checkpoint_history
История чекпоинтов (auto-increment PK).

### 15. knowledge_base
RAG-хранилище знаний для Bid Agent.

Ключевые поля: `type`, `category`, `title`, `content`, `embedding` (Vector(3072)), `usage_count`, `success_rate`.
**HNSW (pgvector) индекс** создается через raw DDL в init-миграции.

### 16. semantic_cache
LLM-кэш с семантическим поиском.

Ключевые поля: `query_hash` (UNIQUE), `query`, `response`, `query_type`, `embedding` (Vector(3072)), `hit_count`, `expires_at`.
**HNSW (pgvector) индекс** создается через raw DDL.

### 17. ab_test_results
A/B тестирование шаблонов предложений.

Ключевые поля: `test_name`, `variant_id`, `job_id`, `bid_id` (FK), `impressions`, `responses`, `hires`, `template_name`, `opening_style`, `tone`.

### 18. orchestrator_goals
Цели автономного оркестратора.

Ключевые поля: `goal_id` (UNIQUE, e.g. `g_001`), `title`, `priority`, `category`, `status`, `result`, `context`, `success_criteria` (ARRAY), `depends_on` (ARRAY).

### 19. telegram_channels
Telegram-каналы для мониторинга (PK = auto-increment Integer).

Ключевые поля: `username` (UNIQUE), `title`, `category`, `active` (bool).

## pgvector

- **Dimension:** 3072 (OpenAI text-embedding-3-large)
- **Таблицы с embedding:** `knowledge_base`, `semantic_cache`
- **Тип индекса:** HNSW (pgvector) (pgvector) -- НЕ HNSW
- **Оператор:** cosine similarity
- HNSW (pgvector) индексы создаются через raw DDL, т.к. SQLAlchemy не поддерживает `USING hnsw` нативно

## Миграции Alembic

| Файл | Описание |
|------|----------|
| `20260207_2326_initial_schema.py` | Начальная схема (17 таблиц) |
| `20260208_1420_add_knowledge_vector_index.py` | HNSW (pgvector) индекс для knowledge_base |
| `20260208_1600_add_user_status_column.py` | Колонка status в users |
| `20260210_1200_add_checkpoint_history_table.py` | Таблица langgraph_checkpoint_history |
| `20260210_1830_fix_checkpoint_column_names.py` | Переименование колонок чекпоинтов |
| `20260211_2000_add_orchestrator_goals.py` | Таблица orchestrator_goals |
| `20260213_1800_migrate_embedding_768_to_3072.py` | Миграция embeddings 768 -> 3072 dim |
| `20260221_1930_add_telegram_channels.py` | Таблица telegram_channels |
| `20260224_1200_seed_telegram_channels.py` | Seed: 22 Telegram-канала |
| `20260226_1200_add_multichannel_outreach.py` | channel_type в campaign_leads |
| `20260301_1000_add_bid_platform_bid_id.py` | platform_bid_id в bids |

**Naming convention:** `YYYYMMDD_HHMM_описание.py`

**Railway deploy:** retry loop для Alembic:
```bash
for i in 1 2 3 4 5; do
  python -m alembic upgrade head && break || sleep 5
done
```

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `src/core/models.py` | 19 ORM-моделей (Base -> DeclarativeBase) |
| `alembic/versions/` | 11 миграций |
| `alembic/env.py` | Async Alembic config (asyncpg) |
| `src/core/database.py` | engine, get_db_session, get_valkey |
| `src/api/routes/__init__.py` | `_escape_like()` хелпер |
| `init.sql` | DDL для HNSW (pgvector) индексов |

---

## Расширенные определения таблиц

### tasks (полная структура)

```sql
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    parent_task_id  UUID REFERENCES tasks(id),       -- для подзадач
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    assigned_agent  VARCHAR(30),                     -- 'dev', 'content', 'design'
    status          VARCHAR(30) DEFAULT 'pending',   -- pending, in_progress, review, done, blocked
    priority        INTEGER DEFAULT 0,
    estimated_hours DECIMAL(5,2),
    actual_hours    DECIMAL(5,2),
    depends_on      UUID[],                          -- task IDs от которых зависит
    artifact_ids    UUID[],
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_tasks_project ON tasks(project_id, status);
CREATE INDEX idx_tasks_agent ON tasks(assigned_agent, status);
```

### artifacts (полная структура)

```sql
CREATE TABLE artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    task_id         UUID REFERENCES tasks(id),
    type            VARCHAR(30) NOT NULL,            -- 'code', 'document', 'image', 'archive'
    filename        VARCHAR(255) NOT NULL,
    mime_type       VARCHAR(100),
    size_bytes      BIGINT,
    storage_type    VARCHAR(20) DEFAULT 'db',        -- 'db', 's3'
    content         BYTEA,                           -- для small files
    storage_url     TEXT,                            -- S3 URL для large files
    version         INTEGER DEFAULT 1,
    previous_version_id UUID REFERENCES artifacts(id),
    generated_by    VARCHAR(30),                     -- какой агент
    llm_model       VARCHAR(50),
    quality_score   DECIMAL(3,2),                    -- от Critic
    security_scan   JSONB,                           -- Semgrep results
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_artifacts_project ON artifacts(project_id);
CREATE INDEX idx_artifacts_type ON artifacts(type);
```

### revisions (полная структура)

```sql
CREATE TABLE revisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    type            VARCHAR(20) NOT NULL,            -- 'minor', 'major', 'scope_creep', 'unclear'
    client_feedback TEXT NOT NULL,
    parsed_items    JSONB,                           -- структурированная декомпозиция
    status          VARCHAR(20) DEFAULT 'new',       -- 'new', 'processing', 'completed', 'escalated'
    assigned_agent  VARCHAR(30),
    hitl_required   BOOLEAN DEFAULT FALSE,
    resolution_note TEXT,
    tasks_created   UUID[],                          -- новые задачи
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_revisions_project ON revisions(project_id, status);
```

### langgraph_checkpoint_history

```sql
CREATE TABLE langgraph_checkpoint_history (
    id              SERIAL PRIMARY KEY,
    thread_id       VARCHAR(255) NOT NULL,
    checkpoint_id   VARCHAR(255) NOT NULL,
    state_data      JSONB NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Trigger: хранить последние 10 checkpoints на thread
CREATE OR REPLACE FUNCTION cleanup_old_checkpoints()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM langgraph_checkpoint_history
    WHERE thread_id = NEW.thread_id
    AND id NOT IN (
        SELECT id FROM langgraph_checkpoint_history
        WHERE thread_id = NEW.thread_id
        ORDER BY created_at DESC
        LIMIT 10
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_cleanup_checkpoints
AFTER INSERT ON langgraph_checkpoint_history
FOR EACH ROW EXECUTE FUNCTION cleanup_old_checkpoints();
```

---

## Data Retention Policy

| Таблица | Retention | Стратегия | Примечание |
|---------|-----------|-----------|------------|
| `agent_logs` | 6 месяцев | Partition by month, drop old partitions | High volume |
| `jobs` | 90 дней active, затем archive | Move to `jobs_archive` | Won jobs -- indefinitely |
| `bids` | 90 дней active, затем archive | Move to `bids_archive` | Won bids -- indefinitely |
| `leads` (unsubscribed) | 30 дней | Hard delete | GDPR compliance |
| `semantic_cache` | TTL-based | Auto-expire через `expires_at` | Managed by cache system |
| `langgraph_checkpoints` | 30 дней после completion | Delete completed threads | Active threads хранятся |
| `hitl_queue` (resolved) | 90 дней | Archive | Audit trail |

### Partition Strategy (agent_logs)

```sql
CREATE TABLE agent_logs (
    id              UUID DEFAULT gen_random_uuid(),
    agent_name      VARCHAR(30) NOT NULL,
    project_id      UUID,
    task_id         UUID,
    job_id          UUID,
    event_type      VARCHAR(50) NOT NULL,
    message         TEXT,
    details         JSONB,
    llm_model       VARCHAR(50),
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    cost_usd        DECIMAL(10,6),
    latency_ms      INTEGER,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Месячные партиции
CREATE TABLE agent_logs_2026_01 PARTITION OF agent_logs
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE agent_logs_2026_02 PARTITION OF agent_logs
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
-- ... добавлять партиции по мере необходимости
```

### Archive Cron Jobs

```sql
-- Еженедельно: архивация старых jobs/bids
INSERT INTO jobs_archive SELECT * FROM jobs
    WHERE status IN ('lost', 'disqualified') AND discovered_at < NOW() - INTERVAL '90 days';
DELETE FROM jobs
    WHERE status IN ('lost', 'disqualified') AND discovered_at < NOW() - INTERVAL '90 days';

-- GDPR: удаление unsubscribed leads через 30 дней
DELETE FROM leads
    WHERE status = 'unsubscribed' AND updated_at < NOW() - INTERVAL '30 days';

-- Cleanup: старые партиции agent_logs (>6 месяцев)
-- DROP TABLE IF EXISTS agent_logs_2025_07;
```

---

## Connection Pool Configuration

```python
engine = create_async_engine(
    os.getenv("DATABASE_URL").replace("postgresql://", "postgresql+asyncpg://"),
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)
```

---

## PostgreSQL Extensions Setup

```sql
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector для vector types
-- vectorscale NOT used (HNSW via pgvector)   -- pgvector для HNSW (pgvector) indexes
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- для gen_random_uuid()
```

---

## Rollback Policy

- Хранить последние 10 миграций reversible
- Всегда тестировать DOWN миграцию перед деплоем UP
- LangGraph checkpoint history: 10 checkpoints на thread (cleanup trigger)

---

## Indexes Summary

| Таблица | Ключевые индексы |
|---------|-----------------|
| jobs | `idx_jobs_status(status, discovered_at)`, `idx_jobs_platform(platform, status)`, `idx_jobs_score(score) WHERE status='qualified'` |
| bids | `idx_bids_status(status)`, `idx_bids_job(job_id)` |
| projects | `idx_projects_status`, `idx_projects_type(type, status)`, `idx_projects_deadline WHERE status NOT IN (...)` |
| tasks | `idx_tasks_project(project_id, status)`, `idx_tasks_agent(assigned_agent, status)` |
| artifacts | `idx_artifacts_project(project_id)`, `idx_artifacts_type(type)` |
| hitl_queue | `idx_hitl_status(status, priority, created_at)`, `idx_hitl_type(type, status)` |
| leads | `idx_leads_city(city, category, status)`, `idx_leads_h3(h3_index)`, `idx_leads_status` |
| agent_logs | `idx_agent_logs_agent(agent_name, created_at)`, `idx_agent_logs_project(project_id, created_at)`, `idx_agent_logs_event(event_type, created_at)` |
| knowledge_base | `idx_knowledge_type(type, category)`, `idx_knowledge_embedding USING hnsw` |
| semantic_cache | `idx_cache_embedding USING hnsw`, `idx_cache_expires(expires_at)`, `idx_cache_type(query_type)` |
| ab_test_results | `idx_ab_test(test_name, variant_id)` |
| langgraph_checkpoints | `idx_checkpoints_thread(thread_id, created_at)` |
| revisions | `idx_revisions_project(project_id, status)` |

---

## Security Considerations

1. **Credentials encryption:** `platform_accounts.credentials` зашифрован Fernet (symmetric). Ключи хранятся в env vars
2. **Row-level security:** Доступен для multi-tenant если потребуется
3. **Audit trail:** `agent_logs` фиксирует все действия агентов
4. **GDPR:** `leads` поддерживает `status = 'unsubscribed'` с автоудалением через 30 дней
5. **SQL injection prevention:** `_escape_like()` хелпер экранирует `%`, `_`, `\` для ILIKE запросов
6. **pgvector params:** SQL с `$1`-style asyncpg params + `# noqa: S608` для обхода ruff false positive
