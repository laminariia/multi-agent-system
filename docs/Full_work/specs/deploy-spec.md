# Deploy и CI/CD: Спецификация

## Railway

| Параметр | Значение |
|----------|----------|
| Сервисы | 2: API (Python) + Dashboard (Node/Remix) |
| API deploy | `railway up --detach` |
| Dashboard deploy | `railway up --detach --service dashboard --path-as-root dashboard` |
| Health check path | `/health` |
| Health check timeout | 300s |
| Restart policy | ON_FAILURE, max 3 retries |

**КРИТИЧНО:** `--path-as-root dashboard` обязателен для dashboard deploy. Без него Railway CLI использует корневой `railway.toml` (Python/API config) и dashboard собирается через Python nixpacks.

### railway.toml (API)
```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "sh -c 'for i in 1 2 3 4 5; do python -m alembic upgrade head && break || sleep 5; done && litestar --app src.api.main:app run --host 0.0.0.0 --port ${PORT:-8000}'"
healthcheckPath = "/health"
```

### Rate Limiting на Railway
Railway reverse proxy коллапсирует ВСЕ клиентские IP в один внутренний (`100.64.0.3`). IP-based rate limiting фактически глобальный. Текущий workaround: 300/min global, 60/min auth. Long-term fix: per-user rate limiting через JWT.

## Docker

### Dockerfile (API) -- Multi-stage build

**Stage 1 (builder):** `python:3.12-slim` + gcc, libpq-dev. Устанавливает Python-зависимости в `/install`.

**Stage 2 (runtime):** `python:3.12-slim` + libpq5, curl, wget. Non-root user `appuser` (UID 1000). Устанавливает Playwright Chromium.

```
COPY src/ -> /app/src/ (chmod 555)
COPY alembic.ini + alembic/ -> /app/alembic/ (chmod 755)
```

**CMD:** Alembic retry loop (5 попыток, sleep 5s) + litestar run.

**HEALTHCHECK:** `curl -sf http://localhost:${PORT}/health` каждые 30s.

### docker-compose.yml (dev)

| Сервис | Image | Порт |
|--------|-------|------|
| postgres | timescale/timescaledb-ha:pg16 | 5432 |
| valkey | valkey/valkey:8.1-alpine | 6379 |
| api | build from Dockerfile | 8000 |
| worker | build from Dockerfile, cmd: `python -m src.worker` | - |
| bot | build from Dockerfile, cmd: `python -m src.bot` | - |

### docker-compose.prod.yml (production)

Добавляет к dev:
- **dashboard:** отдельный image, port 3000
- **nginx:** 1.27-alpine, порты 80/443, TLS через certbot
- **prometheus:** v2.51.0, scrape metrics
- **grafana:** 10.4.1, порт 3001
- **valkey-exporter:** redis_exporter v1.58.0
- **postgres-exporter:** postgres-exporter v0.15.0
- **certbot:** для SSL (profile: ssl)

**Сети:** `frontend` (bridge, public) + `backend` (bridge, internal)

**Ресурсы (limits):**
- postgres: 1GB RAM, 1.0 CPU
- valkey: 512MB RAM, 0.5 CPU (maxmemory 256mb, allkeys-lru)
- api: 1GB RAM, 1.5 CPU
- worker: 1GB RAM, 1.5 CPU
- bot: 512MB RAM, 0.5 CPU
- dashboard: 512MB RAM, 0.5 CPU
- nginx: 256MB RAM, 0.25 CPU

**Security:** read_only: true, no-new-privileges для всех сервисов. Logging: json-file, max 10MB x 5 files.

### .dockerignore
Root `.dockerignore` исключает: .env, venv, tests, .git. НЕ исключает `dashboard/` (это ломает dashboard deploy). `dashboard/.dockerignore` отдельно исключает node_modules, .env.

## CI/CD -- GitHub Actions

### ci.yml (основной pipeline)

Триггеры: push (main, dev), pull_request (main).

