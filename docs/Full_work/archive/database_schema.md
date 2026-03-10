# 📊 Database Schema — Multi-Agent System

**Version:** 1.1  
**Database:** PostgreSQL 16+ with pgvector + pgvectorscale  
**ORM:** SQLAlchemy 2.0 / Alembic migrations

> [!TIP]
> **Extensions required:** `pgvector` for vector storage, `pgvectorscale` for DiskANN indexes (11x faster than HNSW at scale)

---

## 🏗️ Schema Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              DATABASE SCHEMA                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │    jobs     │───▶│    bids     │───▶│  projects   │───▶│   tasks     │   │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘   │
│         │                 │                   │                  │          │
│         ▼                 ▼                   ▼                  ▼          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │ hitl_queue  │    │  revisions  │    │  artifacts  │    │ agent_logs  │   │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘   │
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │   leads     │───▶│  campaigns  │    │   users     │    │  accounts   │   │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Tables Definition

### 1. `users` — Dashboard Users
```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),                    -- null if OAuth only
    name            VARCHAR(100),
    role            VARCHAR(20) DEFAULT 'owner',     -- 'owner', 'viewer'
    telegram_chat_id BIGINT,                         -- for HITL notifications
    settings        JSONB DEFAULT '{}',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login_at   TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_users_email ON users(email);
```

### 2. `platform_accounts` — Freelance Platform Credentials
```sql
CREATE TABLE platform_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
    platform        VARCHAR(50) NOT NULL,            -- 'freelancer', 'upwork', 'fl_ru', 'kwork'
    username        VARCHAR(255),
    credentials     JSONB NOT NULL,                  -- encrypted: cookies, tokens
    status          VARCHAR(20) DEFAULT 'active',    -- 'active', 'suspended', 'rate_limited'
    profile_url     TEXT,
    stats           JSONB DEFAULT '{}',              -- rating, completed_jobs, etc
    last_health_check TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_accounts_platform ON platform_accounts(platform, status);
```

### 3. `jobs` — Discovered Freelance Jobs
```sql
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        VARCHAR(50) NOT NULL,
    external_id     VARCHAR(255) NOT NULL,           -- platform's job ID
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    budget_min      DECIMAL(10,2),
    budget_max      DECIMAL(10,2),
    budget_type     VARCHAR(20),                     -- 'fixed', 'hourly'
    currency        VARCHAR(3) DEFAULT 'USD',
    client_info     JSONB,                           -- rating, history, country
    skills_required TEXT[],
    deadline        TIMESTAMP WITH TIME ZONE,
    platform_deadline TIMESTAMP WITH TIME ZONE,      -- deadline to submit bid
    
    -- Processing status
    status          VARCHAR(30) DEFAULT 'new',       -- new, qualified, disqualified, bid_sent, won, lost
    score           DECIMAL(3,2),                    -- 0.00-1.00 match score
    disqualify_reason VARCHAR(255),
    
    -- Metadata
    discovered_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    url             TEXT,
    raw_data        JSONB,
    
    UNIQUE(platform, external_id)
);

CREATE INDEX idx_jobs_status ON jobs(status, discovered_at DESC);
CREATE INDEX idx_jobs_platform ON jobs(platform, status);
CREATE INDEX idx_jobs_score ON jobs(score DESC) WHERE status = 'qualified';
```

### 4. `bids` — Proposals/Bids Sent
```sql
CREATE TABLE bids (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES jobs(id) ON DELETE CASCADE,
    account_id      UUID REFERENCES platform_accounts(id),
    
    -- Proposal content
    proposal_text   TEXT NOT NULL,
    bid_amount      DECIMAL(10,2) NOT NULL,
    estimated_days  INTEGER,
    portfolio_items UUID[],                          -- references to portfolio
    
    -- Status
    status          VARCHAR(30) DEFAULT 'draft',     -- draft, hitl_pending, approved, sent, won, lost
    hitl_request_id UUID,
    
    -- Timestamps
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sent_at         TIMESTAMP WITH TIME ZONE,
    response_at     TIMESTAMP WITH TIME ZONE,        -- when client responded
    
    -- Analytics
    version         INTEGER DEFAULT 1,               -- for A/B testing
    generation_model VARCHAR(50),                    -- which LLM generated
    
    UNIQUE(job_id, account_id)
);

CREATE INDEX idx_bids_status ON bids(status);
CREATE INDEX idx_bids_job ON bids(job_id);
```

