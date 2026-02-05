# 🔄 LangGraph State Management

**Version:** 1.0  
**Framework:** LangGraph 1.0  
**Storage:** PostgreSQL (primary) + Redis (cache)

---

## 📋 Overview

LangGraph uses a **StateGraph** that tracks the full execution state of multi-agent workflows. This document defines how we persist, recover, and manage this state.

---

## 🗄️ Checkpoint Storage Strategy

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        STATE PERSISTENCE                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   LangGraph                                                              │
│      │                                                                   │
│      ▼                                                                   │
│   ┌──────────────────┐                                                   │
│   │ CheckpointSaver  │                                                   │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│      ┌─────┴─────┐                                                       │
│      ▼           ▼                                                       │
│   ┌──────┐   ┌──────┐                                                    │
│   │Redis │   │Postgres│                                                  │
│   │Cache │   │Storage │                                                  │
│   └──────┘   └────────┘                                                  │
│                                                                          │
│   Hot state    Cold state                                                │
│   (TTL: 1h)    (permanent)                                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why Dual Storage?

| Layer | Purpose | TTL | Use Case |
|-------|---------|-----|----------|
| Redis | Hot cache | 1 hour | Active workflows, fast access |
| PostgreSQL | Persistent | Permanent | Recovery, audit, history |

---

## 📊 State Schema

### AgentState TypedDict

```python
from typing import TypedDict, List, Optional, Literal
from langchain_core.messages import BaseMessage
from datetime import datetime

class ProjectContext(TypedDict):
    project_id: str
    job_id: str
    platform: Literal["freelancer", "upwork", "flru", "kwork"]
    client: dict
    requirements: str
    budget: float
    deadline: datetime

class AgentState(TypedDict):
    # Identity
    thread_id: str          # Unique workflow instance
    checkpoint_id: str      # Current checkpoint
    
    # Project context
    project: ProjectContext
    
    # Current execution
    current_agent: str      # scout, bid, planner, dev, etc.
    current_task: Optional[dict]
    
    # Artifacts produced
    artifacts: dict         # {agent_name: [artifact_ids]}
    
    # Messages (conversation history)
    messages: List[BaseMessage]
    
    # Control flow
    next_agent: Optional[str]
    requires_hitl: bool
    hitl_request_id: Optional[str]
    
    # Error handling
    retry_count: int
    errors: List[str]
    
    # Metadata
    created_at: datetime
    updated_at: datetime
    status: Literal["active", "paused", "completed", "failed"]
```

---

## 🗃️ PostgreSQL Schema

### Checkpoints Table

```sql
CREATE TABLE langgraph_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    
    -- Serialized state
    state_data JSONB NOT NULL,
    
    -- Metadata for queries
    current_agent TEXT,
    status TEXT DEFAULT 'active',
    requires_hitl BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Indexes
    UNIQUE(thread_id, checkpoint_id)
);

CREATE INDEX idx_checkpoints_thread ON langgraph_checkpoints(thread_id);
CREATE INDEX idx_checkpoints_status ON langgraph_checkpoints(status);
CREATE INDEX idx_checkpoints_hitl ON langgraph_checkpoints(requires_hitl) 
    WHERE requires_hitl = TRUE;
```

### Checkpoint History (for rollback)

```sql
CREATE TABLE langgraph_checkpoint_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    state_data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Keep last 10 checkpoints per thread
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

## 🔧 Checkpoint Saver Implementation

```python
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import redis
import asyncpg
from datetime import timedelta

