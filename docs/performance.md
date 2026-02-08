# 📊 Performance & Scalability

**Version:** 1.0  
**Target:** 100 projects/month, 20+ concurrent workflows

---

## 🎯 Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Scout throughput | 100 jobs/min | Jobs scanned per minute |
| Bid generation | <15s per proposal | LLM latency + processing |
| Concurrent projects | Phase 1: 5 параллельных, Phase 2+: до 15 по tier | Active workflows |
| HITL response | <5s (P95) | Dashboard load time |
| Database queries | <100ms (P95) | PostgreSQL response |
| API latency | <200ms (P95) | Litestar endpoints |

---

## 📐 Capacity Planning

### Agent Throughput (24/7 Operation)

| Agent | Per Cycle | Cycles/Hour | Daily Throughput | Bottleneck |
|-------|-----------|-------------|------------------|------------|
| Scout | 10 jobs | 12 | ~2,880 jobs scanned | Platform rate limits |
| Bid | 1 proposal | 6-8 | **50-100 bids/day** | HITL approval speed |
| Planner | 1 plan | On-demand | ~10-15 plans/day | Claude Opus RPM |
| Dev | 1 task | Variable | 5-10 tasks/day | Code complexity |
| Content | 1 piece | 4-6 | ~30-50 pieces/day | Gemini Flash RPM |
| Design | 1 asset | 2-3 | ~15-30 assets/day | Gemini Pro RPM |
| Critic | 1 review | On-demand | ~20-30 reviews/day | GPT 5.3 Codex RPM |
| Packager | 1 delivery | On-demand | ~3-5 deliveries/day | Depends on project volume |
| GeoScout | 50 hexagons | 2-3 | ~500-750 businesses/day | Overpass API limits |
| Outreach | 10 emails | 5 | ~50 emails/day (warm-up phase) | Email warm-up status |

### Bid Volume Reconciliation

| Source | Bids/Day | Notes |
|--------|----------|-------|
| CLAUDE.md | 50-100 | Conservative estimate for Phase 1 |
| rate_limiting.md | 150 | Aggressive 24/7 target for Phase 2+ |
| **Reconciled** | **50-100 (Phase 1), up to 150 (Phase 2+)** | Scale with platform tier upgrades |

### Pipeline Capacity

```
Pipeline A (Freelance):
  Scout (2880/day) → Filter (10-15%) → Bid (50-150/day) → HITL → Win (5-15%) → 3-10 projects/day

Pipeline B (Outreach):
  GeoScout (500-750/day) → Enrich (60-80%) → Outreach (50/day warm-up) → Reply (5-10%)
```

---

## 🏗️ Bottleneck Analysis

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      SYSTEM BOTTLENECKS                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. LLM API Rate Limits (PRIMARY BOTTLENECK)                             │
│     ├── Gemini Flash: 15 RPM (free) → 300 RPM (Tier 1)                   │
│     ├── Claude Opus: 50 RPM (all tiers)                                  │
│     └── Solution: Semantic cache, batching, key rotation                 │
│                                                                          │
│  2. HITL Human Speed (SECONDARY BOTTLENECK)                              │
│     ├── Average approval time: 2-5 minutes                               │
│     └── Solution: Batch approvals, async notifications                   │
│                                                                          │
│  3. Platform Rate Limits                                                 │
│     ├── Freelancer API: 100 req/hour                                     │
│     ├── Upwork GraphQL: 60 req/min                                       │
│     └── Solution: Caching, smart polling intervals                       │
│                                                                          │
│  4. Database I/O (MINOR)                                                 │
│     ├── Checkpoint writes: ~50-100/min at peak                           │
│     └── Solution: Connection pooling, proper indexes                     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🗄️ Database Performance

### Critical Indexes

```sql
-- Jobs table (high read frequency)
CREATE INDEX idx_jobs_status_platform ON jobs(status, platform);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX idx_jobs_budget ON jobs(budget) WHERE status = 'active';

-- HITL requests (frequent queries)
CREATE INDEX idx_hitl_status_priority ON hitl_requests(status, priority);
CREATE INDEX idx_hitl_created_pending ON hitl_requests(created_at) 
    WHERE status = 'pending';

-- Checkpoints (recovery queries)
CREATE INDEX idx_checkpoints_thread ON langgraph_checkpoints(thread_id);
CREATE INDEX idx_checkpoints_status ON langgraph_checkpoints(status) 
    WHERE status = 'active';

-- Proposals (analytics)
CREATE INDEX idx_proposals_status ON proposals(status);
CREATE INDEX idx_proposals_job ON proposals(job_id);

-- Leads (geo queries)
CREATE INDEX idx_leads_h3_index ON leads USING GIST (h3_index);
CREATE INDEX idx_leads_status ON leads(status);
```

### Connection Pooling

```python
# config.py
DATABASE_CONFIG = {
    "min_size": 5,
    "max_size": 20,
    "max_inactive_connection_lifetime": 300,
    "timeout": 30,
}

# Usage with asyncpg
pool = await asyncpg.create_pool(
    DATABASE_URL,
    **DATABASE_CONFIG
)
```

### Query Optimization Examples

```sql
-- ❌ Slow: Full table scan
SELECT * FROM jobs WHERE description ILIKE '%react%';

-- ✅ Fast: Use full-text search
CREATE INDEX idx_jobs_fts ON jobs USING GIN (to_tsvector('english', description));
SELECT * FROM jobs WHERE to_tsvector('english', description) @@ to_tsquery('react');

-- ❌ Slow: N+1 queries
for job in jobs:
    proposals = SELECT * FROM proposals WHERE job_id = job.id

-- ✅ Fast: Single query with JOIN
SELECT j.*, p.* FROM jobs j 
LEFT JOIN proposals p ON j.id = p.job_id 
WHERE j.status = 'active';
```