### 5. `projects` — Active Projects (won bids)
```sql
CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bid_id          UUID REFERENCES bids(id),
    job_id          UUID REFERENCES jobs(id),
    
    -- Project info
    title           VARCHAR(500) NOT NULL,
    type            VARCHAR(30) DEFAULT 'freelance', -- 'freelance', 'digitalization'
    client_name     VARCHAR(255),
    client_contact  JSONB,
    
    -- Financial
    agreed_amount   DECIMAL(10,2),
    currency        VARCHAR(3) DEFAULT 'USD',
    paid_amount     DECIMAL(10,2) DEFAULT 0,
    
    -- Timeline
    start_date      DATE,
    deadline        DATE,
    completed_at    TIMESTAMP WITH TIME ZONE,
    
    -- Status
    status          VARCHAR(30) DEFAULT 'planning',  -- planning, in_progress, review, delivered, completed, cancelled
    progress        INTEGER DEFAULT 0,               -- 0-100%
    revision_count  INTEGER DEFAULT 0,
    
    -- Kanban position
    kanban_column   VARCHAR(30),                     -- board column
    kanban_order    INTEGER,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_projects_type ON projects(type, status);
CREATE INDEX idx_projects_deadline ON projects(deadline) WHERE status NOT IN ('completed', 'cancelled');
```

### 6. `tasks` — Project Task Breakdown
```sql
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    parent_task_id  UUID REFERENCES tasks(id),       -- for subtasks
    
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    assigned_agent  VARCHAR(30),                     -- 'dev', 'content', 'design'
    
    -- Execution
    status          VARCHAR(30) DEFAULT 'pending',   -- pending, in_progress, review, done, blocked
    priority        INTEGER DEFAULT 0,
    estimated_hours DECIMAL(5,2),
    actual_hours    DECIMAL(5,2),
    
    -- Dependencies
    depends_on      UUID[],                          -- task IDs this depends on
    
    -- Output
    artifact_ids    UUID[],
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_tasks_project ON tasks(project_id, status);
CREATE INDEX idx_tasks_agent ON tasks(assigned_agent, status);
```

### 7. `artifacts` — Generated Files/Code
```sql
CREATE TABLE artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    task_id         UUID REFERENCES tasks(id),
    
    type            VARCHAR(30) NOT NULL,            -- 'code', 'document', 'image', 'archive'
    filename        VARCHAR(255) NOT NULL,
    mime_type       VARCHAR(100),
    size_bytes      BIGINT,
    
    -- Storage
    storage_type    VARCHAR(20) DEFAULT 'db',        -- 'db', 's3'
    content         BYTEA,                           -- for small files
    storage_url     TEXT,                            -- S3 URL for large files
    
    -- Versioning
    version         INTEGER DEFAULT 1,
    previous_version_id UUID REFERENCES artifacts(id),
    
    -- Metadata
    generated_by    VARCHAR(30),                     -- which agent
    llm_model       VARCHAR(50),
    quality_score   DECIMAL(3,2),                    -- from Critic
    security_scan   JSONB,                           -- Semgrep results
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_artifacts_project ON artifacts(project_id);
CREATE INDEX idx_artifacts_type ON artifacts(type);
```

### 8. `hitl_queue` — Human-in-the-Loop Requests
```sql
CREATE TABLE hitl_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    type            VARCHAR(30) NOT NULL,            -- 'bid_approval', 'code_review', 'delivery', 'revision', 'alert'
    priority        VARCHAR(10) DEFAULT 'normal',    -- 'urgent', 'normal', 'low'
    
    -- Related entities
    bid_id          UUID REFERENCES bids(id),
    project_id      UUID REFERENCES projects(id),
    task_id         UUID REFERENCES tasks(id),
    
    -- Content
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    payload         JSONB NOT NULL,                  -- type-specific data
    
    -- Actions
    available_actions TEXT[] NOT NULL,               -- ['approve', 'reject', 'edit', 'later']
    
    -- Status
    status          VARCHAR(20) DEFAULT 'pending',   -- 'pending', 'resolved', 'expired', 'skipped'
    resolution      VARCHAR(30),                     -- what action was taken
    resolution_note TEXT,
    resolved_by     UUID REFERENCES users(id),
    
    -- Timing
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at      TIMESTAMP WITH TIME ZONE,
    resolved_at     TIMESTAMP WITH TIME ZONE,
    
    -- Notifications
    telegram_sent   BOOLEAN DEFAULT FALSE,
    email_sent      BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_hitl_status ON hitl_queue(status, priority, created_at);
CREATE INDEX idx_hitl_type ON hitl_queue(type, status);
```

