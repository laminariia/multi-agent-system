# Infrastructure & Adapters Code Review

**Date:** 2026-02-23
**Reviewer:** Research Worker (Claude Opus 4.6)
**Scope:** Dockerfile, docker-compose (prod/dev), CI/CD, Alembic, all platform adapters, rate limiter, Railway deploy config

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH | 7 |
| MEDIUM | 8 |
| LOW | 5 |
| **Total** | **23** |

---

## CRITICAL Findings

### C1. FreelancerClient.submit_bid called without required `period` parameter

- **File:** `/Users/awon/programming/projects/MAS/src/core/graph.py` lines 115-118
- **Impact:** Every Freelancer bid submission will crash with `TypeError: submit_bid() missing 1 required positional argument: 'period'`. Pipeline A bid submission is completely broken for Freelancer platform.
- **Details:** The `submit_bid()` method at `/Users/awon/programming/projects/MAS/src/adapters/freelancer.py` line 236 requires `period: int` (delivery period in days), but the graph call passes only `project_id`, `description`, and `amount`:

```python
# graph.py line 115-118
result = await client.submit_bid(
    project_id=project_id,
    description=description,
    amount=amount,
    # period= is MISSING
)
```

```python
# freelancer.py line 236-242
async def submit_bid(
    self,
    project_id: int,
    description: str,
    amount: float,
    period: int,           # <-- required, no default
    milestone_percentage: int = 100,
) -> dict[str, Any]:
```

- **Fix:** Add `period=bid_data.get("period", 7)` to the `submit_bid()` call in `graph.py`. Ensure the bid agent includes `period` in its output artifacts.

### C2. FreelancerClient not closed after use -- HTTP connection leak

- **File:** `/Users/awon/programming/projects/MAS/src/core/graph.py` lines 105-119
- **Impact:** Every bid submission creates a new `httpx.AsyncClient` that is never closed. Over time this causes file descriptor exhaustion and eventual `OSError: [Errno 24] Too many open files`, crashing the process.
- **Details:** `FreelancerClient` is instantiated on line 105 and `submit_bid` is called, but `client.close()` is never called. There is no `async with` context manager pattern. The `finally` block at the bottom of the function does not close the client either.
- **Fix:** Wrap in try/finally with `await client.close()`, or better, implement `__aenter__`/`__aexit__` on `FreelancerClient` and use `async with`.

### C3. Valkey password defaults to "changeme" in production docker-compose

- **File:** `/Users/awon/programming/projects/MAS/docker-compose.prod.yml` lines 47, 53, 77, 111, 142, 283
- **Impact:** If `VALKEY_PASSWORD` env var is not set, production Valkey runs with "changeme" as password. Combined with the fact that `backend` network is internal, the risk is somewhat mitigated, but any container on the backend network can access all cached data (rate limiter state, sessions, semantic cache).
- **Details:** Default `${VALKEY_PASSWORD:-changeme}` appears 6 times across the prod compose file. Similarly, Grafana defaults to `admin` password at line 262.

```yaml
# line 47
--requirepass ${VALKEY_PASSWORD:-changeme}
# line 262
GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}
```

- **Fix:** Remove defaults entirely. Fail if `VALKEY_PASSWORD` is not set. Use a `.env.prod.template` that documents required vars. For Grafana, same approach -- no default password.

---

## HIGH Findings

### H1. Upwork `_SUBMIT_GUARD` flag is declared but never checked

- **File:** `/Users/awon/programming/projects/MAS/src/adapters/upwork.py` line 30
- **Impact:** The guard `_SUBMIT_GUARD = True` exists at module level but is never referenced in any function. There is no `submit_bid` method on `UpworkClient`, which is correct, but the guard serves no runtime purpose. If someone adds a `submit_bid` method in the future, the guard will not prevent it unless explicitly checked.
- **Details:** The real protection is the absence of a `submit_bid` method plus the docstring warning. The `_SUBMIT_GUARD` flag creates a false sense of security. A compile-time or test-time assertion would be more reliable.
- **Fix:** Add a test in the test suite that asserts `not hasattr(UpworkClient, 'submit_bid')`. Also consider a `__init_subclass__` check that raises if a subclass defines `submit_bid`. Remove the unused `_SUBMIT_GUARD` to avoid confusion.

### H2. XML parsing uses `xml.etree.ElementTree` -- vulnerable to XML bombs

