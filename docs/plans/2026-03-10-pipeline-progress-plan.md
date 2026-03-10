# P2.10 Pipeline Execution Progress

## Spec Reference
`dev-cycle-spec.md` line 563: "Dashboard pipeline progress (bar, текущий агент, задачи)"

## Architecture

### 1. Valkey Progress Store (`src/core/pipeline_progress.py`)
- Key pattern: `pipeline-progress:{thread_id}`
- Value: JSON with progress metadata
- TTL: 24 hours (auto-cleanup after pipeline ends)

```python
{
    "thread_id": "pipeline-abc123",
    "agent_sequence": ["dev", "content", "design"],
    "current_agent": "content",
    "current_index": 1,
    "total_agents": 5,  # sequence + critic + packager
    "completed_agents": ["planner", "dev"],
    "status": "running",  # running | paused | completed | failed
    "started_at": "2026-03-10T08:00:00Z",
    "updated_at": "2026-03-10T08:05:00Z"
}
```

### 2. ConstrainedAgent.invoke() Integration (`src/agents/base.py`)
- Before `_execute()`: call `publish_agent_started(valkey, thread_id, agent_name, state)`
- After success: call `publish_agent_completed(valkey, thread_id, agent_name, state)`
- On failure/HITL: call `publish_agent_status(valkey, thread_id, agent_name, "paused"|"failed")`
- Uses best-effort (try/except) — progress tracking never blocks agent execution

### 3. API Endpoint (`src/api/routes/jobs.py`)
- `GET /api/v1/jobs/{job_id}/pipeline-progress`
- Reads from Valkey by thread_id pattern `pipeline-{job_id}`
- Returns structured progress data
- Falls back to empty response when no progress exists

### 4. WebSocket Integration
- Uses existing `project:update` channel
- Progress updates published via ChannelsPlugin when available
- Dashboard invalidates `["pipeline-progress", jobId]` query on `project:update` events

### 5. Dashboard Component (`dashboard/app/components/execution-progress.tsx`)
- Progress bar: completed_agents / total_agents
- Current agent indicator with pulse animation
- Agent list: checkmark (done), spinner (current), circle (pending)
- Shows elapsed time since started_at

## Files Changed
- `src/core/pipeline_progress.py` (NEW) — progress read/write helpers
- `src/agents/base.py` (EDIT) — publish progress in invoke()
- `src/api/routes/jobs.py` (EDIT) — GET pipeline-progress endpoint
- `src/api/schemas.py` (EDIT) — PipelineProgressSchema
- `dashboard/app/components/execution-progress.tsx` (NEW)
- `dashboard/app/routes/_app.jobs.$id.tsx` (EDIT) — add ExecutionProgress component
- `dashboard/app/lib/api.ts` (EDIT) — fetchPipelineProgress
- `dashboard/app/lib/types.ts` (EDIT) — PipelineProgress type
- `tests/unit/test_pipeline_progress.py` (NEW)