### 9. `revisions` — Client Revision Requests
```sql
CREATE TABLE revisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    
    -- Classification
    type            VARCHAR(20) NOT NULL,            -- 'minor', 'major', 'scope_creep', 'unclear'
    
    -- Content
    client_feedback TEXT NOT NULL,
    parsed_items    JSONB,                           -- structured extraction
    
    -- Processing
    status          VARCHAR(20) DEFAULT 'new',       -- 'new', 'processing', 'completed', 'escalated'
    assigned_agent  VARCHAR(30),
    hitl_required   BOOLEAN DEFAULT FALSE,
    
    -- Resolution
    resolution_note TEXT,
    tasks_created   UUID[],                          -- new tasks spawned
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_revisions_project ON revisions(project_id, status);
```

### 10. `leads` — Cold Outreach Leads (Pipeline B)
```sql
CREATE TABLE leads (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Business info
    name            VARCHAR(255) NOT NULL,
    category        VARCHAR(100),                    -- 'restaurant', 'salon', etc
    address         TEXT,
    city            VARCHAR(100),
    country         VARCHAR(50),
    
    -- Geo
    latitude        DECIMAL(10,8),
    longitude       DECIMAL(11,8),
    h3_index        VARCHAR(20),                     -- H3 hexagon ID
    
    -- Contact
    phone           VARCHAR(50),
    email           VARCHAR(255),
    website         VARCHAR(255),                    -- null = no website (our target)
    social_links    JSONB,
    
    -- Enrichment
    enrichment_source VARCHAR(20),                   -- 'osint', 'hunter', 'apollo', 'not_found'
    enrichment_cost DECIMAL(5,4),
    enrichment_data JSONB,
    
    -- Status
    status          VARCHAR(20) DEFAULT 'new',       -- 'new', 'enriched', 'contacted', 'responded', 'converted', 'unsubscribed'
    
    -- Source
    osm_id          BIGINT,
    discovered_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(osm_id)
);

CREATE INDEX idx_leads_city ON leads(city, category, status);
CREATE INDEX idx_leads_h3 ON leads(h3_index);
CREATE INDEX idx_leads_status ON leads(status);
```

### 11. `email_campaigns` — Outreach Campaigns
```sql
CREATE TABLE email_campaigns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    name            VARCHAR(255) NOT NULL,
    subject_template TEXT NOT NULL,
    body_template   TEXT NOT NULL,
    
    -- Targeting
    target_cities   TEXT[],
    target_categories TEXT[],
    
    -- Status
    status          VARCHAR(20) DEFAULT 'draft',     -- 'draft', 'hitl_pending', 'active', 'paused', 'completed'
    
    -- Stats
    total_leads     INTEGER DEFAULT 0,
    sent_count      INTEGER DEFAULT 0,
    open_count      INTEGER DEFAULT 0,
    reply_count     INTEGER DEFAULT 0,
    bounce_count    INTEGER DEFAULT 0,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at      TIMESTAMP WITH TIME ZONE
);

CREATE TABLE campaign_leads (
    campaign_id     UUID REFERENCES email_campaigns(id) ON DELETE CASCADE,
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    
    status          VARCHAR(20) DEFAULT 'pending',   -- 'pending', 'sent', 'opened', 'replied', 'bounced'
    personalized_subject TEXT,
    personalized_body TEXT,
    sent_at         TIMESTAMP WITH TIME ZONE,
    opened_at       TIMESTAMP WITH TIME ZONE,
    replied_at      TIMESTAMP WITH TIME ZONE,
    
    PRIMARY KEY (campaign_id, lead_id)
);
```

