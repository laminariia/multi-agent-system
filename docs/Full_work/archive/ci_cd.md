# 🔄 CI/CD Pipeline

**Version:** 1.0  
**Platform:** GitHub Actions  
**Target:** Docker + VPS deployment

---

## 📋 Overview

Continuous Integration and Deployment pipeline for the Multi-Agent System:
- Automated testing on every push
- Docker image building
- Deployment to production VPS
- Rollback capabilities

---

## 🏗️ Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CI/CD PIPELINE                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   [Push/PR]                                                              │
│       │                                                                  │
│       ▼                                                                  │
│   ┌──────────────────┐                                                   │
│   │ Lint & Format    │ ─── ruff, black, mypy                            │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌──────────────────┐                                                   │
│   │ Unit Tests       │ ─── pytest                                       │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌──────────────────┐                                                   │
│   │ Security Scan    │ ─── Semgrep, Trivy                               │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│            ▼ (main branch only)                                          │
│   ┌──────────────────┐                                                   │
│   │ Build Docker     │                                                   │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌──────────────────┐                                                   │
│   │ Deploy to VPS    │ ─── SSH + docker-compose                         │
│   └────────┬─────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌──────────────────┐                                                   │
│   │ Health Check     │                                                   │
│   └──────────────────┘                                                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Workflow Files

### Main CI/CD Workflow

```yaml
# .github/workflows/ci-cd.yml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  # ═══════════════════════════════════════════════════════════════
  # JOB 1: Lint and Format Check
  # ═══════════════════════════════════════════════════════════════
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      
      - name: Install dependencies
        run: |
          pip install ruff black mypy
          pip install -r requirements.txt
      
      - name: Ruff lint
        run: ruff check src/
      
      - name: Black format check
        run: black --check src/
      
      - name: MyPy type check
        run: mypy src/ --ignore-missing-imports

  # ═══════════════════════════════════════════════════════════════
  # JOB 2: Unit Tests
  # ═══════════════════════════════════════════════════════════════
  test:
    runs-on: ubuntu-latest
    needs: lint
    
    services:
      postgres:
        image: timescale/timescaledb-ha:pg16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_mas
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      
      valkey:
        image: valkey/valkey:8.1-alpine
        ports:
          - 6379:6379
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov
      
      - name: Run tests
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test_mas
          VALKEY_URL: valkey://localhost:6379
        run: |
          pytest tests/ -v --cov=src --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          file: coverage.xml

  # ═══════════════════════════════════════════════════════════════
  # JOB 3: Security Scan
  # ═══════════════════════════════════════════════════════════════
  security:
    runs-on: ubuntu-latest
    needs: lint
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Semgrep scan
        uses: returntocorp/semgrep-action@v1
        with:
          config: >-
            p/python
            p/security-audit
            p/owasp-top-ten
      
      - name: Trivy vulnerability scan
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          scan-ref: '.'
          format: 'sarif'
          output: 'trivy-results.sarif'
      
      - name: Upload Trivy results
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: 'trivy-results.sarif'

  # ═══════════════════════════════════════════════════════════════
  # JOB 4: Build Docker Image
  # ═══════════════════════════════════════════════════════════════
  build:
    runs-on: ubuntu-latest
    needs: [test, security]
    if: github.ref == 'refs/heads/main'
    
    outputs:
      image_tag: ${{ steps.meta.outputs.tags }}
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Login to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,prefix=
            type=raw,value=latest,enable={{is_default_branch}}
      
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # ═══════════════════════════════════════════════════════════════
  # JOB 5: Deploy to Production
  # ═══════════════════════════════════════════════════════════════
  deploy:
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/main'
    environment: production
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/mas
            
            # Pull latest code
            git pull origin main
            
            # Login to registry
            echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            
            # Pull new images
            docker-compose pull
            
            # Deploy with zero-downtime
            docker-compose up -d --remove-orphans
            
            # Cleanup old images
            docker image prune -f
      
      - name: Health check
        run: |
          sleep 30
          curl -f https://${{ secrets.VPS_HOST }}/health || exit 1
      
      - name: Notify on success via Telegram
        run: |
          curl -s -X POST "https://api.telegram.org/bot${{ secrets.TELEGRAM_BOT_TOKEN }}/sendMessage" \
            -d chat_id="${{ secrets.TELEGRAM_CHAT_ID }}" \
            -d parse_mode="Markdown" \
            -d text="✅ *MAS deployed* to production%0ACommit: \`${{ github.sha }}\`"

  # ═══════════════════════════════════════════════════════════════
  # JOB 6: Rollback (manual trigger)
  # ═══════════════════════════════════════════════════════════════
  rollback:
    runs-on: ubuntu-latest
    if: github.event_name == 'workflow_dispatch'
    environment: production
    
    steps:
      - name: Rollback to previous version
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/mas
            
            # Get previous image tag
            PREV_TAG=$(docker-compose config | grep image | head -1 | awk -F: '{print $(NF-1)}')
            
            # Rollback
            docker-compose down
            docker-compose -f docker-compose.rollback.yml up -d
            
            echo "Rolled back to previous version"
```

---

## 🔧 PR Checks Workflow

```yaml
# .github/workflows/pr-checks.yml
name: PR Checks

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  pr-validation:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      
      - name: Check PR title format
        run: |
          TITLE="${{ github.event.pull_request.title }}"
          if ! echo "$TITLE" | grep -qE "^(feat|fix|docs|style|refactor|test|chore)(\(.+\))?: .+"; then
            echo "❌ PR title must follow conventional commits format"
            exit 1
          fi
      
      - name: Check for merge conflicts
        run: |
          git fetch origin main
          if ! git merge-base --is-ancestor origin/main HEAD; then
            echo "❌ Branch is not up to date with main"
            exit 1
          fi
      
      - name: Detect large files
        run: |
          find . -type f -size +5M | grep -v ".git" && exit 1 || exit 0
```

---

## 🔐 Required Secrets

Configure these in GitHub repo Settings → Secrets:

| Secret | Purpose | Example |
|--------|---------|---------|
| `VPS_HOST` | Server IP/domain | `mas.example.com` |
| `VPS_USER` | SSH username | `deploy` |
| `VPS_SSH_KEY` | SSH private key | `-----BEGIN...` |
| `TELEGRAM_BOT_TOKEN` | Telegram notifications | `123456:ABC-DEF...` |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | `-1001234567890` |

---

## 📦 Dockerfile

```dockerfile
# Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/
COPY knowledge/ ./knowledge/
COPY prompts/ ./prompts/

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 🚀 Deployment Commands

### Manual Deployment

```bash
# On VPS
cd /opt/mas
git pull
docker-compose pull
docker-compose up -d
```

### Manual Rollback

```bash
# Rollback to specific commit
docker-compose down
git checkout <previous-commit>
docker-compose up -d
```

### View Logs

```bash
docker-compose logs -f api
docker-compose logs -f worker
```

---

## 📊 Monitoring Integration

### GitHub Actions Status Badge

```markdown
[![CI/CD](https://github.com/user/mas/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/user/mas/actions)
```

### Deployment History

All deployments are logged in GitHub Actions with:
- Commit SHA
- Timestamp
- Duration
- Status