- **File:** `/Users/awon/programming/projects/MAS/src/adapters/fl_ru.py` line 14, 271
- **Impact:** `ET.fromstring()` is vulnerable to entity expansion attacks (Billion Laughs / XML bombs). If FL.ru's RSS feed is compromised or a MITM attack occurs, the parser could consume unbounded memory and crash the process. The `# noqa: S314` comment acknowledges but dismisses this risk.
- **Details:** The FL.ru RSS feed is fetched over HTTPS, which provides some protection, but the "trusted source" comment is optimistic -- RSS feeds can be manipulated.

```python
root = ET.fromstring(response.text)  # noqa: S314 -- trusted source
```

- **Fix:** Replace with `defusedxml.ElementTree.fromstring()`:

```python
import defusedxml.ElementTree as SafeET
root = SafeET.fromstring(response.text)
```

Add `defusedxml` to project dependencies.

### H3. Docker socket mounted in worker container

- **File:** `/Users/awon/programming/projects/MAS/docker-compose.prod.yml` line 122
- **Impact:** Mounting `/var/run/docker.sock` gives the worker container full Docker API access. If the worker is compromised (e.g., via code execution in the Dev agent or a malicious LLM response), the attacker can create privileged containers, access the host filesystem, and escalate to root on the host machine. This is equivalent to root access on the host.
- **Details:**

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

Even with `:ro`, most Docker API operations (run container, exec, etc.) work through GET/POST requests, not file writes.

- **Fix:** If Docker-in-Docker is needed for the Dev agent sandbox, use a dedicated Docker API proxy like `docker-socket-proxy` (Tecnativa) that allows only specific API endpoints. Alternatively, use a remote Docker host with TLS authentication.

### H4. Production docker-compose API service lacks migration retry logic

- **File:** `/Users/awon/programming/projects/MAS/docker-compose.prod.yml` line 72-73
- **Impact:** Unlike the Dockerfile (line 64) and railway.toml (line 6) which both have 5-attempt retry loops, the prod compose API service runs `alembic upgrade head` without retries. If Postgres takes slightly longer than the healthcheck suggests, the migration fails and the container exits.

```yaml
# docker-compose.prod.yml line 72-73
command: >
  sh -c "alembic upgrade head && litestar --app src.api.main:app run..."
```

```dockerfile
# Dockerfile line 64 -- HAS retries
CMD ["sh", "-c", "for i in 1 2 3 4 5; do python -m alembic upgrade head && break ..."]
```

- **Fix:** Apply the same retry loop as in the Dockerfile CMD.

### H5. Playwright Chromium installed in production image adds ~400MB

- **File:** `/Users/awon/programming/projects/MAS/Dockerfile` line 51
- **Impact:** `playwright install --with-deps chromium` adds 400-600MB of Chromium binaries and system libraries to every production image, including the API service. Only the worker service actually needs browser capabilities for scraping.
- **Details:** The API container does not use Playwright. The bot container does not use Playwright. All three services (api, worker, bot) use the same Docker image but only worker needs the browser.
- **Fix:** Create two images: a slim API/bot image and a full worker image with Playwright. Use a multi-stage build or conditional install. Alternatively, use a build arg:

```dockerfile
ARG INSTALL_PLAYWRIGHT=false
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then playwright install --with-deps chromium; fi
```

### H6. FL.ru rate limiter state is per-instance, not distributed

- **File:** `/Users/awon/programming/projects/MAS/src/adapters/fl_ru.py` lines 72, 102-122
- **Impact:** `FlRuClient` maintains an in-memory `_request_timestamps` list for its internal 10-req/hour limit. If multiple worker instances exist (horizontal scaling), each has its own counter, effectively multiplying the rate by N workers. This could trigger FL.ru's server-side rate limiting or IP ban.
- **Details:** The `AdaptiveRateLimiter` (Valkey-backed) is also used alongside this internal limiter, but the internal limiter runs first (`_check_rate_limit()` at line 221 before `await self._rate_limiter.acquire()` at line 224). The duplication creates confusion and the internal one is non-distributed.
- **Fix:** Remove the internal rate limiter from `FlRuClient` entirely and rely solely on the `AdaptiveRateLimiter` which is Valkey-backed and distributed. Configure `_DEFAULT_LIMITS["fl_ru"]` to enforce the 10-req/hour limit.

### H7. Rate limiter `paused_until` uses `time.monotonic()` which is not portable across processes

- **File:** `/Users/awon/programming/projects/MAS/src/adapters/rate_limiter.py` line 263
- **Impact:** `time.monotonic()` is per-process and arbitrary. When `paused_until` is persisted to Valkey (line 133) and loaded by a different process (line 168), the monotonic timestamp from process A is meaningless in process B. Bans set by one worker will either never expire or expire immediately in another worker.