| Stage | Job | Зависит от | Описание |
|-------|-----|-----------|----------|
| 1 | lint | - | ruff check + ruff format + TypeScript typecheck (dashboard) |
| 2a | test-unit | lint | pytest tests/unit/ -x --cov --cov-fail-under=70 |
| 2b | test-golden | lint | Golden set regression (LLM output tests), условный: `[golden]` в commit или PR |
| 2c | test-integration | lint | С PostgreSQL + Valkey services. Alembic migrate + alembic check |
| 2c | security | - (параллельно) | Semgrep (p/python, p/security-audit, p/owasp-top-ten) + Trivy FS scan + Gitleaks |
| 3 | test-e2e | unit + integration | Playwright. API + Dashboard запускаются, pytest tests/e2e/ |
| 4 | coverage | unit + integration | Codecov upload (только main push) |
| 5 | build | unit + integration + security | Docker build + push GHCR (API + Dashboard). Trivy image scan |
| 6 | deploy | build | Railway deploy (API + Dashboard) + health check + Telegram notify |

**Только main push:** stages 4-6.

### deploy.yml (manual)
`workflow_dispatch` с inputs: deploy_api (bool), deploy_dashboard (bool). Для ручного re-deploy без полного CI.

### pr-checks.yml
- Conventional commits в PR title (`feat|fix|docs|...`)
- Branch up-to-date с main
- Детекция больших файлов (>5MB)
- Поиск debug маркеров в diff (TODO_REMOVE, debugger, console.log, breakpoint, import pdb)

## Переменные окружения

| Переменная | Описание | Обязательная |
|------------|----------|:------------:|
| `DATABASE_URL` | PostgreSQL connection string | Да |
| `VALKEY_URL` | Valkey (Redis-compatible) connection | Да |
| `JWT_SECRET_KEY` | Секрет для JWT подписи (min 32 chars) | Да |
| `ENCRYPTION_KEY` | Fernet ключ для шифрования credentials | Да |
| `OPENROUTER_API_KEY` | OpenRouter API для всех LLM вызовов | Да |
| `OPENAI_API_KEY` | OpenAI для embeddings (text-embedding-3-large) | Нет |
| `ADMIN_EMAIL` | Email для auto-seed admin user | Нет |
| `ADMIN_PASSWORD` | Пароль для auto-seed admin | Нет |
| `SENTRY_DSN` | Sentry error tracking | Нет |
| `LANGSMITH_API_KEY` | LangSmith LLM tracing | Нет |
| `TELEGRAM_BOT_TOKEN` | Telegram bot для HITL уведомлений | Нет |
| `TELEGRAM_CHAT_ID` | Telegram chat для уведомлений | Нет |
| `TELEGRAM_API_ID` | Telethon MTProto (канал-мониторинг) | Нет |
| `TELEGRAM_API_HASH` | Telethon MTProto | Нет |
| `TELEGRAM_SESSION_STRING` | Telethon session | Нет |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | SMTP для email outreach | Нет |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin пароль | Нет |
| `VALKEY_PASSWORD` | Пароль Valkey (prod) | Нет |

## Health Checks

### /health endpoint
Проверяет DB (`SELECT 1`) + Valkey (`PING`).
- **healthy** -- оба connected
- **degraded** -- один из двух
- **unhealthy** -- оба down

Возвращает: `status`, `db_connected`, `valkey_connected`, `version`, `timestamp`.

### Heartbeat (агенты)
- Interval: 90s (каждый агент пингует)
- Timeout: 180s (после этого агент = dead)
- Max restarts: 3 (потом HITL alert)
- Monitor poll: 30s

## Мониторинг

### Prometheus
- **MASMetrics** singleton (`src/monitoring/metrics.py`)
- Scrape endpoint: `GET /metrics` (exclude_from_auth)
- **Gotcha:** между тестами нужно unregister collectors из `REGISTRY._names_to_collectors`, иначе `ValueError: Duplicated timeseries`

### Sentry
- Инициализация через `init_sentry(dsn, environment)` в lifespan
- Environment: "production" если `!DEBUG`, иначе "development"

### Structured Logging
- structlog для всего приложения
- JSON format в production

### Log Rotation

