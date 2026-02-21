# Technology Stack — Multi-Agent System v4.2

**Version:** 1.0  
**Updated:** February 2026  
**Status:** Authoritative Source

> [!IMPORTANT]
> This is the **single source of truth** for all technology choices.  
> If other documents conflict with this file, **this file is correct**.

---

## Core Stack Overview

| Layer | Technology | Version | Notes |
|-------|------------|---------|-------|
| **Language** | Python | 3.12+ | Type hints required |
| **Orchestration** | LangGraph | 1.0+ | StateGraph for multi-agent |
| **Backend API** | Litestar | 2.x | NOT FastAPI |
| **Database** | PostgreSQL | 16+ | With extensions below |
| **Vector Search** | pgvectorscale | latest | DiskANN indexes (11x faster than HNSW) |
| **Vector Types** | pgvector | 0.7+ | Required for pgvectorscale |
| **Embeddings** | OpenAI text-embedding-3-large | 3072 dim | Embedding model for vector search |
| **Cache/Queue** | Valkey | 8.1 | Redis-compatible fork |
| **Frontend** | Remix | 2.x | NOT Next.js |
| **UI Components** | shadcn/ui | latest | With Tailwind CSS |
| **Browser Automation** | Playwright | 1.40+ | With playwright-stealth |
| **Code Sandbox** | Docker | 24+ | Primary (E2B for quick tests only) |

---

## Detailed Technology Choices

### 1. Backend Framework: Litestar

**Why Litestar over FastAPI:**
- Native async support with better performance
- Built-in dependency injection
- OpenAPI 3.1 support
- Better WebSocket handling
- No Starlette dependency issues

```python
# Correct import
from litestar import Litestar, get, post
from litestar.channels import ChannelsPlugin

# NOT this (FastAPI)
# from fastapi import FastAPI  # WRONG
```

### 2. Cache & Queue: Valkey 8.1

**Why Valkey over Redis:**
- True open source (BSD-3)
- Drop-in Redis replacement
- 100% Redis protocol compatible
- Active community after Redis license change

```python
# Python client usage (redis-py works with Valkey)
import redis

# Connection URL format
valkey_url = "valkey://localhost:6379"
# OR (redis:// also works due to protocol compatibility)
valkey_url = "redis://localhost:6379"

client = redis.from_url(valkey_url)
```

> [!NOTE]
> Code samples throughout documentation may use `redis` variable names.
> This is intentional — Valkey is API-compatible with Redis.

### 3. Vector Search: pgvectorscale + pgvector

**Why pgvectorscale:**
- DiskANN indexes are 11x faster than HNSW at scale
- 99% recall at high QPS
- Integrated with PostgreSQL (no separate vector DB)
- Works with existing pgvector types

```sql
-- Required extensions
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector
CREATE EXTENSION IF NOT EXISTS vectorscale;   -- pgvectorscale

-- DiskANN index (faster than HNSW)
CREATE INDEX idx_embeddings ON knowledge_base 
    USING diskann (embedding vector_cosine_ops);

-- NOT HNSW (slower at scale)
-- CREATE INDEX idx_embeddings ON knowledge_base 
--     USING hnsw (embedding vector_cosine_ops);  -- AVOID
```

### 4. Frontend: Remix

**Why Remix over Next.js:**
- Server-first architecture
- Better data loading patterns
- Nested routing with loaders
- Progressive enhancement built-in
- No RSC complexity

```typescript
// Correct: Remix
import { json, LoaderFunction } from "@remix-run/node";
import { useLoaderData } from "@remix-run/react";

// NOT this (Next.js)
// import { GetServerSideProps } from "next";  // WRONG
```

### 5. Browser Automation: Playwright + Stealth

**Required packages:**
```bash
pip install playwright playwright-stealth
playwright install chromium
```

**Stealth configuration (REQUIRED for freelance platforms):**
```python
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def create_stealth_browser():
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=False,  # Headless is more detectable
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ]
    )
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        locale='en-US',
        timezone_id='America/New_York',
    )
    page = await context.new_page()
    await stealth_async(page)  # Apply stealth patches
    return page
```

### 6. Code Execution: Docker (Primary) + E2B (Quick Tests)

| Use Case | Tool | Time Limit | Notes |
|----------|------|------------|-------|
| Full project execution | Docker | Unlimited | Primary choice |
| Quick tests/validation | E2B | 5-10 min | Only for fast checks |
| Long-running tasks | Docker | Unlimited | E2B times out |

