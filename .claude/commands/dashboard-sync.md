# Dashboard Sync — Frontend Impact Checker

Every backend feature potentially requires a UI update. This command ensures nothing is missed.

## When to Run

- After implementing any backend feature
- After adding new API endpoints
- After changing models/DB schema
- Manually via `/dashboard-sync`

## Protocol

### Step 1: Detect Backend Changes

```bash
git diff main --name-only -- src/api/routes/
git diff main --name-only -- src/core/models.py
git diff main --name-only -- src/adapters/ src/agents/
```

Collect:
- New API endpoints (GET/POST/PATCH/DELETE)
- New/changed data models
- New configuration parameters
- WebSocket channel changes

### Step 2: Map Backend -> Frontend

| Backend Change | UI Impact | Priority |
|---------------|-----------|----------|
| New CRUD API (`/api/v1/{resource}`) | New page + sidebar link | HIGH |
| New GET endpoint (data) | Widget/card on dashboard | MEDIUM |
| New model with `active` field | Toggle in UI | MEDIUM |
| New WebSocket channel | Real-time widget | MEDIUM |
| New user-facing env vars | Section in Settings | LOW |
| New agent/pipeline change | Update agent cards | LOW |
| Internal-only changes (no API) | Nothing | NONE |

### Step 3: Audit Current Dashboard

Check what exists:
```
Dashboard routes:     dashboard/app/routes/
Dashboard components: dashboard/app/components/
Sidebar navigation:   dashboard/app/components/sidebar-nav.tsx
API client calls:     grep -r "fetch\|api/v1" dashboard/app/
```

For each new API endpoint verify:
- [ ] Route in dashboard that calls it?
- [ ] Navigation link in sidebar?
- [ ] Component for displaying data?

### Step 4: Generate Report

```markdown
## Dashboard Sync Report

### Backend Changes Detected
- [list of API endpoints and changes]

### Dashboard Impact Analysis

| Change | UI Needed | Exists? | Action |
|--------|----------|---------|--------|
| GET /api/v1/resource | Page | NO | CREATE route + component |

### Verdict: {SYNC_NEEDED | UP_TO_DATE}
```

### Step 5: Action

If **SYNC_NEEDED**: ask user whether to create UI (full page / minimal / skip).
If **UP_TO_DATE**: report "Dashboard in sync. No changes needed."

## Dashboard Architecture

```
dashboard/app/
├── routes/           # File-based routing (Remix)
│   ├── _app.tsx      # Main layout (sidebar + content)
│   ├── _app.dashboard.tsx, _app.jobs.tsx, _app.agents.tsx
│   ├── _app.hitl.tsx, _app.settings.tsx, _app.orchestrator.tsx
│   ├── _app.geo.tsx, _app.outreach.tsx, _app.users.tsx
│   └── _app.leads.$id.tsx
├── components/       # Reusable components (sidebar-nav.tsx, etc.)
└── lib/              # Utilities, API client
```

Sidebar navigation: `sidebar-nav.tsx` — every new page MUST be added there.
API base URL: via `VITE_API_URL` env var or relative `/api/v1/`.