**Docker logging driver:** `json-file` с ограничениями:

```yaml
# docker-compose.prod.yml — для каждого сервиса
logging:
  driver: json-file
  options:
    max-size: "10m"    # Максимум 10MB на файл
    max-file: "5"      # Максимум 5 файлов (итого 50MB на сервис)
```

**Application-level rotation (structlog → файл):**

```python
# src/core/logging_config.py
import logging.handlers

file_handler = logging.handlers.RotatingFileHandler(
    filename="/var/log/mas/api.log",
    maxBytes=50_000_000,    # 50 MB
    backupCount=10,          # 10 файлов = 500 MB max
    encoding="utf-8",
)
```

**Loki retention:**

| Лог-тип | Retention | Хранилище |
|---------|-----------|-----------|
| API access | 30 дней | Loki |
| Agent activity | 90 дней | Loki |
| Error logs | 180 дней | Loki |
| Security/audit | 365 дней | Loki + S3 archive |
| Docker container | 50 MB/сервис | Local (json-file driver) |

**Cleanup cron (хост-уровень):**

```bash
# /etc/cron.daily/mas-log-cleanup
find /var/log/mas/ -name "*.log.*" -mtime +30 -delete
find /var/log/mas/ -name "*.log" -size +100M -exec truncate -s 0 {} \;
```

### Grafana (prod)
- Provisioned dashboards в `docker/monitoring/grafana/dashboards/`
- Datasource: Prometheus
- Port: 3001 (configurable via `GRAFANA_PORT`)

## Alembic на Railway

**Проблема:** PostgreSQL может быть не готов при старте контейнера (race condition).

**Решение:** retry loop в CMD:
```bash
for i in 1 2 3 4 5; do
  python -m alembic upgrade head && break || echo "Attempt $i failed" && sleep 5
done
```

В CI: `alembic check` после `alembic upgrade head` проверяет отсутствие pending миграций.

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `Dockerfile` | Multi-stage build для API |
| `dashboard/Dockerfile` | (не реализовано -- используется nixpacks на Railway) |
| `docker-compose.yml` | Dev environment (postgres, valkey, api, worker, bot) |
| `docker-compose.prod.yml` | Production (+ dashboard, nginx, prometheus, grafana, exporters) |
| `docker-compose.test.yml` | Test environment |
| `docker-compose.loadtest.yml` | Load testing environment |
| `railway.toml` | Railway API service config |
| `.github/workflows/ci.yml` | Основной CI/CD pipeline (6 stages) |
| `.github/workflows/deploy.yml` | Manual deploy workflow |
| `.github/workflows/pr-checks.yml` | PR validation (conventional commits, debug markers) |
| `.dockerignore` | Root: исключает .env, venv, tests |
| `dashboard/.dockerignore` | Dashboard: исключает node_modules |
| `.env.example` | Шаблон переменных окружения |
| `src/monitoring/sentry_config.py` | Sentry init |
| `src/monitoring/metrics.py` | MASMetrics singleton (Prometheus) |

## Deployment Procedures

### Quick Start (VPS / Docker Compose)

```bash
# 1. Clone + настройка
git clone https://github.com/yourorg/mas-freelance.git && cd mas-freelance
cp .env.example .env && nano .env  # заполнить секреты

# 2. Запуск
docker compose up -d

# 3. Миграции
docker compose exec api alembic upgrade head

# 4. Проверка
curl http://localhost:8000/health
```

### Production Checklist

