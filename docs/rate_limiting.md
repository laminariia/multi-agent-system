# ⚡ Rate Limiting & Throttling Strategy

**Version:** 1.0  
**Scope:** All external API calls, platform interactions, and internal processing

---

## 🎯 Rate Limiting Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          RATE LIMITING LAYERS                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Layer 1: EXTERNAL PLATFORMS                                                │
│  ├── Freelancer API: 100 req/hour                                           │
│  ├── Upwork GraphQL: 60 req/min (browser)                                   │
│  ├── FL.ru RSS: 10 req/hour                                                 │
│  └── Kwork: 30 pages/hour                                                   │
│                                                                             │
│  Layer 2: LLM PROVIDERS (REAL TIER-BASED LIMITS)                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Provider      │ Free/Tier0 │ Tier 1      │ Tier 2+     │ TPM       │   │
│  ├───────────────┼────────────┼─────────────┼─────────────┼───────────┤   │
│  │ Gemini Flash  │ 5-15 RPM   │ 150-300 RPM │ 2000+ RPM   │ 1M        │   │
│  │ Gemini Pro    │ 2 RPM      │ 60 RPM      │ 1000 RPM    │ 500K      │   │
│  │ Claude Opus   │ 50 RPM     │ 50-100 RPM  │ 300+ RPM    │ 40K       │   │
│  │ GPT 5.3 Codex │ 60 RPM     │ 500 RPM     │ 5000+ RPM   │ 150K      │   │
│  │ NanoBanana    │ via Gemini │ via Gemini  │ via Gemini  │ -         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ⚠️ Free tier = 5 RPM Gemini = только ~2 шага агента/минуту!               │
│  ⚠️ Требуется Tier 1+ ($5+) для реального параллелизма                     │
│                                                                             │
│  Layer 3: ENRICHMENT APIS                                                   │
│  ├── Hunter.io: 500 req/day                                                 │
│  ├── Apollo.io: 300 req/day                                                 │
│  └── Overpass API: 10,000 req/day                                           │
│                                                                             │
│  Layer 4: BUSINESS TARGETS (24/7)                                           │
│  ├── Bids/day: 150 │ Projects/day: 3-4 (100/month)                          │
│  └── Parallel: Phase 1: 5 concurrent, Phase 2: 10, Phase 3+: 15-20 (по типу проекта) │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

> **⚠️ ВАЖНО:** Без платного Tier 1+ в Gemini и Claude реальный параллелизм 
> невозможен. Free tier позволяет только 1-2 проекта одновременно.

---

## 🔧 Implementation

### Rate Limiter Core

```python
from redis import Redis
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

class RateLimitWindow(Enum):
    SECOND = 1
    MINUTE = 60
    HOUR = 3600
    DAY = 86400

@dataclass
class RateLimit:
    limit: int
    window: RateLimitWindow
    key_prefix: str

class RateLimiter:
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def check_and_increment(
        self, 
        limit_config: RateLimit,
        identifier: str = "default"
    ) -> tuple[bool, dict]:
        """
        Check if action is allowed and increment counter.
        
        Returns:
            (allowed: bool, info: {remaining, reset_at, retry_after})
        """
        key = f"ratelimit:{limit_config.key_prefix}:{identifier}"
        window_seconds = limit_config.window.value
        
        now = datetime.now()
        window_start = int(now.timestamp()) // window_seconds * window_seconds
        
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        current_count, _ = await pipe.execute()
        
        remaining = max(0, limit_config.limit - current_count)
        reset_at = datetime.fromtimestamp(window_start + window_seconds)
        
        allowed = current_count <= limit_config.limit
        
        return allowed, {
            "remaining": remaining,
            "limit": limit_config.limit,
            "reset_at": reset_at.isoformat(),
            "retry_after": (reset_at - now).seconds if not allowed else 0
        }
    
    async def wait_if_needed(
        self, 
        limit_config: RateLimit,
        identifier: str = "default"
    ) -> None:
        """Wait until rate limit resets if exceeded."""
        allowed, info = await self.check_and_increment(limit_config, identifier)
        
        if not allowed:
            retry_after = info["retry_after"]
            logger.warning(f"Rate limited. Waiting {retry_after}s for {limit_config.key_prefix}")
            await asyncio.sleep(retry_after)
            # Retry
            return await self.wait_if_needed(limit_config, identifier)
```

