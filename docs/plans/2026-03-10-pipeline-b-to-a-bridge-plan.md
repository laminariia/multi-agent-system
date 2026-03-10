# P2.9 — Pipeline B → A Bridge

## Problem
Pipeline B produces Leads via geo-scanning and outreach. When a deal is won (client approves),
there's no way to automatically launch Pipeline A (development cycle) with the deal context.
The operator must manually create a job and trigger pipeline — losing agreed scope, design versions,
and conversation history.

## Solution

### 1. Deal Model (`src/core/models.py`, model #20)
New `deals` table linking Pipeline B leads to Pipeline A execution:
- `id` UUID PK
- `lead_id` FK → leads.id (nullable)
- `title` String(255)
- `status` String(20): new → negotiating → proposal_sent → won → lost/cancelled
- `agreed_scope` Text — becomes `requirements` for Pipeline A
- `budget` Numeric(12,2)
- `deadline` DateTime
- `client_context` JSONB — client metadata
- `design_versions` JSONB — approved designs (skip design agent if present)
- `conversation_history` JSONB
- `pipeline_a_thread_id` String(100) — back-reference to Pipeline A run
- `created_at`, `updated_at`

### 2. Alembic Migration
- Create `deals` table with indexes on `status` and `lead_id`

### 3. API Routes (`src/api/routes/deals.py`)
- `POST /api/v1/deals` — create deal from lead or manually
- `GET /api/v1/deals` — list deals (filter by status, search)
- `GET /api/v1/deals/{id}` — deal detail
- `PATCH /api/v1/deals/{id}` — update deal fields
- `POST /api/v1/deals/{id}/start-development` — **BRIDGE**: launch Pipeline A

### 4. Bridge Logic (`start-development`)
1. Validate deal exists, `status == 'won'`
2. Build `ProjectContext`:
   - `requirements = deal.agreed_scope`
   - `client = deal.client_context`
   - `budget = deal.budget`
   - `deadline = deal.deadline`
   - `platform = "outreach"` (Pipeline B source)
3. Call `run_project_pipeline()` as background task
4. Store `pipeline_a_thread_id` back on deal
5. Update deal status to `in_development`

### 5. Pydantic Schemas (`src/api/schemas.py`)
- `DealCreateSchema`, `DealUpdateSchema`

### Files changed
- `src/core/models.py` — Deal model
- `alembic/versions/20260310_...` — migration
- `src/api/routes/deals.py` — new routes
- `src/api/schemas.py` — request schemas
- `src/api/main.py` — register DealController
- `tests/unit/test_deals.py` — new test file