---

## 📦 Caching Strategy

### Multi-Layer Cache

| Layer | Storage | TTL | Use Case |
|-------|---------|-----|----------|
| L1 | In-memory | 60s | Hot data (active workflows) |
| L2 | Valkey | 5min-24h | LLM responses, job listings |
| L3 | PostgreSQL | Permanent | Historical data, checkpoints |

### Semantic Cache (LLM)

```python
# Already in semantic_cache.md, key metrics:
SEMANTIC_CACHE_CONFIG = {
    "similarity_threshold": 0.92,
    "ttl_hours": 24,
    "max_entries": 10000,
    "expected_hit_rate": 0.15  # 15% cost savings
}
```

### Redis Cache Patterns

```python
# Job listings cache
CACHE_KEYS = {
    "jobs:freelancer:active": 300,      # 5 min
    "jobs:upwork:active": 300,          # 5 min
    "llm:response:{hash}": 86400,       # 24 hours
    "checkpoint:{thread_id}:latest": 3600,  # 1 hour
    "rate_limit:{provider}:{key}": 60,  # 1 min
}
```

---

## 📈 Horizontal Scaling

### When to Scale

| Trigger | Threshold | Action |
|---------|-----------|--------|
| CPU | >80% for 5min | Add worker replica |
| Memory | >85% | Add worker replica |
| Queue depth | >50 jobs | Add worker replica |
| LLM wait time | >30s avg | Add API keys |

### Docker Compose Scaling

```yaml
# docker-compose.scale.yml
services:
  worker:
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '1'
          memory: 2G
```

```bash
# Scale up workers
docker-compose up -d --scale worker=3

# Scale down
docker-compose up -d --scale worker=1
```

### Valkey-Based Work Distribution

```python
# Workers pull from Valkey queue (natural load balancing)
async def worker_loop():
    while True:
        # BRPOP blocks until job available
        _, job_data = await redis.brpop("job_queue", timeout=30)
        if job_data:
            await process_job(json.loads(job_data))
```

---

## 🔬 Load Testing

### Locust Configuration

```python
# locustfile.py
from locust import HttpUser, task, between

class MASUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def check_hitl_queue(self):
        self.client.get("/api/v1/hitl/queue")

    @task(1)
    def submit_job(self):
        self.client.post("/api/v1/jobs/scan", json={
            "platform": "freelancer",
            "keywords": ["react", "python"]
        })

    @task(2)
    def get_project_status(self):
        self.client.get("/api/v1/projects/status")
```

### Running Load Tests

```bash
# Install
pip install locust

# Run (web UI at localhost:8089)
locust -f locustfile.py --host=http://localhost:8000

# Headless mode
locust -f locustfile.py --host=http://localhost:8000 \
    --users 50 --spawn-rate 5 --run-time 10m --headless
```

### Target Metrics

| Scenario | Users | RPS Target | P95 Latency |
|----------|-------|------------|-------------|
| Normal | 10 | 50 | <200ms |
| Peak | 50 | 200 | <500ms |
| Stress | 100 | 300 | <1s |

---

## 📊 Monitoring Metrics

### Prometheus Metrics

```python
from prometheus_client import Counter, Gauge, Histogram

# Request metrics
request_count = Counter(
    'mas_requests_total',
    'Total requests',
    ['endpoint', 'status']
)

request_latency = Histogram(
    'mas_request_latency_seconds',
    'Request latency',
    ['endpoint'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Business metrics
active_projects = Gauge('mas_active_projects', 'Active projects', ['complexity'])
hitl_queue_size = Gauge('mas_hitl_queue_size', 'HITL pending items')
llm_cost_today = Gauge('mas_llm_cost_usd', 'LLM spend today')

# Agent metrics
agent_duration = Histogram(
    'mas_agent_duration_seconds',
    'Agent execution time',
    ['agent_name']
)
```

### Grafana Dashboard Queries

```promql
# Request rate
rate(mas_requests_total[5m])

# P95 latency
histogram_quantile(0.95, rate(mas_request_latency_seconds_bucket[5m]))

# Active projects by complexity
mas_active_projects

# LLM cost trend
increase(mas_llm_cost_usd[24h])

# Agent performance
histogram_quantile(0.95, rate(mas_agent_duration_seconds_bucket[5m]))
```

---

## ⚠️ Performance Alerts

| Alert | Condition | Severity | Action |
|-------|-----------|----------|--------|
| High latency | P95 > 1s for 5min | Warning | Check LLM/DB |
| Queue backup | Depth > 50 for 10min | Warning | Scale workers |
| LLM throttled | Error rate > 10% | Error | Check rate limits |
| DB slow | Query time > 500ms | Warning | Check indexes |
| OOM risk | Memory > 90% | Critical | Scale or restart |

---

## 🔧 Optimization Checklist

### Before Launch
- [ ] All critical indexes created
- [ ] Connection pooling configured
- [ ] Valkey caching enabled
- [ ] Semantic cache tested
- [ ] Load test passed (10 concurrent users)

### Monthly Review  
- [ ] Review slow query logs
- [ ] Check cache hit rates
- [ ] Analyze LLM token usage
- [ ] Review scaling triggers
- [ ] Update performance targets if needed