```python
# Decision logic
def choose_sandbox(estimated_time_minutes: int) -> str:
    if estimated_time_minutes > 5:
        return "docker"  # Always Docker for longer tasks
    return "e2b"  # E2B only for quick validation
```

---

## LLM Models — Canonical Agent Assignment

> [!IMPORTANT]
> This is the **single source of truth** for agent → LLM mapping.
> All other documents MUST match this table.

> **API Gateway:** All LLM calls go through **OpenRouter** (`OPENROUTER_API_KEY`).
> Exception: Embeddings use **OpenAI API** directly (`OPENAI_API_KEY`).

| Agent | Primary Model | OpenRouter ID | Fallback 1 | Fallback 2 | Cost Tier |
|-------|--------------|---------------|------------|------------|-----------|
| **Scout** | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Grok 4.1 Fast | Local Qwen3-4B | Low |
| **Bid** | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Claude Haiku 3.5 | — | Low |
| **Planner** | Claude Opus 4.6 | `anthropic/claude-opus-4.6` | DeepSeek R1 | Gemini 3 Pro | High |
| **Dev** (complex) | Claude Opus 4.6 | `anthropic/claude-opus-4.6` | DeepSeek V3.2 | Qwen3-Coder-Next | High |
| **Dev** (standard) | Claude Sonnet 4.5 | `anthropic/claude-sonnet-4.5` | DeepSeek V3.2 | Qwen3-Coder-Next | Medium |
| **Content** | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Gemini 3 Flash | — | Low |
| **Design** | NanoBanana Pro | `google/gemini-3-pro-image-preview` | Gemini 3 Flash | — | Medium |
| **Critic** | Claude Sonnet 4.5 | `anthropic/claude-sonnet-4.5` | DeepSeek R1 | GPT-5.2 Codex | High |
| **Packager** | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Grok 4.1 Fast | — | Low |
| **GeoScout** | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Grok 4.1 Fast | Local Qwen3-4B | Low |
| **Outreach** | DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Gemini 3 Flash | — | Low |

### Supplementary Models

| Task Type | Model | Provider | Notes |
|-----------|-------|----------|-------|
| Embeddings | OpenAI text-embedding-3-large (3072 dim) | OpenAI API direct | Vector search, semantic cache |
| Fast image drafts | Gemini 3 Flash | OpenRouter | Quick concept exploration |

### Dev Agent Routing Logic

Planner tags each task with complexity. Dev Agent selects model accordingly:
- `complexity: "complex"` → Claude Opus 4.6 (architecture, multi-file, new modules)
- `complexity: "standard"` → Claude Sonnet 4.5 (bug fixes, single-file, routine code)

### NanoBanana Pro

**NanoBanana Pro** = Gemini 3 Pro Image Generation (`google/gemini-3-pro-image-preview`).
Used by Design Agent for high-quality image generation: logos with text, infographics, marketing materials.
- 2K/4K output resolution, flexible aspect ratios
- Industry-leading text rendering in images
- Context: 65K tokens

### OpenRouter Model Reference

| Model | OpenRouter ID | $/1M in | $/1M out | Context |
|-------|---------------|---------|----------|---------|
| DeepSeek V3.2 | `deepseek/deepseek-v3.2` | $0.25 | $0.38 | 164K |
| DeepSeek R1 | `deepseek/deepseek-r1` | $0.70 | $2.50 | 64K |
| Claude Opus 4.6 | `anthropic/claude-opus-4.6` | $5.00 | $25.00 | 1M |
| Claude Sonnet 4.5 | `anthropic/claude-sonnet-4.5` | $3.00 | $15.00 | 1M |
| Claude Haiku 3.5 | `anthropic/claude-3.5-haiku` | $0.80 | $4.00 | 200K |
| NanoBanana Pro | `google/gemini-3-pro-image-preview` | $2.00 | $12.00 | 65K |
| Gemini 3 Flash | `google/gemini-3-flash-preview` | $0.50 | $3.00 | 1M |
| Gemini 3 Pro | `google/gemini-3-pro-preview` | $2.00 | $12.00 | 1M |
| GPT-5.2 | `openai/gpt-5.2` | $1.75 | $14.00 | 400K |
| GPT-5.2 Codex | `openai/gpt-5.2-codex` | $1.75 | $14.00 | 400K |
| Grok 4.1 Fast | `x-ai/grok-4.1-fast` | $0.20 | $0.50 | 2M |
| Qwen3-Coder-Next | `qwen/qwen3-coder-next` | $0.07 | $0.30 | 262K |