```python
state.paused_until = time.monotonic() + _BAN_PAUSE_SECONDS  # line 263
```

Then serialized:
```python
"paused_until": str(state.paused_until),  # line 133 -- monotonic value persisted
```

- **Fix:** Use `time.time()` (wall clock) for `paused_until` since it is shared across processes. Keep `time.monotonic()` for `last_request_ts` which is only used within a single process.

---

## MEDIUM Findings

### M1. Dev compose exposes Postgres on host port 5432 without password protection

- **File:** `/Users/awon/programming/projects/MAS/docker-compose.yml` lines 6-7, 10
- **Impact:** Dev Postgres is exposed on `0.0.0.0:5432` with password `mas_password`. On a shared network or if the developer machine has public-facing ports, anyone can connect.
- **Fix:** Bind to `127.0.0.1:5432:5432` instead of `5432:5432`.

### M2. Dev compose Valkey has no password

- **File:** `/Users/awon/programming/projects/MAS/docker-compose.yml` lines 21-28
- **Impact:** Dev Valkey runs without authentication and is exposed on `0.0.0.0:6379`. Any local process or network neighbor can read/write cached data.
- **Fix:** Add `--requirepass dev_password` to Valkey command, bind to `127.0.0.1`.

### M3. Alembic env.py does not support downgrade safety checks

- **File:** `/Users/awon/programming/projects/MAS/alembic/env.py`
- **Impact:** There are no guardrails against accidental `downgrade` in production. Running `alembic downgrade` could drop tables or columns with data loss. There is no pre-migration backup or confirmation mechanism.
- **Fix:** Add an environment check in `run_migrations_online()` that prevents downgrade in production:

```python
if os.getenv("ENVIRONMENT") == "production":
    # Only allow upgrade, not downgrade
    if context.get_x_argument(as_dictionary=True).get("cmd") == "downgrade":
        raise RuntimeError("Downgrade is blocked in production")
```

### M4. Grafana exposed on host port in production

- **File:** `/Users/awon/programming/projects/MAS/docker-compose.prod.yml` lines 264-265
- **Impact:** Grafana is exposed on `${GRAFANA_PORT:-3001}:3000` directly on the host. Combined with the default `admin` password (H3), this gives unauthenticated access to monitoring dashboards. Grafana should only be accessible through the nginx reverse proxy.
- **Fix:** Remove the `ports` mapping from Grafana. Access it only through nginx or an SSH tunnel. Add it to the `frontend` network if nginx proxying is desired.

### M5. Railway CLI not pinned to a specific version in CI

- **File:** `/Users/awon/programming/projects/MAS/.github/workflows/ci.yml` line 539
- **Impact:** `npm i -g @railway/cli` installs the latest version. A breaking change in Railway CLI could cause deploy failures with no obvious cause.

```yaml
run: npm i -g @railway/cli
```

- **Fix:** Pin to a specific version: `npm i -g @railway/cli@3.x.x`.

### M6. Ruff not pinned to a specific version in CI lint step

- **File:** `/Users/awon/programming/projects/MAS/.github/workflows/ci.yml` line 43
- **Impact:** `pip install "ruff>=0.8"` allows any ruff version >=0.8. A new ruff release with new rules could break CI unexpectedly.
- **Fix:** Pin: `pip install "ruff==0.9.x"` (whatever version is currently in use locally).

### M7. CI E2E test does not verify API server actually started

- **File:** `/Users/awon/programming/projects/MAS/.github/workflows/ci.yml` lines 370-374
- **Impact:** The health check loop waits up to 60s but does not `exit 1` if the API never responds. If all 30 retries fail, the step succeeds silently and E2E tests run against a dead server.

```yaml
for i in $(seq 1 30); do
  curl -sf http://localhost:8000/health && break || sleep 2
done
# No exit 1 on failure!
```

- **Fix:** Add `|| (echo "API failed to start" && exit 1)` after the loop.

### M8. Playwright Chromium installed as root, then user switched

- **File:** `/Users/awon/programming/projects/MAS/Dockerfile` lines 51, 61
- **Impact:** `playwright install --with-deps chromium` (line 51) runs as root before the `USER appuser` switch (line 61). The Chromium binary ends up owned by root. When the appuser tries to run Chromium, it may fail depending on file permissions, or it works but the browser profile directory could have permission issues at runtime.
- **Fix:** Install Playwright as the appuser, or ensure the installed files have appropriate read/execute permissions for appuser. The `chmod -R 555 /app/src` on line 54 doesn't cover Playwright's install location (`~/.cache/ms-playwright/` or `/ms-playwright/`).