---

## 📋 Rate Limit Configuration

### Platform Limits

```python
PLATFORM_LIMITS = {
    "freelancer": {
        "api": RateLimit(100, RateLimitWindow.HOUR, "freelancer:api"),
        "search": RateLimit(30, RateLimitWindow.MINUTE, "freelancer:search"),
        "bid_submit": RateLimit(10, RateLimitWindow.HOUR, "freelancer:bid"),
    },
    "upwork": {
        "graphql": RateLimit(60, RateLimitWindow.MINUTE, "upwork:graphql"),
        "search": RateLimit(20, RateLimitWindow.MINUTE, "upwork:search"),
        "bid_submit": RateLimit(5, RateLimitWindow.HOUR, "upwork:bid"),
    },
    "fl_ru": {
        "rss": RateLimit(10, RateLimitWindow.HOUR, "fl_ru:rss"),
    },
    "kwork": {
        "scrape": RateLimit(30, RateLimitWindow.HOUR, "kwork:scrape"),
    }
}
```

### LLM Limits

```python
LLM_LIMITS = {
    # CONFIGURE BASED ON YOUR API TIER
    # Free/Tier0: 5-15 RPM Gemini, 50 RPM Claude
    # Tier 1 ($5-50/mo): 150-300 RPM Gemini, 100 RPM Claude  
    # Tier 2+ ($250+/mo): 2000+ RPM Gemini, 300+ RPM Claude
    
    "gemini_flash": {
        "tier_0": RateLimit(15, RateLimitWindow.MINUTE, "gemini:rpm"),  # Free
        "tier_1": RateLimit(300, RateLimitWindow.MINUTE, "gemini:rpm"), # Paid
        "tier_2": RateLimit(2000, RateLimitWindow.MINUTE, "gemini:rpm"), # Scale
        "tokens": RateLimit(1_000_000, RateLimitWindow.MINUTE, "gemini:tpm"),
    },
    "gemini_pro": {
        "tier_0": RateLimit(2, RateLimitWindow.MINUTE, "gemini_pro:rpm"),  # Free
        "tier_1": RateLimit(60, RateLimitWindow.MINUTE, "gemini_pro:rpm"), # Paid
        "tier_2": RateLimit(1000, RateLimitWindow.MINUTE, "gemini_pro:rpm"), # Scale
        "tokens": RateLimit(500_000, RateLimitWindow.MINUTE, "gemini_pro:tpm"),
    },
    "claude_opus": {
        "tier_0": RateLimit(50, RateLimitWindow.MINUTE, "claude:rpm"),
        "tier_1": RateLimit(100, RateLimitWindow.MINUTE, "claude:rpm"),
        "tier_2": RateLimit(300, RateLimitWindow.MINUTE, "claude:rpm"),
        "tokens": RateLimit(40_000, RateLimitWindow.MINUTE, "claude:tpm"),
    },
    "gpt_5_3_codex": {
        "tier_0": RateLimit(60, RateLimitWindow.MINUTE, "openai:rpm"),  # Free tier
        "tier_1": RateLimit(500, RateLimitWindow.MINUTE, "openai:rpm"), # Tier 1
        "tier_2": RateLimit(5000, RateLimitWindow.MINUTE, "openai:rpm"), # Tier 2+
        "tokens": RateLimit(150_000, RateLimitWindow.MINUTE, "openai:tpm"),
    },
    "nanobanana_pro": {
        # Uses Gemini API limits
        "images": RateLimit(30, RateLimitWindow.MINUTE, "nanobanana:images"),
    }
}

# Current tier (set based on API plan)
CURRENT_TIER = "tier_1"  # Change to tier_0/tier_2 as needed
```

