# Load Testing

Load tests for the Multi-Agent Service API using [Locust](https://locust.io/).

## Quick Start

### Local (recommended for development)

```bash
# 1. Start backend services
docker compose up -d postgres valkey

# 2. Start the API
litestar --app src.api.main:app run --reload

# 3. Run Locust with web UI
locust -f tests/load/locustfile.py --host http://localhost:8000

# 4. Open http://localhost:8089 and configure:
#    Users: 50, Spawn rate: 5, Run time: 60s
```

### Headless (CI-friendly)

```bash
locust -f tests/load/locustfile.py --host http://localhost:8000 \
       --users 50 --spawn-rate 5 --run-time 60s \
       --headless --csv results/load
```

### Docker Compose (full stack)

```bash
# Start everything including Locust master + 2 workers
docker compose -f docker-compose.loadtest.yml up -d postgres valkey api
docker compose -f docker-compose.loadtest.yml up locust-master locust-worker-1 locust-worker-2

# Results will be in ./results/
```

## Validate Results

After a headless run with `--csv results/load`:

```bash
python tests/load/validate_results.py results/load
```

## User Classes

| Class | Weight | Wait Time | Description |
|-------|--------|-----------|-------------|
| DashboardUser | 3 | 2-5s | Sequential dashboard browsing flow |
| APIConsumer | 2 | 1-3s | Programmatic API calls (health, HITL, jobs) |
| WebSocketUser | 1 | 5-15s | Real-time update polling |

### DashboardUser Flow

1. Login (on start)
2. GET orchestrator status (dashboard overview)
3. GET jobs list
4. GET agents status
5. GET HITL pending queue
6. GET random job detail

### APIConsumer Endpoints

- `GET /api/v1/health` (weight 5)
- `GET /api/v1/orchestrator/status` (weight 3)
- `POST /api/v1/hitl/[id]/resolve` (weight 2)
- `GET /api/v1/pipeline-b/leads` (weight 2)
- `GET /api/v1/jobs?status=active` (weight 3)

## Performance Targets

| Metric | Threshold |
|--------|-----------|
| P50 response time | < 200ms |
| P95 response time | < 500ms |
| P99 response time | < 2000ms |
| Error rate | < 1% |
| 5xx errors | 0 |
| Minimum RPS | > 50 |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCUST_USERS` | 50 | Number of simulated users |
| `LOCUST_SPAWN_RATE` | 5 | Users spawned per second |
| `LOCUST_RUN_TIME` | 60s | Test duration |
| `LOADTEST_EMAIL` | loadtest@example.com | Test user email |
| `LOADTEST_PASSWORD` | loadtest-password-123 | Test user password |

## Files

| File | Purpose |
|------|---------|
| `locustfile.py` | Main load test definitions (3 user classes) |
| `conftest_load.py` | Shared config, credentials, auth helpers |
| `validate_results.py` | Post-run threshold validation |
| `README.md` | This file |