---

## LOW Findings

### L1. Docker Healthcheck uses shell form for curl

- **File:** `/Users/awon/programming/projects/MAS/Dockerfile` line 57-58
- **Impact:** The healthcheck uses `CMD ["sh", "-c", "curl -sf ..."]`. If `curl` is not in the PATH or the `sh` environment is unexpected, the healthcheck fails. Minor issue since `curl` is explicitly installed on line 31.
- **Fix:** No action required, but could use `CMD curl -sf http://localhost:${PORT:-8000}/health` for simplicity.

### L2. `.dockerignore` excludes Dockerfile itself

- **File:** `/Users/awon/programming/projects/MAS/.dockerignore` lines 41-42
- **Impact:** Excluding `Dockerfile` from the build context is fine (it's not needed inside the image), but excluding `docker-compose*.yml` prevents multi-stage builds from referencing other compose files. This is cosmetic -- no practical impact.

### L3. Hardcoded RUB/USD conversion rates in adapters

- **Files:** `/Users/awon/programming/projects/MAS/src/adapters/fl_ru.py` lines 42-50, `/Users/awon/programming/projects/MAS/src/adapters/kwork.py` lines 43-52
- **Impact:** `_RUB_TO_USD = 0.011` and `_CURRENCY_TO_USD` are hardcoded. Exchange rates fluctuate. At current rates (Feb 2026), RUB/USD is closer to 0.010. The ~10% error could cause budget filtering to include/exclude borderline jobs incorrectly.
- **Fix:** Make conversion rates configurable via environment variable or fetch from a simple exchange rate API on startup.

### L4. CI golden set tests missing `timeout-minutes`

- **File:** `/Users/awon/programming/projects/MAS/.github/workflows/ci.yml` lines 133-163
- **Impact:** The `test-golden` job has no `timeout-minutes` set. If golden set tests hang (e.g., waiting for an LLM response that never comes), the job runs until GitHub's default 6-hour timeout. This wastes CI minutes.
- **Fix:** Add `timeout-minutes: 15` to the job definition.

### L5. Kwork adapter does not extract skills from job cards

- **File:** `/Users/awon/programming/projects/MAS/src/adapters/kwork.py` line 245
- **Impact:** The `skills_required` field is always an empty list `[]`. Kwork does display skill tags on job cards, but the adapter doesn't parse them. This reduces the quality of job matching in the Scout agent.
- **Fix:** Add a selector for skill tags and parse them similar to the Upwork adapter's skill extraction logic.

---

## Positive Observations

The following aspects are well-implemented:

1. **CI/CD actions are SHA-pinned** -- All GitHub Actions use full commit SHA pins (e.g., `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`), eliminating supply chain attack risk from tag mutation. This is excellent security practice.

2. **Multi-stage Docker build** -- The Dockerfile correctly separates build dependencies (gcc, libpq-dev) from the runtime image, keeping the final image smaller.

3. **Non-root user in Dockerfile** -- The `appuser` (UID 1000) is properly created and used. `read_only: true` and `no-new-privileges:true` are set on all prod compose services.

4. **Prod compose network segmentation** -- The `backend` network is marked `internal: true`, preventing direct external access to Postgres, Valkey, and monitoring services.

5. **Rate limiter architecture** -- The `AdaptiveRateLimiter` with graduated responses (success -> increase, 429 -> halve, ban -> 30min pause + alert) is well-designed. The retry decorator with non-retryable error classification is correct.

6. **Upwork adapter is genuinely read-only** -- No `submit_bid` method exists on `UpworkClient`. The docstring warnings are clear. The HITL pattern in the graph correctly routes through approval nodes.

7. **Error handling in adapters** -- All adapters properly detect and raise platform-specific exceptions (`CaptchaDetectedError`, `CloudflareBlockError`, `PlatformBannedError`, `PlatformRateLimitError`) enabling the retry decorator and rate limiter to respond appropriately.

8. **Logging** -- All adapters use structured logging with bound context (`platform=...`) and timing metrics (`latency_ms`). This enables effective observability.

9. **Alembic async configuration** -- The `env.py` correctly uses `async_engine_from_config` with `NullPool` (appropriate for migrations where connection pooling adds no value), and the `compare_type=True` flag ensures type changes are detected during autogenerate.

10. **Security scans in CI** -- Semgrep (SAST), Trivy (dependency + container scanning), and Gitleaks (secret detection) run in parallel with tests. Trivy also scans the built Docker image before deploy.