### Business Logic Limits

```python
# MAS 2026 - 24/7 Operation Targets
# - Server runs continuously, most orders are simple (micro/small)
# - Minimum: 3 projects/day = 21/week = 90/month
# - Target: 100 projects/month = ~25/week = 3-4/day
# - Aggressive: 150+ projects/month

BUSINESS_LIMITS = {
    # Single tier for 24/7 automated operation
    "default": {
        "bids_per_day": RateLimit(150, RateLimitWindow.DAY, "business:bids"),
        "emails_per_hour": RateLimit(100, RateLimitWindow.HOUR, "business:emails"),
        "scans_per_hour": RateLimit(20, RateLimitWindow.HOUR, "business:geo_scans"),
    },
    
    # Targets (not limits, for tracking)
    "targets": {
        "min_projects_per_day": 3,           # Minimum acceptable
        "target_projects_per_day": 4,        # Goal
        "target_projects_per_month": 100,    # Monthly target
        "max_project_budget_usd": 5000,      # Can take any size
        "max_project_duration_days": 60,     # Up to 2 months
    }
}

class DynamicProjectLimits:
    """
    24/7 Parallel Project Execution
    
    HOW PARALLELISM WORKS:
    =====================
    Each project runs as an independent async task in the job queue.
    Agents are stateless and can work on multiple projects simultaneously.
    
    Example: 5 projects running in parallel
    ┌─────────────────────────────────────────────────────────────────┐
    │  Worker Pool (asyncio tasks)                                    │
    ├─────────────────────────────────────────────────────────────────┤
    │  [Project A - micro]  Scout → Content → Design → Critic ✓     │
    │  [Project B - small]  Scout ✓ → Content → Design (waiting)     │
    │  [Project C - micro]  Scout → Content → Design ✓ → Critic      │
    │  [Project D - medium] Scout ✓ → Content (running...)           │
    │  [Project E - micro]  Scout → Content ✓ → Design → Critic      │
    └─────────────────────────────────────────────────────────────────┘
    
    BOTTLENECKS:
    - LLM rate limits (60 RPM Gemini, 50 RPM Claude)
    - HITL approval (human speed)
    - Platform bid submission limits
    
    With 60 RPM and avg 3 calls per step:
    - Can process ~20 agent steps/minute
    - 5 parallel projects × 4 steps = 20 steps → fits in 1 minute
    """
    
    # Phase 1: 5 concurrent, Phase 2: 10, Phase 3+: 15-20 (по типу проекта)
    PARALLEL_LIMITS = {
        "micro": 10,    # Landing pages, fixes (~1 hour each)
        "small": 7,     # WordPress, components (~4 hours each)
        "medium": 5,    # Dashboards, simple apps (~12 hours each)
        "large": 3,     # Complex apps (~40 hours each)
    }
    
    TIME_ESTIMATES_HOURS = {
        "micro": 1,
        "small": 4,
        "medium": 12,
        "large": 40,
    }
    
    async def get_active_slots(self) -> dict:
        """Get current usage per complexity tier."""
        active = await self.get_active_projects()
        return {
            tier: sum(1 for p in active if p.complexity == tier)
            for tier in self.PARALLEL_LIMITS
        }
    
    async def can_accept_project(self, complexity: str) -> bool:
        """Check if we have capacity for a new project."""
        slots = await self.get_active_slots()
        current = slots.get(complexity, 0)
        limit = self.PARALLEL_LIMITS.get(complexity, 5)
        return current < limit
```

---

## 🚦 Queue Overflow Protection

### Job Queue Limits