class HybridCheckpointSaver(BaseCheckpointSaver):
    """
    Dual-layer checkpoint saver:
    - Redis for hot cache (fast access)
    - PostgreSQL for persistence (durability)
    """
    
    def __init__(
        self,
        redis_url: str,
        postgres_url: str,
        redis_ttl: timedelta = timedelta(hours=1)
    ):
        super().__init__(serde=JsonPlusSerializer())
        self.redis = redis.from_url(redis_url)
        self.postgres_url = postgres_url
        self.redis_ttl = redis_ttl
        
    async def aget(self, config: dict) -> Optional[dict]:
        """Get checkpoint, try Redis first, fallback to Postgres."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")
        
        # Try Redis first
        cache_key = f"checkpoint:{thread_id}:{checkpoint_id or 'latest'}"
        cached = self.redis.get(cache_key)
        if cached:
            return self.serde.loads(cached)
        
        # Fallback to PostgreSQL
        async with asyncpg.connect(self.postgres_url) as conn:
            if checkpoint_id:
                row = await conn.fetchrow(
                    "SELECT state_data FROM langgraph_checkpoints "
                    "WHERE thread_id = $1 AND checkpoint_id = $2",
                    thread_id, checkpoint_id
                )
            else:
                row = await conn.fetchrow(
                    "SELECT state_data FROM langgraph_checkpoints "
                    "WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
                    thread_id
                )
            
            if row:
                # Warm cache
                self.redis.setex(
                    cache_key,
                    self.redis_ttl,
                    self.serde.dumps(row["state_data"])
                )
                return row["state_data"]
        
        return None
    
    async def aput(self, config: dict, checkpoint: dict) -> dict:
        """Save checkpoint to both Redis and PostgreSQL."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint.get("id", str(uuid.uuid4()))
        parent_id = checkpoint.get("parent_id")
        
        serialized = self.serde.dumps(checkpoint)
        
        # Save to Redis (hot cache)
        cache_key = f"checkpoint:{thread_id}:{checkpoint_id}"
        latest_key = f"checkpoint:{thread_id}:latest"
        self.redis.setex(cache_key, self.redis_ttl, serialized)
        self.redis.setex(latest_key, self.redis_ttl, serialized)
        
        # Save to PostgreSQL (persistent)
        async with asyncpg.connect(self.postgres_url) as conn:
            await conn.execute("""
                INSERT INTO langgraph_checkpoints 
                (thread_id, checkpoint_id, parent_checkpoint_id, state_data,
                 current_agent, status, requires_hitl)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (thread_id, checkpoint_id) 
                DO UPDATE SET state_data = $4, updated_at = NOW()
            """,
                thread_id,
                checkpoint_id,
                parent_id,
                checkpoint,
                checkpoint.get("current_agent"),
                checkpoint.get("status", "active"),
                checkpoint.get("requires_hitl", False)
            )
            
            # Also save to history for rollback
            await conn.execute("""
                INSERT INTO langgraph_checkpoint_history 
                (thread_id, checkpoint_id, state_data)
                VALUES ($1, $2, $3)
            """, thread_id, checkpoint_id, checkpoint)
        
        return {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
```

---

## 🔄 Recovery Procedures

### Scenario 1: Resume from HITL Pause

```python
async def resume_from_hitl(thread_id: str, hitl_response: dict):
    """Resume workflow after HITL approval."""
    
    # Get latest checkpoint
    checkpoint = await saver.aget({
        "configurable": {"thread_id": thread_id}
    })
    
    # Update state with HITL response
    checkpoint["hitl_response"] = hitl_response
    checkpoint["requires_hitl"] = False
    
    # Resume graph execution
    graph = build_agent_graph()
    async for event in graph.astream(
        checkpoint,
        config={"configurable": {"thread_id": thread_id}}
    ):
        handle_event(event)
```

### Scenario 2: Recover from Crash

```python
async def recover_active_workflows():
    """Recover all workflows that were active when system crashed."""
    
    async with asyncpg.connect(postgres_url) as conn:
        # Find all non-completed workflows
        rows = await conn.fetch("""
            SELECT DISTINCT ON (thread_id) 
                thread_id, state_data
            FROM langgraph_checkpoints
            WHERE status NOT IN ('completed', 'failed')
            ORDER BY thread_id, created_at DESC
        """)
        
        for row in rows:
            thread_id = row["thread_id"]
            state = row["state_data"]
            
            logger.info(f"Recovering workflow {thread_id}")
            
            # Resume from last checkpoint
            graph = build_agent_graph()
            await graph.ainvoke(
                state,
                config={"configurable": {"thread_id": thread_id}}
            )
```

### Scenario 3: Rollback to Previous Checkpoint

```python
async def rollback_workflow(thread_id: str, steps_back: int = 1):
    """Rollback workflow to a previous checkpoint."""
    
    async with asyncpg.connect(postgres_url) as conn:
        # Get checkpoint N steps back
        row = await conn.fetchrow("""
            SELECT checkpoint_id, state_data
            FROM langgraph_checkpoint_history
            WHERE thread_id = $1
            ORDER BY created_at DESC
            OFFSET $2 LIMIT 1
        """, thread_id, steps_back)
        
        if not row:
            raise ValueError(f"No checkpoint found {steps_back} steps back")
        
        # Restore as current state
        await saver.aput(
            {"configurable": {"thread_id": thread_id}},
            row["state_data"]
        )
        
        logger.info(f"Rolled back {thread_id} to {row['checkpoint_id']}")
```

---

## 📊 State Queries

### Active Workflows

```sql
SELECT thread_id, current_agent, status, created_at
FROM langgraph_checkpoints
WHERE status = 'active'
ORDER BY created_at DESC;
```

### HITL Pending

```sql
SELECT thread_id, state_data->>'project' as project, created_at
FROM langgraph_checkpoints
WHERE requires_hitl = TRUE
AND status = 'active'
ORDER BY created_at ASC;
```

### Workflow History

```sql
SELECT checkpoint_id, current_agent, created_at
FROM langgraph_checkpoint_history
WHERE thread_id = $1
ORDER BY created_at DESC;
```

---

## ⚙️ Configuration

```python
# config.py
LANGGRAPH_CONFIG = {
    "checkpoint": {
        "redis_url": "redis://localhost:6379/1",
        "postgres_url": "postgresql://mas:password@localhost/mas",
        "redis_ttl_hours": 1,
        "max_history_per_thread": 10,
    },
    "recovery": {
        "auto_recover_on_startup": True,
        "max_retry_count": 3,
    }
}
```
