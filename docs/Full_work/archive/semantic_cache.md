# 🧠 Semantic Cache Architecture

**Version:** 1.0  
**Purpose:** Reduce LLM API costs by caching semantically similar queries

> [!NOTE]
> **Valkey Compatibility:** Code samples use `redis` Python library with `redis` variable names. This is intentional — **Valkey 8.1** is fully Redis-protocol compatible. The `redis-py` library works with Valkey without changes. Production runs on Valkey (see `deployment.md`).

---

## 📋 Overview

Semantic caching stores LLM responses and retrieves them for semantically similar (not just identical) queries. This reduces:
- API costs (up to 30-50% reduction)
- Response latency (cache hits are instant)
- Rate limit pressure

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SEMANTIC CACHE FLOW                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   User Query                                                             │
│       │                                                                  │
│       ▼                                                                  │
│   ┌──────────────────┐                                                   │
│   │ Embed Query      │                                                   │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌──────────────────┐     ┌─────────────────┐                           │
│   │ Vector Search    │────▶│ Similar found?  │                           │
│   │ (Valkey/Postgres) │     └────────┬────────┘                           │
│   └──────────────────┘              │                                    │
│                                     │                                    │
│              ┌──────────────────────┼──────────────────────┐             │
│              │                      │                      │             │
│              ▼ YES (>0.92)          ▼ NO                   │             │
│   ┌──────────────────┐   ┌──────────────────┐              │             │
│   │ Return Cached    │   │ Call LLM API     │              │             │
│   │ Response         │   └────────┬─────────┘              │             │
│   └──────────────────┘            │                        │             │
│                                   ▼                        │             │
│                        ┌──────────────────┐                │             │
│                        │ Cache Response   │                │             │
│                        │ + Embedding      │                │             │
│                        └──────────────────┘                │             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Configuration

```python
SEMANTIC_CACHE_CONFIG = {
    # Similarity threshold (0.0 - 1.0)
    # Higher = more strict (fewer false positives, less cache hits)
    # Lower = more lenient (more hits, risk of wrong responses)
    "similarity_threshold": 0.92,
    
    # TTL settings by query type
    "ttl": {
        "proposal_generation": 86400,      # 24 hours
        "code_generation": 3600,           # 1 hour (code changes often)
        "content_writing": 43200,          # 12 hours
        "translation": 604800,             # 7 days (stable)
        "default": 21600,                  # 6 hours
    },
    
    # Storage
    "storage": "redis",  # "redis" or "postgres"
    "redis_prefix": "sem_cache:",
    
    # Limits
    "max_cache_size_mb": 500,
    "max_entries": 10000,
}
```

---

## 🗄️ Storage Implementation

### Valkey Implementation (Recommended for Speed)

```python
import redis
import numpy as np
from redis.commands.search.field import VectorField, TextField, NumericField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType

class ValkeySemanticCache:
    def __init__(self, valkey_url: str, config: dict):
        self.redis = redis.from_url(valkey_url)  # redis-py is Valkey-compatible
        self.config = config
        self.embeddings = get_embeddings()
        self._create_index()
    
    def _create_index(self):
        """Create Valkey vector index for semantic search."""
        try:
            self.redis.ft("semantic_cache").create_index(
                fields=[
                    VectorField(
                        "embedding",
                        "HNSW",
                        {
                            "TYPE": "FLOAT32",
                            "DIM": 768,
                            "DISTANCE_METRIC": "COSINE"
                        }
                    ),
                    TextField("query"),
                    TextField("response"),
                    TextField("query_type"),
                    NumericField("timestamp"),
                ],
                definition=IndexDefinition(
                    prefix=["sem_cache:"],
                    index_type=IndexType.HASH
                )
            )
        except redis.ResponseError:
            # Index already exists
            pass
    
    async def get(self, query: str, query_type: str = "default") -> Optional[str]:
        """Get cached response for semantically similar query."""
        
        # Embed the query
        query_embedding = await self.embeddings.aembed_query(query)
        
        # Vector similarity search
        results = self.redis.ft("semantic_cache").search(
            f"*=>[KNN 1 @embedding $vec AS score]",
            query_params={
                "vec": np.array(query_embedding, dtype=np.float32).tobytes()
            }
        )
        
        if not results.docs:
            return None
        
        best_match = results.docs[0]
        similarity = 1 - float(best_match.score)  # Convert distance to similarity
        
        # Check threshold
        if similarity < self.config["similarity_threshold"]:
            return None
        
        # Check TTL
        ttl = self.config["ttl"].get(query_type, self.config["ttl"]["default"])
        if time.time() - float(best_match.timestamp) > ttl:
            # Expired, delete and return None
            self.redis.delete(best_match.id)
            return None
        
        return best_match.response
    
    async def set(
        self, 
        query: str, 
        response: str, 
        query_type: str = "default"
    ):
        """Cache a query-response pair."""
        
        query_embedding = await self.embeddings.aembed_query(query)
        
        key = f"sem_cache:{hashlib.sha256(query.encode()).hexdigest()[:16]}"
        
        self.redis.hset(key, mapping={
            "query": query,
            "response": response,
            "query_type": query_type,
            "embedding": np.array(query_embedding, dtype=np.float32).tobytes(),
            "timestamp": time.time(),
        })
        
        # Set TTL
        ttl = self.config["ttl"].get(query_type, self.config["ttl"]["default"])
        self.redis.expire(key, ttl)
```

### PostgreSQL Implementation (For Persistence)