```python
class JobQueueManager:
    MAX_PENDING_JOBS = 100
    MAX_PROCESSING_TIME = 300  # 5 minutes per job
    
    async def add_to_queue(self, job: Job) -> bool:
        current_count = await self.redis.llen("job_queue")
        
        if current_count >= self.MAX_PENDING_JOBS:
            logger.warning(f"Job queue full ({current_count}). Dropping oldest jobs.")
            # Remove oldest jobs
            await self.prune_old_jobs()
        
        await self.redis.rpush("job_queue", job.json())
        return True
    
    async def prune_old_jobs(self):
        """Remove jobs older than 1 hour from queue."""
        # Move to DLQ instead of deleting
        jobs = await self.redis.lrange("job_queue", 0, -1)
        for job_json in jobs:
            job = Job.parse_raw(job_json)
            if job.discovered_at < datetime.now() - timedelta(hours=1):
                await self.redis.lrem("job_queue", 1, job_json)
                await self.redis.rpush("job_queue:dlq", job_json)  # Dead letter queue
```

### Processing Throttle

```python
class ProcessingThrottle:
    """Prevent system overload during high-volume periods."""
    
    async def should_process_next(self) -> bool:
        # Check system health
        cpu_percent = psutil.cpu_percent()
        memory_percent = psutil.virtual_memory().percent
        
        if cpu_percent > 80:
            logger.warning(f"High CPU ({cpu_percent}%). Throttling.")
            await asyncio.sleep(5)
            return False
        
        if memory_percent > 85:
            logger.warning(f"High memory ({memory_percent}%). Throttling.")
            await asyncio.sleep(5)
            return False
        
        return True
```

---

## 💰 Cost-Based Limits

### LLM Budget Control

```python
@dataclass
class BudgetConfig:
    daily_limit_usd: float = 50.0
    monthly_limit_usd: float = 500.0
    alert_threshold: float = 0.8  # Alert at 80%

class LLMBudgetManager:
    COST_PER_1K_TOKENS = {
        "gemini_flash_input": 0.000075,
        "gemini_flash_output": 0.0003,
        "claude_opus_input": 0.015,
        "claude_opus_output": 0.075,
    }
    
    async def check_budget(self) -> tuple[bool, dict]:
        today_cost = await self.get_today_cost()
        month_cost = await self.get_month_cost()
        
        daily_remaining = self.config.daily_limit_usd - today_cost
        monthly_remaining = self.config.monthly_limit_usd - month_cost
        
        # Check for alerts
        if daily_remaining < self.config.daily_limit_usd * (1 - self.config.alert_threshold):
            await self.send_alert("Daily LLM budget at 80%")
        
        can_proceed = daily_remaining > 0 and monthly_remaining > 0
        
        return can_proceed, {
            "today_spent": today_cost,
            "daily_remaining": daily_remaining,
            "month_spent": month_cost,
            "monthly_remaining": monthly_remaining
        }
    
    async def record_usage(self, model: str, input_tokens: int, output_tokens: int):
        cost = (
            input_tokens / 1000 * self.COST_PER_1K_TOKENS.get(f"{model}_input", 0) +
            output_tokens / 1000 * self.COST_PER_1K_TOKENS.get(f"{model}_output", 0)
        )
        
        await self.redis.incrbyfloat("budget:today", cost)
        await self.redis.incrbyfloat("budget:month", cost)
        
        # Check if budget exceeded
        can_proceed, info = await self.check_budget()
        if not can_proceed:
            await self.create_hitl_alert(
                "LLM_BUDGET_EXCEEDED",
                f"Budget limit reached. Today: ${info['today_spent']:.2f}"
            )
            raise BudgetExceededError()
```

---

## 🔄 Retry Strategy

### Exponential Backoff

```python
class RetryConfig:
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0

async def with_retry(
    func: Callable,
    config: RetryConfig = RetryConfig(),
    retriable_exceptions: tuple = (RateLimitError, TimeoutError)
) -> Any:
    """Execute function with exponential backoff retry."""
    
    for attempt in range(config.max_retries):
        try:
            return await func()
        except retriable_exceptions as e:
            if attempt == config.max_retries - 1:
                raise
            
            delay = min(
                config.base_delay * (config.exponential_base ** attempt),
                config.max_delay
            )
            # Add jitter to prevent thundering herd
            delay += random.uniform(0, delay * 0.1)
            
            logger.warning(f"Retry {attempt + 1}/{config.max_retries} after {delay:.1f}s: {e}")
            await asyncio.sleep(delay)
```