**Pre-deployment:**
- Все API-ключи валидны и квоты достаточны
- БД забэкаплена
- SSL-сертификаты установлены (Let's Encrypt)
- Firewall: только 80, 443, 22
- Grafana alerts настроены
- Telegram bot работает

**Post-deployment:**
- `/health` возвращает 200
- Все агенты проходят heartbeat
- Тестовая генерация bid (staging)
- Prometheus scraping активен
- Log rotation работает
- Backup cron job подтверждён

### Security Audit (pre-deploy)
- Нет секретов в git history
- Rate limiting включён
- CORS настроен корректно
- SQL injection protection (параметризованные запросы)
- XSS protection headers
- HTTPS enforced (HTTP → redirect)

### Rollback Procedure

**Quick Rollback (<5 мин):**
```bash
docker compose down
git checkout HEAD~1
docker compose up -d
curl http://localhost:8000/health
```

**Database Rollback:**
```bash
docker compose stop api agent-orchestrator
pg_restore -U mas -d mas /backups/mas_$(date -d 'yesterday' +%Y%m%d).dump
docker compose exec api alembic downgrade -1
docker compose start api agent-orchestrator
```

### SSL Certificate Automation

Let's Encrypt + Certbot. Автообновление каждые 12ч (`certbot renew`). Nginx reverse proxy: HTTP→HTTPS redirect, security headers (`Strict-Transport-Security`, `X-Content-Type-Options nosniff`, `X-Frame-Options DENY`, `X-XSS-Protection`).

Первоначальная установка:
```bash
docker compose run --rm certbot certonly \
    --webroot -w /var/www/certbot \
    -d mas.example.com \
    --email admin@example.com --agree-tos --no-eff-email
```

### Scaling Guidelines

**Vertical (start here):**

| Нагрузка | Сервер | Стоимость |
|----------|--------|-----------|
| MVP (10 проектов/мес) | 2 vCPU, 4GB RAM | $20/мес |
| Growth (50 проектов/мес) | 4 vCPU, 8GB RAM | $40/мес |
| Scale (100+ проектов/мес) | 8 vCPU, 16GB RAM | $80/мес |

**Horizontal:** `docker-compose up -d --scale worker=3`. Workers тянут задачи из Valkey queue (`BRPOP`) — естественная балансировка нагрузки.

## CI/CD Pipeline (GitHub Actions)

### Архитектура pipeline

```
[Push/PR] → Lint → Unit Tests ──┐
                                 ├→ Build Docker → Deploy → Health Check → Telegram notify
             Security Scan ──────┘
```

### ci-cd.yml (основной workflow)

Триггеры: `push` (main, develop), `pull_request` (main).

| Job | Зависимости | Описание |
|-----|-------------|----------|
| lint | - | ruff check + black --check + mypy (ignore-missing-imports) |
| test | lint | pytest с PostgreSQL + Valkey services, coverage upload (Codecov) |
| security | lint | Semgrep (p/python, p/security-audit, p/owasp-top-ten) + Trivy FS scan (SARIF → GitHub CodeQL) |
| build | test + security | Docker build + push GHCR (main only). Buildx cache (GHA). Tags: SHA + `latest` |
| deploy | build | SSH → VPS (`appleboy/ssh-action`): git pull → docker-compose pull → up -d. Health check + Telegram notify |
| rollback | workflow_dispatch | Manual rollback через SSH на VPS |

### deploy.yml (manual)
`workflow_dispatch` с inputs: `deploy_api` (bool), `deploy_dashboard` (bool). Для ручного re-deploy без полного CI.

### pr-checks.yml
- Conventional commits в PR title (`feat|fix|docs|style|refactor|test|chore`)
- Branch up-to-date с main
- Детекция больших файлов (>5MB)
- Поиск debug маркеров в diff (`TODO_REMOVE`, `debugger`, `console.log`, `breakpoint`, `import pdb`)

### Required GitHub Secrets

| Secret | Назначение |
|--------|-----------|
| `VPS_HOST` | IP/домен сервера |
| `VPS_USER` | SSH username |
| `VPS_SSH_KEY` | SSH private key |
| `TELEGRAM_BOT_TOKEN` | Telegram notifications |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

## Backup & Disaster Recovery

### Цели восстановления

| Метрика | Значение |
|---------|----------|
| **RTO** (Recovery Time Objective) | < 1 час |
| **RPO** (Recovery Point Objective) | < 24 часа |

### Backup Strategy

**PostgreSQL:**
- Ежедневный `pg_dump` (cron 03:00) → gzip → S3/Backblaze B2
- Локально: 7 дней retention
- S3: 30 дней retention
- Glacier: 90 дней (disaster recovery)
- Верификация: `gunzip -t` + тестовый restore в temp DB + сравнение row counts
- Alert в Telegram при failure

**Valkey:**
- `appendonly yes`, `appendfsync everysec`
- RDB snapshots: каждый час (1 key), каждые 5 мин (100 keys), каждую минуту (10000 keys)
- Manual: `BGSAVE` + копирование `dump.rdb`

**Секреты:**
- Encrypted storage (1Password/Doppler)
- Не хранятся в git

### Secret Rotation Policy

| Тип | Частота | Метод |
|-----|---------|-------|
| JWT Secret Key | 90 дней | Manual (все сессии инвалидируются) |
| LLM API Keys | При компрометации | Manual |
| Database Password | 180 дней | Script (`ALTER USER` + restart) |
| OAuth Tokens | По expiry | Auto-refresh |
| Proxy Credentials | Ежемесячно | Manual |

### Disaster Recovery Scenarios

**Scenario 1: Server Failure** (~45 мин)
1. Spin up new VPS from image (5 мин)
2. Pull latest Docker images (5 мин)
3. Restore DB from S3 (15 мин)
4. Update DNS (5-30 мин)
5. Verify health (10 мин)

**Scenario 2: Database Corruption**
1. Stop writes → `docker compose stop api agent-orchestrator`
2. Попытка repair: `REINDEX DATABASE mas`
3. При серьёзной коррупции: restore from backup
4. `scripts/recalculate_project_states.py` для in-flight проектов

**Scenario 3: LLM API Outage**
Fallback chain: DeepSeek V3.2 → Claude Opus → Gemini Pro → GPT-4 → HITL queue.

**Scenario 4: Platform Mass-Ban**
1. СТОП вся автоматизация на платформе
2. НЕ создавать новые аккаунты (IP-linked)
3. Residential proxy rotation + увеличение human-like задержек
4. Ожидание 30-90 дней перед retry
5. Фокус на другие платформы

### Platform Account Failover

Multi-account setup (primary + backup) для Freelancer и Kwork. При бане:
1. Пауза всей активности на платформе
2. Telegram critical alert
3. Переключение на backup через HITL confirmation
4. Если backup нет — manual incident

### Recovery Testing Schedule

| Тест | Частота |
|------|---------|
| Database restore | Ежемесячно |
| Valkey restore | Ежеквартально |
| Full DR simulation | Ежегодно |
| Account failover | Раз в полгода |
| LLM failover | Ежемесячно |

## Observability Stack

```
Prometheus (metrics, 30d) → Grafana (dashboards + alerts) → Telegram Bot
Loki + Promtail (logs, 90-180d) → Grafana
LangSmith (LLM traces) → Grafana
Sentry (errors, 90d) ← Litestar integration
```

### Log Retention Policy

| Тип | Retention | Назначение |
|-----|-----------|------------|
| API access logs | 30 дней | Debugging, audit |
| Agent activity | 90 дней | Performance analysis |
| Error logs | 180 дней | Pattern detection |
| Security logs | 1 год | Compliance |

### Loki + Promtail

Docker-сервисы для log aggregation. Promtail scrapes: `/var/lib/docker/containers/*/*log` (все контейнеры) + `/var/log/mas/api.log` (API-специфичные).

## Performance Targets

| Метрика | Целевое значение |
|---------|-----------------|
| Scout throughput | 100 jobs/мин |
| Bid generation | <15с на proposal |
| Concurrent projects | Phase 1: 5, Phase 2+: до 15 |
| HITL response (Dashboard) | <5с (P95) |
| Database queries | <100мс (P95) |
| API latency | <200мс (P95) |

### Load Testing Targets

| Сценарий | Users | RPS Target | P95 Latency |
|----------|-------|------------|-------------|
| Normal | 10 | 50 | <200мс |
| Peak | 50 | 200 | <500мс |
| Stress | 100 | 300 | <1с |

### Bottleneck Analysis

1. **LLM API Rate Limits** (PRIMARY) — Semantic cache, batching, key rotation
2. **HITL Human Speed** (SECONDARY) — Batch approvals, async notifications
3. **Platform Rate Limits** — Caching, smart polling intervals
4. **Database I/O** (MINOR) — Connection pooling (min=5, max=20), proper indexes

### Caching Strategy

| Слой | Хранилище | TTL | Назначение |
|------|-----------|-----|------------|
| L1 | In-memory | 60с | Hot data (active workflows) |
| L2 | Valkey | 5мин-24ч | LLM responses, job listings |
| L3 | PostgreSQL | Permanent | Historical data, checkpoints |

Semantic cache: similarity threshold 0.92, TTL 24ч, max 10000 entries, expected hit rate ~15%.

### Horizontal Scaling Triggers

| Триггер | Порог | Действие |
|---------|-------|----------|
| CPU | >80% за 5 мин | Добавить worker replica |
| Memory | >85% | Добавить worker replica |
| Queue depth | >50 jobs | Добавить worker replica |
| LLM wait time | >30с avg | Добавить API keys |

### Performance Alerts

| Alert | Условие | Severity |
|-------|---------|----------|
| High latency | P95 > 1с за 5 мин | Warning |
| Queue backup | Depth > 50 за 10 мин | Warning |
| LLM throttled | Error rate > 10% | Error |
| DB slow | Query time > 500мс | Warning |
| OOM risk | Memory > 90% | Critical |

### Grafana Alert Rules

**Настройка:** Provisioned через `docker/monitoring/grafana/provisioning/alerting/rules.yml`

#### Critical Alerts (Telegram немедленно)

```yaml
groups:
  - name: mas-critical
    interval: 30s
    rules:
      - alert: AllAgentsDead
        expr: count(mas_agent_heartbeat_status{status="alive"}) == 0
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "All MAS agents are dead"

      - alert: DatabaseDown
        expr: pg_up == 0
        for: 1m
        labels:
          severity: critical

      - alert: ValkeyDown
        expr: redis_up == 0
        for: 1m
        labels:
          severity: critical

      - alert: OOMRisk
        expr: container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.90
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Container {{ $labels.name }} using >90% memory"
```

#### Warning Alerts (Dashboard + batch Telegram каждые 30 мин)

```yaml
      - alert: HighAPILatency
        expr: histogram_quantile(0.95, rate(mas_http_request_duration_seconds_bucket[5m])) > 1
        for: 5m
        labels:
          severity: warning

      - alert: LLMThrottled
        expr: rate(mas_llm_errors_total{error_type="rate_limit"}[5m]) / rate(mas_llm_requests_total[5m]) > 0.10
        for: 5m
        labels:
          severity: warning

      - alert: HITLQueueBacklog
        expr: mas_hitl_pending_count > 20
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "{{ $value }} HITL items pending for >30min"

      - alert: SlowDBQueries
        expr: histogram_quantile(0.95, rate(mas_db_query_duration_seconds_bucket[5m])) > 0.5
        for: 10m
        labels:
          severity: warning

      - alert: DiskSpaceLow
        expr: node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"} < 0.15
        for: 5m
        labels:
          severity: warning
```

#### Info Alerts (только Dashboard лог)

```yaml
      - alert: AgentRestarted
        expr: increase(mas_agent_restart_total[5m]) > 0
        labels:
          severity: info

      - alert: CacheHitRateLow
        expr: mas_cache_hit_rate < 0.05
        for: 1h
        labels:
          severity: info
```

#### Notification Channels

```yaml
# docker/monitoring/grafana/provisioning/alerting/contactpoints.yml
contactPoints:
  - name: telegram-critical
    type: telegram
    settings:
      bottoken: "${TELEGRAM_BOT_TOKEN}"
      chatid: "${TELEGRAM_CHAT_ID}"

  - name: dashboard-webhook
    type: webhook
    settings:
      url: "http://api:8000/api/v1/webhooks/grafana"

policies:
  - receiver: telegram-critical
    matchers:
      - severity = critical
    group_wait: 30s
    repeat_interval: 5m

  - receiver: dashboard-webhook
    matchers:
      - severity =~ "warning|info"
    group_wait: 5m
    repeat_interval: 30m
```