### 12. `agent_logs` — Agent Activity Logs
```sql
CREATE TABLE agent_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name      VARCHAR(30) NOT NULL,
    
    -- Context
    project_id      UUID REFERENCES projects(id),
    task_id         UUID REFERENCES tasks(id),
    job_id          UUID REFERENCES jobs(id),
    
    -- Event
    event_type      VARCHAR(50) NOT NULL,            -- 'started', 'completed', 'error', 'llm_call', 'tool_use'
    message         TEXT,
    details         JSONB,
    
    -- LLM tracking
    llm_model       VARCHAR(50),
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    cost_usd        DECIMAL(10,6),
    latency_ms      INTEGER,
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Partition by month for performance
CREATE INDEX idx_agent_logs_agent ON agent_logs(agent_name, created_at DESC);
CREATE INDEX idx_agent_logs_project ON agent_logs(project_id, created_at DESC);
CREATE INDEX idx_agent_logs_event ON agent_logs(event_type, created_at DESC);
```

### 13. `agent_heartbeats` — Agent Health Monitoring
```sql
CREATE TABLE agent_heartbeats (
    agent_name      VARCHAR(30) PRIMARY KEY,
    status          VARCHAR(20) DEFAULT 'idle',      -- 'idle', 'working', 'error', 'dead'
    current_task    TEXT,
    last_heartbeat  TIMESTAMP WITH TIME ZONE,
    restart_count   INTEGER DEFAULT 0,
    error_message   TEXT,
    metadata        JSONB
);
```

### 14. `langgraph_checkpoints` — LangGraph State Persistence
```sql
-- Standard LangGraph checkpoint schema
CREATE TABLE langgraph_checkpoints (
    thread_id       VARCHAR(255) NOT NULL,
    checkpoint_id   VARCHAR(255) NOT NULL,
    parent_id       VARCHAR(255),
    checkpoint      JSONB NOT NULL,
    metadata        JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    PRIMARY KEY (thread_id, checkpoint_id)
);

CREATE INDEX idx_checkpoints_thread ON langgraph_checkpoints(thread_id, created_at DESC);
```

### 15. `knowledge_base` — RAG Knowledge Items
```sql
CREATE TABLE knowledge_base (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    type            VARCHAR(30) NOT NULL,            -- 'proposal_template', 'portfolio_case', 'email_template'
    category        VARCHAR(100),                    -- 'webdev', 'mobile', 'restaurant', etc
    
    title           VARCHAR(255) NOT NULL,
    content         TEXT NOT NULL,
    
    -- Embeddings for RAG
    embedding       vector(3072),                    -- OpenAI text-embedding-3-large (3072 dim)

    -- Stats
    usage_count     INTEGER DEFAULT 0,
    success_rate    DECIMAL(3,2),                    -- win rate if used
    
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_knowledge_type ON knowledge_base(type, category);

-- DiskANN index for ultra-fast vector search (pgvectorscale)
-- 11x faster than HNSW at 99% recall on large datasets
CREATE INDEX idx_knowledge_embedding ON knowledge_base 
    USING diskann (embedding vector_cosine_ops);
```

### 16. `semantic_cache` — LLM Response Cache

> See `docs/semantic_cache.md` for full architecture.

```sql
CREATE TABLE semantic_cache (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_hash      TEXT UNIQUE,
    query           TEXT NOT NULL,
    response        TEXT NOT NULL,
    query_type      TEXT DEFAULT 'default',
    embedding       vector(3072),                    -- OpenAI text-embedding-3-large (3072 dim)
    hit_count       INTEGER DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL
);

-- DiskANN index for fast vector similarity search
CREATE INDEX idx_cache_embedding ON semantic_cache
    USING diskann (embedding vector_cosine_ops);

CREATE INDEX idx_cache_expires ON semantic_cache(expires_at);
CREATE INDEX idx_cache_type ON semantic_cache(query_type);
```

### 17. `ab_test_results` — A/B Testing for Proposals

```sql
CREATE TABLE ab_test_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    test_name       VARCHAR(100) NOT NULL,
    variant_id      VARCHAR(50) NOT NULL,
    job_id          UUID REFERENCES jobs(id),
    bid_id          UUID REFERENCES bids(id),

    -- Metrics
    impressions     INTEGER DEFAULT 0,
    responses       INTEGER DEFAULT 0,
    hires           INTEGER DEFAULT 0,

    -- Metadata
    template_name   VARCHAR(100),
    opening_style   VARCHAR(30),                     -- 'question', 'statement', 'story'
    tone            VARCHAR(30),                     -- 'professional', 'friendly', 'technical'

    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_ab_test ON ab_test_results(test_name, variant_id);
```