```sql
CREATE TABLE semantic_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_hash TEXT UNIQUE,
    query TEXT NOT NULL,
    response TEXT NOT NULL,
    query_type TEXT DEFAULT 'default',
    embedding vector(768),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    hit_count INTEGER DEFAULT 0
);

CREATE INDEX idx_cache_embedding ON semantic_cache
USING diskann (embedding vector_cosine_ops);

CREATE INDEX idx_cache_expires ON semantic_cache(expires_at);
```

```python
class PostgresSemanticCache:
    async def get(self, query: str, query_type: str = "default") -> Optional[str]:
        query_embedding = await self.embeddings.aembed_query(query)
        
        row = await self.db.fetchrow("""
            SELECT response, 1 - (embedding <=> $1::vector) as similarity
            FROM semantic_cache
            WHERE expires_at > NOW()
            ORDER BY embedding <=> $1::vector
            LIMIT 1
        """, query_embedding)
        
        if row and row["similarity"] >= self.config["similarity_threshold"]:
            # Update hit count
            await self.db.execute(
                "UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = $1",
                row["id"]
            )
            return row["response"]
        
        return None
```

---

## 🔄 Cache Invalidation

### Strategies

| Strategy | When to Use | Implementation |
|----------|-------------|----------------|
| TTL-based | Default | Automatic expiry |
| Manual | Knowledge update | API endpoint |
| Pattern-based | Category changes | Query type filter |
| Full flush | Major updates | Valkey FLUSHDB |

### Invalidation API

```python
@app.delete("/api/cache/invalidate")
async def invalidate_cache(
    query_type: Optional[str] = None,
    older_than: Optional[datetime] = None
):
    """Invalidate cache entries."""
    
    if query_type:
        # Invalidate by type
        await cache.invalidate_by_type(query_type)
    elif older_than:
        # Invalidate old entries
        await cache.invalidate_before(older_than)
    else:
        # Full flush
        await cache.flush()
    
    return {"status": "invalidated"}
```

### Automatic Invalidation Triggers

```python
# When knowledge base is updated
@app.on_event("knowledge_updated")
async def on_knowledge_update(event):
    """Invalidate relevant cache when knowledge changes."""
    
    if event.source_type == "proposals":
        await cache.invalidate_by_type("proposal_generation")
    elif event.source_type == "technical":
        await cache.invalidate_by_type("code_generation")
```

---

## 📊 Metrics & Monitoring

### Key Metrics

```python
class CacheMetrics:
    def __init__(self, valkey: Redis):  # redis-py client, Valkey-compatible
        self.redis = valkey
    
    async def get_stats(self) -> dict:
        """Get cache statistics."""
        
        info = self.redis.info("stats")
        
        # Custom metrics
        total_entries = self.redis.dbsize()
        memory_used = self.redis.info("memory")["used_memory"]
        
        return {
            "total_entries": total_entries,
            "memory_used_mb": memory_used / 1024 / 1024,
            "hit_rate": self._calculate_hit_rate(),
            "avg_similarity": self._get_avg_similarity(),
            "entries_by_type": self._count_by_type(),
        }
    
    def _calculate_hit_rate(self) -> float:
        hits = int(self.redis.get("cache:hits") or 0)
        misses = int(self.redis.get("cache:misses") or 0)
        total = hits + misses
        return hits / total if total > 0 else 0
```

### Dashboard Queries

```sql
-- Cache efficiency
SELECT 
    query_type,
    COUNT(*) as total,
    SUM(hit_count) as total_hits,
    AVG(hit_count) as avg_hits
FROM semantic_cache
WHERE expires_at > NOW()
GROUP BY query_type;

-- Expiring soon
SELECT COUNT(*) 
FROM semantic_cache 
WHERE expires_at BETWEEN NOW() AND NOW() + INTERVAL '1 hour';
```

---

## 🔧 LangChain Integration

```python
from langchain.cache import BaseCache
from langchain.schema import Generation

class SemanticLLMCache(BaseCache):
    """LangChain-compatible semantic cache."""
    
    def __init__(self, cache: ValkeySemanticCache):
        self.cache = cache
    
    async def alookup(self, prompt: str, llm_string: str) -> Optional[list[Generation]]:
        """Look up cached response."""
        
        cached = await self.cache.get(prompt)
        if cached:
            return [Generation(text=cached)]
        return None
    
    async def aupdate(self, prompt: str, llm_string: str, return_val: list[Generation]):
        """Cache a response."""
        
        if return_val:
            await self.cache.set(
                query=prompt,
                response=return_val[0].text
            )

# Usage with LangChain
from langchain_google_genai import ChatGoogleGenerativeAI

cache = SemanticLLMCache(ValkeySemanticCache(valkey_url, config))
llm = ChatGoogleGenerativeAI(
    model="gemini-3-flash",
    cache=cache
)
```

---

## ⚠️ Edge Cases

### False Positives Prevention

```python
# Additional validation for high-stakes queries
async def get_with_validation(self, query: str, query_type: str) -> Optional[str]:
    cached = await self.get(query, query_type)
    
    if cached and query_type in ["code_generation", "proposal_generation"]:
        # Double-check similarity is very high
        similarity = await self._calculate_exact_similarity(query, cached)
        if similarity < 0.98:  # Stricter threshold
            return None
    
    return cached
```

### Never Cache

```python
# Queries that should never be cached
NEVER_CACHE = [
    "real_time_data",      # Requires fresh data
    "random_generation",   # Results should vary
    "personalized",        # User-specific
]

def should_cache(query_type: str) -> bool:
    return query_type not in NEVER_CACHE
```

---

## 📈 Expected Impact

| Metric | Without Cache | With Cache | Improvement |
|--------|---------------|------------|-------------|
| API costs | $500/month | $300/month | -40% |
| Avg latency | 2-3 sec | 50 ms (hits) | -95% |
| Rate limit headroom | 60% used | 35% used | +25% |
