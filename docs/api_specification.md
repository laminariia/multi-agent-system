# 🔌 API Specification — Multi-Agent System

**Version:** 1.0  
**Framework:** Litestar + Python 3.12  
**Real-time:** WebSocket (Socket.IO)

---

## 🏗️ API Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              API STRUCTURE                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  HTTP REST                           WebSocket                               │
│  ─────────                           ─────────                               │
│  /api/auth/*                         /ws/events                              │
│  /api/hitl/*                           ├── agent:heartbeat                   │
│  /api/agents/*                         ├── agent:log                         │
│  /api/projects/*                       ├── hitl:new                          │
│  /api/jobs/*                           ├── hitl:resolved                     │
│  /api/outreach/*                       ├── project:update                    │
│  /api/analytics/*                      └── notification                      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔐 Authentication

### POST `/api/auth/login`
Login with email/password.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secret123"
}
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "John",
    "role": "owner"
  }
}
```

### POST `/api/auth/refresh`
Refresh access token.

### POST `/api/auth/logout`
Invalidate tokens.

### GET `/api/auth/me`
Get current user info.

---

## ⏳ HITL Queue

### GET `/api/hitl/pending`
Get pending HITL requests.

**Query params:**
- `type` (optional): `bid_approval`, `code_review`, `delivery`, `revision`, `alert`
- `limit`: default 20
- `offset`: pagination

**Response:**
```json
{
  "items": [
    {
      "id": "uuid",
      "type": "bid_approval",
      "priority": "urgent",
      "title": "React Dashboard for Analytics",
      "expires_at": "2026-01-29T22:30:00Z",
      "payload": {
        "job_id": "uuid",
        "bid_id": "uuid",
        "proposal_text": "Hi! I've built 15+ similar...",
        "bid_amount": 2500,
        "client_info": {...},
        "job_url": "https://..."
      },
      "available_actions": ["approve", "edit", "skip", "later"],
      "created_at": "2026-01-29T21:00:00Z"
    }
  ],
  "total": 5,
  "pending_urgent": 2
}
```

### POST `/api/hitl/{id}/resolve`
Resolve a HITL request.

**Request:**
```json
{
  "action": "approve",  // or "reject", "edit", "skip", "later"
  "note": "Optional note",
  "edited_payload": {   // only for "edit" action
    "proposal_text": "Updated text...",
    "bid_amount": 2200
  }
}
```

**Response (200):**
```json
{
  "id": "uuid",
  "status": "resolved",
  "resolution": "approve",
  "next_action": "bid_will_be_submitted"
}
```

### GET `/api/hitl/stats`
HITL statistics.

**Response:**
```json
{
  "today": {"pending": 3, "resolved": 12, "expired": 1},
  "avg_resolution_time_minutes": 8.5,
  "by_type": {
    "bid_approval": {"pending": 2, "resolved": 8},
    "code_review": {"pending": 1, "resolved": 3},
    "delivery": {"pending": 0, "resolved": 1}
  }
}
```

---

## 🤖 Agents

### GET `/api/agents/status`
Get all agents status.

**Response:**
```json
{
  "system_health": "healthy",
  "last_check": "2026-01-29T21:50:00Z",
  "agents": [
    {
      "name": "scout",
      "display_name": "🔍 Scout Agent",
      "pipeline": "A",
      "status": "idle",
      "last_heartbeat": "2026-01-29T21:49:55Z",
      "current_task": null,
      "restart_count": 0
    },
    {
      "name": "bid",
      "display_name": "💼 Bid Agent",
      "pipeline": "A",
      "status": "working",
      "last_heartbeat": "2026-01-29T21:50:00Z",
      "current_task": "Drafting proposal for job #4521",
      "restart_count": 0
    }
  ]
}
```

### GET `/api/agents/{name}/logs`
Get agent logs.

**Query params:**
- `limit`: default 50
- `since`: ISO timestamp
- `level`: `info`, `warning`, `error`

**Response:**
```json
{
  "agent": "dev",
  "logs": [
    {
      "id": "uuid",
      "timestamp": "2026-01-29T21:45:12Z",
      "level": "info",
      "event_type": "llm_call",
      "message": "Generated 245 lines of React code",
      "details": {
        "model": "claude-opus-4.5",
        "tokens_input": 1520,
        "tokens_output": 890,
        "latency_ms": 3200
      }
    }
  ]
}
```

### POST `/api/agents/{name}/restart`
Force restart an agent.

### POST `/api/agents/{name}/pause`
Pause agent processing.

### POST `/api/agents/{name}/resume`
Resume agent processing.

---

## 📋 Projects

### GET `/api/projects`
List projects with Kanban data.

**Query params:**
- `type`: `freelance`, `digitalization`
- `status`: `planning`, `in_progress`, `review`, `delivered`, `completed`

**Response:**
```json
{
  "projects": [
    {
      "id": "uuid",
      "title": "React Dashboard",
      "type": "freelance",
      "status": "in_progress",
      "progress": 65,
      "deadline": "2026-02-15",
      "client_name": "TechStartup Inc.",
      "agreed_amount": 2500,
      "revision_count": 1,
      "kanban_column": "in_progress",
      "kanban_order": 1
    }
  ],
  "total": 8
}
```

### GET `/api/projects/{id}`
Get project details with tasks.

### PATCH `/api/projects/{id}`
Update project (status, kanban position).

**Request:**
```json
{
  "kanban_column": "review",
  "kanban_order": 2
}
```

### POST `/api/projects/{id}/add-to-queue`
Add digitalization project to processing queue.

---

## 🔍 Jobs

### GET `/api/jobs`
List discovered jobs.

**Query params:**
- `status`: `new`, `qualified`, `bid_sent`, `won`, `lost`
- `platform`: `freelancer`, `upwork`, `fl_ru`, `kwork`
- `min_score`: 0.0-1.0

### GET `/api/jobs/{id}`
Job details.

### POST `/api/jobs/{id}/disqualify`
Manually disqualify a job.

---

## 🗺️ Outreach (Pipeline B)

### POST `/api/outreach/scan`
Start a geo scan.

**Request:**
```json
{
  "city": "Москва",
  "radius_km": 10,
  "categories": ["restaurant", "cafe"],
  "require_no_website": true
}
```

**Response:**
```json
{
  "scan_id": "uuid",
  "status": "started",
  "estimated_hexagons": 847
}
```

### GET `/api/outreach/scans/{id}`
Get scan progress.

### GET `/api/outreach/leads`
List leads.

**Query params:**
- `city`: filter by city
- `status`: `new`, `enriched`, `contacted`, `responded`
- `has_email`: boolean

### POST `/api/outreach/campaigns`
Create email campaign.

**Request:**
```json
{
  "name": "Moscow Restaurants Q1",
  "subject_template": "{{business_name}}, нужен сайт?",
  "body_template": "Привет! Заметил, что у {{business_name}}...",
  "target_lead_ids": ["uuid1", "uuid2"],
  "schedule_at": "2026-02-01T09:00:00Z"
}
```

### GET `/api/outreach/campaigns/{id}`
Campaign details with stats.

### POST `/api/outreach/campaigns/{id}/pause`
Pause campaign.

### POST `/api/outreach/campaigns/{id}/resume`
Resume campaign.

---

## 📈 Analytics

### GET `/api/analytics/dashboard`
Main dashboard KPIs.

**Response:**
```json
{
  "period": "30d",
  "revenue": {
    "total": 12450,
    "change_percent": 18
  },
  "projects": {
    "active": 5,
    "completed": 12
  },
  "bids": {
    "sent": 156,
    "won": 38,
    "win_rate": 0.24
  },
  "agents": {
    "healthy": 9,
    "total": 10
  },
  "hitl": {
    "pending": 3,
    "avg_resolution_minutes": 8.5
  }
}
```

### GET `/api/analytics/funnel`
Bids funnel data.

**Response:**
```json
{
  "jobs_scanned": 486,
  "qualified": 287,
  "bids_sent": 156,
  "won": 38
}
```

### GET `/api/analytics/costs`
Cost breakdown.

**Response:**
```json
{
  "period": "30d",
  "total_usd": 585,
  "breakdown": {
    "llm_api": 340,
    "e2b_sandbox": 120,
    "enrichment": 45,
    "proxies": 80
  },
  "roi_percent": 2027
}
```

---

## 📡 WebSocket Events

### Connection
```javascript
const socket = io("wss://api.example.com/ws", {
  auth: { token: "Bearer eyJ..." }
});
```

### Events (Server → Client)

#### `agent:heartbeat`
```json
{
  "agent": "dev",
  "status": "working",
  "current_task": "Generating code",
  "timestamp": "2026-01-29T21:50:00Z"
}
```

#### `agent:log`
```json
{
  "agent": "scout",
  "level": "info",
  "message": "Found 3 qualified jobs",
  "timestamp": "2026-01-29T21:50:00Z"
}
```

#### `hitl:new`
```json
{
  "id": "uuid",
  "type": "bid_approval",
  "priority": "urgent",
  "title": "New bid ready for approval",
  "expires_at": "2026-01-29T22:30:00Z"
}
```

#### `hitl:resolved`
```json
{
  "id": "uuid",
  "resolution": "approve",
  "resolved_by": "user_uuid"
}
```

#### `project:update`
```json
{
  "project_id": "uuid",
  "field": "progress",
  "old_value": 60,
  "new_value": 65
}
```

#### `notification`
```json
{
  "type": "deadline_warning",
  "severity": "warning",
  "title": "Deadline approaching",
  "message": "Project #42 due in 24 hours",
  "project_id": "uuid"
}
```

### Events (Client → Server)

#### `subscribe:project`
```json
{ "project_id": "uuid" }
```

#### `subscribe:agent`
```json
{ "agent": "dev" }
```

---

## ⚠️ Error Responses

All errors follow this format:

```json
{
  "error": {
    "code": "HITL_EXPIRED",
    "message": "This HITL request has expired",
    "details": {
      "expired_at": "2026-01-29T21:00:00Z"
    }
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `UNAUTHORIZED` | 401 | Invalid or missing token |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `HITL_EXPIRED` | 410 | HITL request expired |
| `HITL_ALREADY_RESOLVED` | 409 | Already resolved |
| `AGENT_UNAVAILABLE` | 503 | Agent is not responding |
| `RATE_LIMITED` | 429 | Too many requests |

---

## 🔒 Rate Limits

| Endpoint Group | Limit |
|----------------|-------|
| `/api/auth/*` | 10/min |
| `/api/hitl/*` | 60/min |
| `/api/agents/*` | 30/min |
| `/api/outreach/scan` | 5/hour |
| WebSocket events | 100/sec |

---

## 📱 Telegram Bot API

### Webhook endpoint: `POST /api/telegram/webhook`

### Commands

| Command | Description |
|---------|-------------|
| `/status` | System health overview |
| `/pending` | List pending HITL items |
| `/stats` | Today's statistics |
| `/approve {id}` | Quick approve HITL |
| `/skip {id}` | Skip HITL item |

### Inline Keyboards

HITL notifications include inline buttons:
- ✅ Approve
- ❌ Skip  
- ⏸️ Later
- 🔗 Open Dashboard

---

## 🔄 API Versioning Strategy

### Version Format

```
/api/v{major}/resource
```

**Examples:**
- `/api/v1/hitl/pending` — Current stable
- `/api/v2/hitl/pending` — Next major version with breaking changes

### Versioning Rules

| Change Type | Version Impact | Example |
|-------------|---------------|---------|
| Bug fix | Patch (no URL change) | Fix typo in response |
| New optional field | Minor (no URL change) | Add `created_by` to response |
| New endpoint | Minor (no URL change) | Add `/api/v1/projects/archive` |
| Required field change | **Major** (new URL) | Change `proposal_text` to `content` |
| Response structure change | **Major** (new URL) | Rename `items` to `data` |
| Endpoint removal | **Major** (new URL) | Deprecate then remove |

### Deprecation Process

```yaml
# Timeline for deprecating v1 endpoint:
1. Announce v2 release              # Day 0
2. Add deprecation header to v1     # Day 0
3. Monitor v1 usage                 # 30 days
4. Send deprecation warnings        # Day 30
5. Redirect v1 → v2 (if possible)   # Day 60
6. Remove v1 entirely               # Day 90
```

### Deprecation Headers

When an endpoint is deprecated, responses include:

```http
HTTP/1.1 200 OK
X-API-Deprecated: true
X-API-Deprecation-Date: 2026-04-01
X-API-Replacement: /api/v2/hitl/pending
```

### Client Handling

```python
# Recommended client pattern
class APIClient:
    def __init__(self, base_url: str, version: str = "v1"):
        self.base_url = f"{base_url}/api/{version}"
    
    async def request(self, method: str, path: str, **kwargs) -> dict:
        response = await self._make_request(method, path, **kwargs)
        
        # Check for deprecation warning
        if response.headers.get("X-API-Deprecated"):
            logger.warning(
                f"API endpoint {path} is deprecated. "
                f"Use {response.headers.get('X-API-Replacement')} instead."
            )
        
        return response.json()
```

### Current Versions

| Version | Status | Support Until |
|---------|--------|---------------|
| v1 | **Stable** | Ongoing |
| v2 | Planning | TBD |

