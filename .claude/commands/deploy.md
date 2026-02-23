# Deploy to Railway

Deploy the Multi-Agent Service with safety pre-flight checks.

## Pre-flight Checklist

### Step 1: Code Quality

```bash
ruff check src/
```

**BLOCK if ruff finds errors.** Fix them first or run `ruff check --fix src/`.

### Step 2: Unit Tests

```bash
pytest tests/unit/ -x -q --no-header
```

**BLOCK if any tests fail.** Report failures.

### Step 3: Security Check

Verify in `src/core/config.py`:
- `DEBUG` is not hardcoded to `True`
- `JWT_SECRET_KEY` has production validator
- `ENCRYPTION_KEY` has production validator
- No hardcoded API keys

```bash
ruff check src/ --select S --quiet
```

**BLOCK if security issues found.**

### Step 4: Git Status

```bash
git status --porcelain
git log --oneline -3
```

**WARN if uncommitted changes exist.** Ask user to commit first.

### Step 5: Docker Build Verification

```bash
docker compose -f docker-compose.prod.yml config --quiet 2>&1
```

**WARN if Docker config has issues.**

### Step 6: Confirm with User

Display summary:
```
Pre-flight Results:
  Ruff:     PASS/FAIL
  Tests:    PASS/FAIL (N passed)
  Security: PASS/FAIL
  Git:      clean / N uncommitted changes
  Docker:   PASS/WARN

Ready to deploy to Railway?
```

**Wait for explicit user confirmation before deploying.**

### Step 7: Deploy

Two services to deploy:
```bash
# API service (Python/Litestar)
railway up --detach

# Dashboard service (Node/Remix) — CRITICAL: use --path-as-root
railway up --detach --service dashboard --path-as-root dashboard
```

### Step 8: Post-deploy Verification

Wait 30 seconds, then check health:
```bash
railway logs --latest
```

Report status:
```
Deploy complete.
Rollback: railway rollback
Logs: railway logs
```

## Important Notes

- Dashboard deploy MUST use `--path-as-root dashboard` flag
- Without it, Railway uses root Python config instead of Node config
- Railway collapses all IPs to `100.64.0.3` — rate limits are effectively global
- Alembic retries 5x on startup (PostgreSQL race condition)