### Monthly Cost Estimate (~$391/mo at full 24/7 load)

| Agent | Model | $/mo |
|-------|-------|------|
| Scout | DeepSeek V3.2 | $14 |
| Bid | DeepSeek V3.2 | $6 |
| Planner | Claude Opus 4.6 | $41 |
| Dev (complex 30%) | Claude Opus 4.6 | $41 |
| Dev (standard 70%) | Claude Sonnet 4.5 | $57 |
| Content | DeepSeek V3.2 | $3 |
| Design | NanoBanana Pro | $30 |
| Critic | Claude Sonnet 4.5 | $176 |
| Packager | DeepSeek V3.2 | $1 |
| GeoScout | DeepSeek V3.2 | $6 |
| Outreach | DeepSeek V3.2 | $11 |
| Embeddings | text-embedding-3-large | $5 |
| **Total** | | **~$391** |

### Platform Status

| Platform | Status | Integration Method |
|----------|--------|-------------------|
| Freelancer.com | **Primary** | REST API |
| FL.ru | **Primary** | RSS Feed |
| Kwork | **Secondary** | Playwright scraper |
| Upwork | **Optional** | Playwright+Stealth monitoring OR manual search |

---

## Required PostgreSQL Extensions

```sql
-- Run on database initialization
CREATE EXTENSION IF NOT EXISTS vector;        -- Vector type support
CREATE EXTENSION IF NOT EXISTS vectorscale;   -- DiskANN indexes
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- UUID generation
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- Text search (optional)
```

---

## Environment Variables

> [!IMPORTANT]
> Canonical `.env.example` — all other documents MUST use these exact variable names.

```bash
# ==================== DATABASE ====================
DATABASE_URL=postgresql://user:pass@localhost:5432/mas
VALKEY_URL=valkey://localhost:6379

# ==================== LLM (OpenRouter — single gateway for all models) ====================
OPENROUTER_API_KEY=         # All LLM calls: https://openrouter.ai/keys
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# ==================== EMBEDDINGS (OpenAI direct) ====================
OPENAI_API_KEY=             # text-embedding-3-large (3072 dim)

# ==================== SANDBOX ====================
E2B_API_KEY=                # For quick tests only (<5 min)

# ==================== FREELANCE PLATFORMS ====================
FREELANCER_CLIENT_ID=
FREELANCER_CLIENT_SECRET=

# Upwork (OPTIONAL — Playwright monitoring or manual search)
# UPWORK_EMAIL=
# UPWORK_PASSWORD=

# ==================== ENRICHMENT ====================
HUNTER_API_KEY=
APOLLO_API_KEY=

# ==================== PROXY ====================
BRIGHTDATA_USERNAME=
BRIGHTDATA_PASSWORD=
BRIGHTDATA_HOST=brd.superproxy.io

# ==================== AUTH ====================
JWT_SECRET_KEY=
ENCRYPTION_KEY=             # Fernet key for credentials encryption

# ==================== NOTIFICATIONS ====================
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# ==================== MONITORING ====================
LANGSMITH_API_KEY=          # LLM tracing
SENTRY_DSN=                 # Error tracking
```

---

## Version Compatibility Matrix

| Component | Min Version | Recommended | Max Tested |
|-----------|-------------|-------------|------------|
| Python | 3.11 | 3.12 | 3.13 |
| PostgreSQL | 15 | 16 | 17 |
| Valkey | 8.0 | 8.1 | 8.x |
| LangGraph | 0.2 | 1.0+ | - |
| Litestar | 2.0 | 2.x | - |
| Remix | 2.0 | 2.x | - |

---

## Anti-Patterns (DO NOT USE)

| Wrong | Correct | Reason |
|-------|---------|--------|
| FastAPI | Litestar | Architecture decision |
| Redis | Valkey | Licensing, same API |
| Next.js | Remix | Architecture decision |
| NextAuth | Custom JWT | Simpler, no framework lock |
| HNSW index | DiskANN | Performance at scale |
| E2B for all | Docker primary | E2B has time limits |
| Selenium | Playwright | Better API, stealth support |

---

## Migration Notes

If you find code using deprecated patterns:

1. **FastAPI imports** → Replace with Litestar
2. **Redis URL** → Can keep `redis://` (Valkey is compatible)
3. **HNSW indexes** → Recreate with DiskANN
4. **Next.js components** → Rewrite for Remix

---

**Last reviewed:** February 2026  
**Maintainer:** MAS Architecture Team