---

## 📊 Monitoring & Alerts

### Rate Limit Dashboard Metrics

```python
RATE_LIMIT_METRICS = {
    # Prometheus metrics
    "rate_limit_hits_total": Counter("Requests that exceeded rate limit"),
    "rate_limit_remaining": Gauge("Remaining requests in current window"),
    "budget_spent_usd": Gauge("LLM budget spent"),
    "queue_depth": Gauge("Number of jobs in queue"),
}

async def collect_metrics():
    for platform, limits in PLATFORM_LIMITS.items():
        for limit_name, limit_config in limits.items():
            remaining = await rate_limiter.get_remaining(limit_config)
            RATE_LIMIT_METRICS["rate_limit_remaining"].labels(
                platform=platform, 
                limit=limit_name
            ).set(remaining)
```

### Alert Conditions

| Condition | Severity | Action |
|-----------|----------|--------|
| Rate limit hit 3x in 1 hour | Warning | Telegram notification |
| Daily budget > 80% | Warning | Telegram notification |
| Monthly budget > 90% | Error | HITL + pause optional agents |
| Queue depth > 80 jobs | Warning | Log + temporary throttle |
| Platform account rate limited | Error | HITL + use backup account |
| API usage > 70% of tier limit | Info | Consider tier upgrade |

---

## 🚀 Rate Limit Optimization Strategies

### 1. Request Batching

```python
class PromptBatcher:
    """
    Combine multiple prompts into single API call.
    Reduces RPM usage by 5-10x.
    """
    
    def __init__(self, max_batch_size: int = 10, max_wait_ms: int = 500):
        self.queue = asyncio.Queue()
        self.max_batch_size = max_batch_size
        self.max_wait_ms = max_wait_ms
    
    async def add_prompt(self, prompt: str, callback: Callable):
        """Add prompt to batch queue."""
        await self.queue.put((prompt, callback))
    
    async def process_batches(self):
        """Process batch when full or timeout."""
        while True:
            batch = []
            try:
                # Collect prompts up to max_batch_size or timeout
                while len(batch) < self.max_batch_size:
                    prompt, callback = await asyncio.wait_for(
                        self.queue.get(), 
                        timeout=self.max_wait_ms / 1000
                    )
                    batch.append((prompt, callback))
            except asyncio.TimeoutError:
                pass
            
            if batch:
                # Single API call with combined prompts
                combined_prompt = "\n---\n".join([p for p, _ in batch])
                response = await self.llm_call(combined_prompt)
                
                # Parse and distribute results
                results = response.split("---")
                for (_, callback), result in zip(batch, results):
                    await callback(result.strip())
```

### 2. Response Caching

```python
import hashlib
from redis import Redis

class ResponseCache:
    """
    Cache LLM responses by prompt hash.
    Reduces redundant API calls significantly.
    """
    
    def __init__(self, redis: Redis, ttl_hours: int = 24):
        self.redis = redis
        self.ttl = ttl_hours * 3600
    
    def _hash_prompt(self, prompt: str, model: str) -> str:
        content = f"{model}:{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    async def get_or_call(
        self, 
        prompt: str, 
        model: str, 
        llm_func: Callable
    ) -> str:
        """Return cached response or make API call."""
        cache_key = f"llm_cache:{self._hash_prompt(prompt, model)}"
        
        # Try cache first
        cached = await self.redis.get(cache_key)
        if cached:
            logger.info(f"Cache HIT for {model}")
            return cached.decode()
        
        # Make API call and cache
        response = await llm_func(prompt)
        await self.redis.setex(cache_key, self.ttl, response)
        return response
```

### 3. API Key Rotation

