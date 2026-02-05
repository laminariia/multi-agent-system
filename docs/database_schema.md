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
    embedding       vector(384),                     -- for semantic search
    
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