---

## 📅 Data Retention Policy

| Table | Retention | Strategy | Notes |
|-------|-----------|----------|-------|
| `agent_logs` | 6 months | Partition by month, drop old partitions | High volume, query by date range |
| `jobs` | 90 days active, then archive | Move to `jobs_archive` table | Keep won jobs indefinitely |
| `bids` | 90 days active, then archive | Move to `bids_archive` table | Keep won bids indefinitely |
| `leads` (unsubscribed) | 30 days | Hard delete | GDPR compliance |
| `semantic_cache` | TTL-based | Auto-expire via `expires_at` | Managed by cache system |
| `langgraph_checkpoints` | 30 days after completion | Delete completed thread checkpoints | Keep active threads |
| `hitl_queue` (resolved) | 90 days | Archive | Audit trail |

### Partition Example (agent_logs)

```sql
-- Convert agent_logs to partitioned table
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

-- Create monthly partitions
CREATE TABLE agent_logs_2026_01 PARTITION OF agent_logs
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE agent_logs_2026_02 PARTITION OF agent_logs
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
-- ... add partitions as needed

-- Cleanup cron (run monthly):
-- DROP TABLE IF EXISTS agent_logs_2025_07;  -- older than 6 months
```

### Archive Cron Job

```sql
-- Run weekly: archive old jobs/bids
INSERT INTO jobs_archive SELECT * FROM jobs
    WHERE status IN ('lost', 'disqualified') AND discovered_at < NOW() - INTERVAL '90 days';
DELETE FROM jobs
    WHERE status IN ('lost', 'disqualified') AND discovered_at < NOW() - INTERVAL '90 days';

-- GDPR: delete unsubscribed leads after 30 days
DELETE FROM leads
    WHERE status = 'unsubscribed' AND updated_at < NOW() - INTERVAL '30 days';
```

---

## 🔧 Migrations Strategy

### PostgreSQL Extensions Setup
```sql
-- Run once on database initialization (or add to init.sql)
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector for vector types
CREATE EXTENSION IF NOT EXISTS vectorscale;   -- pgvectorscale for DiskANN indexes
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- for gen_random_uuid()
```

### Alembic Setup

```bash
alembic init migrations
```

#### `alembic.ini` (key settings)

```ini
[alembic]
script_location = migrations
# Use async driver
sqlalchemy.url = postgresql+asyncpg://%(DATABASE_URL)s

[alembic:exclude]
# Exclude pgvector extension tables from autogenerate
tables = spatial_ref_sys
```

#### `migrations/env.py` (async configuration)

```python
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from src.core.models import Base  # Import all models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online():
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

#### SQLAlchemy Model Example

```python
# src/core/models.py
from sqlalchemy import Column, String, Integer, DateTime, DECIMAL, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import DeclarativeBase, relationship
from pgvector.sqlalchemy import Vector
import uuid
from datetime import datetime, timezone

class Base(DeclarativeBase):
    pass

class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform = Column(String(50), nullable=False)
    external_id = Column(String(255), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    budget_min = Column(DECIMAL(10, 2))
    budget_max = Column(DECIMAL(10, 2))
    status = Column(String(30), default="new")
    score = Column(DECIMAL(3, 2))
    discovered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    bids = relationship("Bid", back_populates="job")

class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(30), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(3072))  # OpenAI text-embedding-3-large
```

#### Connection Pool Configuration

```python
# src/core/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import os

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

### Migration naming convention:
```
YYYYMMDD_HHMM_description.py
# Example: 20260129_1200_initial_schema.py
```

### Rollback policy:
- Keep last 10 migrations reversible
- Always test DOWN migration before deploying UP

---

## 📊 Indexes Summary

| Table | Key Indexes |
|-------|-------------|
| jobs | status, platform, score |
| bids | status, job_id |
| projects | status, type, deadline |
| hitl_queue | status+priority+created |
| leads | city+category, h3_index |
| agent_logs | agent+time, project+time |

---

## 🔐 Security Considerations

1. **Credentials encryption:** `platform_accounts.credentials` field should use pgcrypto
2. **Row-level security:** Enable for multi-tenant if needed
3. **Audit trail:** `agent_logs` captures all actions
4. **GDPR:** `leads` table supports `status = 'unsubscribed'`