```python
class APIKeyPool:
    """
    Rotate between multiple API keys to increase effective limits.
    Each key gets its own rate limit quota.
    """
    
    def __init__(self):
        self.keys = {
            "gemini": [
                {"key": os.getenv("GEMINI_KEY_1"), "used": 0, "limit": 300},
                {"key": os.getenv("GEMINI_KEY_2"), "used": 0, "limit": 300},
            ],
            "claude": [
                {"key": os.getenv("CLAUDE_KEY_1"), "used": 0, "limit": 100},
            ]
        }
        self.lock = asyncio.Lock()
    
    async def get_available_key(self, provider: str) -> str:
        """Get key with lowest usage."""
        async with self.lock:
            keys = self.keys.get(provider, [])
            available = [k for k in keys if k["used"] < k["limit"]]
            
            if not available:
                # All keys exhausted, wait for reset
                raise RateLimitError(f"All {provider} keys exhausted")
            
            # Pick key with lowest usage
            best_key = min(available, key=lambda x: x["used"])
            best_key["used"] += 1
            return best_key["key"]
    
    async def reset_daily(self):
        """Reset counters (call via cron at midnight)."""
        async with self.lock:
            for provider in self.keys:
                for key in self.keys[provider]:
                    key["used"] = 0
```

### 4. Async Rate Limiter (aiolimiter)

```python
from aiolimiter import AsyncLimiter

class SmartRateLimiter:
    """
    Token bucket rate limiting with real-time monitoring.
    Uses aiolimiter for precise async control.
    """
    
    def __init__(self, tier: str = "tier_1"):
        # Configure based on tier
        limits = {
            "tier_0": {"gemini": 15, "claude": 50},
            "tier_1": {"gemini": 300, "claude": 100},
            "tier_2": {"gemini": 2000, "claude": 300},
        }
        
        self.gemini_limiter = AsyncLimiter(
            limits[tier]["gemini"], 60  # RPM
        )
        self.claude_limiter = AsyncLimiter(
            limits[tier]["claude"], 60
        )
        
        # Usage tracking
        self.usage = {"gemini": 0, "claude": 0}
    
    async def acquire(self, provider: str):
        """Wait for rate limit slot."""
        limiter = getattr(self, f"{provider}_limiter")
        
        async with limiter:
            self.usage[provider] += 1
            
            # Log warning at 90% capacity
            remaining = limiter._rate_per_sec * 60 - self.usage[provider]
            if remaining < limiter._rate_per_sec * 6:  # <10% remaining
                logger.warning(f"{provider} at 90%+ capacity")
            
            yield
```

---

## 📈 Real-Time Monitoring Setup

### Prometheus + Grafana Stack

```python
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# Metrics
api_requests = Counter(
    'api_requests_total', 
    'Total API requests',
    ['provider', 'status']
)
api_latency = Histogram(
    'api_latency_seconds',
    'API call latency',
    ['provider']
)
rate_limit_usage = Gauge(
    'rate_limit_usage_percent',
    'Current rate limit usage percentage',
    ['provider']
)
queue_depth = Gauge(
    'job_queue_depth',
    'Number of jobs in queue',
    ['complexity']
)

# Start metrics server
start_http_server(8000)  # Prometheus scrapes :8000/metrics
```

### Docker Compose for Monitoring

```yaml
# docker-compose.monitoring.yml
version: '3.8'
services:
  prometheus:
    image: prom/prometheus:v3.0.0
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
  
  grafana:
    image: grafana/grafana:11.0.0
    ports:
      - "3001:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-data:/var/lib/grafana

volumes:
  grafana-data:
```

### Alert Rules (prometheus.yml)

```yaml
groups:
  - name: rate_limits
    rules:
      - alert: HighAPIUsage
        expr: rate_limit_usage_percent > 70
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "API usage above 70%"
      
      - alert: RateLimitExhausted
        expr: rate_limit_usage_percent >= 95
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Rate limit nearly exhausted"
```
