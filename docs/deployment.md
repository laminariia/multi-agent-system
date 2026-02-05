# 🚀 Deployment Guide

**Version:** 1.0  
**Target Environment:** Docker + VPS (Hetzner/DigitalOcean)

---

## 📋 Quick Start

```bash
# 1. Clone repository
git clone https://github.com/yourorg/mas-freelance.git
cd mas-freelance

# 2. Copy environment template
cp .env.example .env

# 3. Fill in secrets (see Environment Variables below)
nano .env

# 4. Start with Docker Compose
docker compose up -d

# 5. Run database migrations
docker compose exec api alembic upgrade head

# 6. Verify health
curl http://localhost:8000/health
```

---

## 🐳 Docker Compose Configuration

```yaml
# docker-compose.yml
version: '3.9'

services:
  # ==================== CORE SERVICES ====================
  api:
    build: 
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://mas:${DB_PASSWORD}@postgres:5432/mas
      - VALKEY_URL=valkey://valkey:6379/0  # Valkey 8.1 (Redis-compatible)
    depends_on:
      - postgres
      - valkey
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  agent-orchestrator:
    build:
      context: .
      dockerfile: Dockerfile.agents
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - CLAUDE_API_KEY=${CLAUDE_API_KEY}
      - DATABASE_URL=postgresql://mas:${DB_PASSWORD}@postgres:5432/mas
      - VALKEY_URL=valkey://valkey:6379/0
    depends_on:
      - api
      - valkey
    restart: unless-stopped

  # ==================== DATA LAYER ====================
  postgres:
    image: pgvector/pgvector:pg16  # Includes pgvector + pgvectorscale
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    environment:
      - POSTGRES_DB=mas
      - POSTGRES_USER=mas
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mas"]
      interval: 10s
      timeout: 5s
      retries: 5

  valkey:
    image: valkey/valkey:8.1-alpine  # 45% faster than Redis, open-source
    volumes:
      - valkey_data:/data
    command: valkey-server --appendonly yes
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ==================== BROWSER AUTOMATION ====================
  playwright:
    image: mcr.microsoft.com/playwright:v1.40.0-jammy
    shm_size: 2gb
    environment:
      - DISPLAY=:99
    volumes:
      - ./browser_sessions:/app/sessions
    depends_on:
      - valkey

  # ==================== CODE SANDBOX ====================
  sandbox:
    build:
      context: ./sandbox
      dockerfile: Dockerfile.sandbox
    privileged: false
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:size=512M
    mem_limit: 2g
    cpus: 2
    restart: unless-stopped

  # ==================== MONITORING ====================
  prometheus:
    image: prom/prometheus:v2.47.0
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.2.0
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/dashboards:/etc/grafana/provisioning/dashboards
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    restart: unless-stopped

volumes:
  postgres_data:
  valkey_data:
  prometheus_data:
  grafana_data:
```

---

## 🔐 Environment Variables

```bash
# .env.example

# ==================== CORE ====================
DATABASE_URL=postgresql://mas:password@localhost:5432/mas
VALKEY_URL=valkey://localhost:6379/0  # Redis-compatible protocol
SECRET_KEY=your-secret-key-min-32-chars

# ==================== LLM PROVIDERS ====================
GEMINI_API_KEY=AIza...
CLAUDE_API_KEY=sk-ant-...

# ==================== PLATFORMS ====================
# Freelancer.com
FREELANCER_OAUTH_CLIENT_ID=...
FREELANCER_OAUTH_CLIENT_SECRET=...
FREELANCER_ACCESS_TOKEN=...

# Upwork (GraphQL - monitoring only!)
UPWORK_CLIENT_ID=...
UPWORK_CLIENT_SECRET=...

# ==================== ENRICHMENT ====================
HUNTER_API_KEY=...
APOLLO_API_KEY=...

# ==================== PROXY ====================
BRIGHTDATA_USERNAME=...
BRIGHTDATA_PASSWORD=...
BRIGHTDATA_HOST=brd.superproxy.io
BRIGHTDATA_PORT=22225

# ==================== NOTIFICATIONS ====================
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TWILIO_ACCOUNT_SID=...  # For critical alerts
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=...

# ==================== EMAIL (Outreach) ====================
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
EMAIL_FROM=outreach@yourdomain.com

# ==================== MONITORING ====================
GRAFANA_PASSWORD=...
LANGSMITH_API_KEY=...  # LLM tracing
SENTRY_DSN=...         # Error tracking

# ==================== SECURITY ====================
E2B_API_KEY=...  # Only for quick tests < 5 min
```

---

## 🏭 Production Checklist

### Pre-Deployment

- [ ] All API keys are valid and have sufficient quotas
- [ ] Database has been backed up
- [ ] SSL certificates are installed (Let's Encrypt)
- [ ] Firewall rules configured (only 80, 443, 22)
- [ ] Monitoring alerts configured in Grafana
- [ ] Telegram bot is operational

### Post-Deployment

- [ ] Health endpoint returns 200
- [ ] All agents complete heartbeat
- [ ] Test bid generation (staging)
- [ ] Verify Prometheus scraping
- [ ] Check log rotation is working
- [ ] Confirm backup cron job

### Security Audit

- [ ] No secrets in git history
- [ ] Rate limiting enabled on API
- [ ] CORS properly configured
- [ ] SQL injection protection (parameterized queries)
- [ ] XSS protection headers
- [ ] HTTPS enforced (redirect HTTP)

---

## 🔄 Rollback Procedure

### Quick Rollback (< 5 min)

```bash
# 1. Stop current deployment
docker compose down

# 2. Pull previous version
git checkout HEAD~1

# 3. Restart with previous version
docker compose up -d

# 4. Verify health
curl http://localhost:8000/health
```

### Database Rollback

```bash
# 1. Stop API to prevent writes
docker compose stop api agent-orchestrator

# 2. Restore from backup
pg_restore -U mas -d mas /backups/mas_$(date -d 'yesterday' +%Y%m%d).dump

# 3. Downgrade migrations if needed
docker compose exec api alembic downgrade -1

# 4. Restart services
docker compose start api agent-orchestrator
```

---

## 📊 Scaling Guidelines

### Vertical Scaling (start here)

| Load Level | Server Spec | Est. Cost |
|------------|-------------|-----------|
| MVP (10 projects/month) | 2 vCPU, 4GB RAM | $20/mo |
| Growth (50 projects/month) | 4 vCPU, 8GB RAM | $40/mo |
| Scale (100+ projects/month) | 8 vCPU, 16GB RAM | $80/mo |

### Horizontal Scaling (advanced)

```yaml
# docker-compose.scale.yml
services:
  agent-orchestrator:
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '2'
          memory: 4G

  api:
    deploy:
      replicas: 2
```

---

## 🔍 Troubleshooting

### Agent Not Starting

```bash
# Check logs
docker compose logs agent-orchestrator

# Common issues:
# - Missing API keys
# - Redis connection failed
# - Rate limit exceeded
```

### Database Connection Issues

```bash
# Test connection
docker compose exec postgres psql -U mas -c "SELECT 1"

# Check connection pool
docker compose exec api python -c "from database import engine; print(engine.pool.status())"
```

### High Memory Usage

```bash
# Check container stats
docker stats

# Restart specific service
docker compose restart agent-orchestrator
```

---

## 📊 Log Aggregation

### Loki + Grafana Stack

```yaml
# Add to docker-compose.yml
  loki:
    image: grafana/loki:2.9.0
    ports:
      - "3100:3100"
    volumes:
      - loki_data:/loki
      - ./monitoring/loki-config.yml:/etc/loki/local-config.yaml
    command: -config.file=/etc/loki/local-config.yaml
    restart: unless-stopped

  promtail:
    image: grafana/promtail:2.9.0
    volumes:
      - /var/log:/var/log:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - ./monitoring/promtail-config.yml:/etc/promtail/config.yml
    command: -config.file=/etc/promtail/config.yml
    restart: unless-stopped

volumes:
  loki_data:
```

### Promtail Configuration

```yaml
# monitoring/promtail-config.yml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: containers
    static_configs:
      - targets:
          - localhost
        labels:
          job: containerlogs
          __path__: /var/lib/docker/containers/*/*log

  - job_name: mas-api
    static_configs:
      - targets:
          - localhost
        labels:
          job: mas-api
          __path__: /var/log/mas/api.log
```

### Log Retention Policy

| Log Type | Retention | Reason |
|----------|-----------|--------|
| API access logs | 30 days | Debugging, audit |
| Agent activity | 90 days | Performance analysis |
| Error logs | 180 days | Pattern detection |
| Security logs | 1 year | Compliance |

---

## 🔒 SSL Certificate Automation

### Let's Encrypt with Certbot

```yaml
# Add to docker-compose.yml
  certbot:
    image: certbot/certbot
    volumes:
      - ./certbot/conf:/etc/letsencrypt
      - ./certbot/www:/var/www/certbot
    entrypoint: /bin/sh -c "trap exit TERM; while :; do certbot renew; sleep 12h & wait $${!}; done;"

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certbot/conf:/etc/letsencrypt:ro
      - ./certbot/www:/var/www/certbot:ro
    depends_on:
      - api
    restart: unless-stopped
```

### Nginx Configuration

```nginx
# nginx/nginx.conf
events { worker_connections 1024; }

http {
    # Redirect HTTP to HTTPS
    server {
        listen 80;
        server_name mas.example.com;
        
        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }
        
        location / {
            return 301 https://$host$request_uri;
        }
    }

    # HTTPS server
    server {
        listen 443 ssl http2;
        server_name mas.example.com;

        ssl_certificate /etc/letsencrypt/live/mas.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/mas.example.com/privkey.pem;
        
        # Security headers
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options nosniff;
        add_header X-Frame-Options DENY;
        add_header X-XSS-Protection "1; mode=block";

        location / {
            proxy_pass http://api:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
        
        location /ws {
            proxy_pass http://api:8000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
    }
}
```

### Initial Certificate Setup

```bash
# First time only
docker compose run --rm certbot certonly \
    --webroot -w /var/www/certbot \
    -d mas.example.com \
    --email admin@example.com \
    --agree-tos \
    --no-eff-email

# Verify auto-renewal
docker compose exec certbot certbot renew --dry-run
```

---

## 🔑 Secret Rotation Policy

### Rotation Schedule

| Secret Type | Rotation Frequency | Method |
|-------------|-------------------|--------|
| JWT Secret Key | 90 days | Manual |
| LLM API Keys | On compromise | Manual |
| Database Password | 180 days | Script |
| OAuth Tokens | Per expiry | Auto-refresh |
| Proxy Credentials | Monthly | Manual |

### Rotation Procedure

#### 1. JWT Secret Key

```bash
# Generate new key
NEW_KEY=$(openssl rand -hex 32)

# Update .env
sed -i "s/SECRET_KEY=.*/SECRET_KEY=$NEW_KEY/" .env

# Restart API (active sessions will be invalidated)
docker compose restart api

# Note: Users will need to re-authenticate
```

#### 2. Database Password

```bash
# 1. Generate new password
NEW_DB_PASS=$(openssl rand -base64 24)

# 2. Update PostgreSQL
docker compose exec postgres psql -U mas -c \
    "ALTER USER mas WITH PASSWORD '$NEW_DB_PASS';"

# 3. Update .env
sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=$NEW_DB_PASS/" .env

# 4. Restart dependent services
docker compose restart api agent-orchestrator
```

#### 3. LLM API Keys

```bash
# 1. Generate new key in provider dashboard
# 2. Update .env with new key
# 3. Verify it works
docker compose exec api python -c "from llm import test_connection; test_connection()"

# 4. Revoke old key in provider dashboard
```

### Secret Storage Best Practices

```
✅ DO:
- Use environment variables
- Encrypt backups
- Limit access to .env file (chmod 600)
- Use separate keys for dev/staging/prod

❌ DON'T:
- Commit secrets to git
- Share keys via Slack/email
- Use same key across environments
- Store secrets in code comments
```

### Audit Checklist

```bash
# Check for exposed secrets
git log -p | grep -i "api_key\|secret\|password"

# Verify .env permissions
ls -la .env
# Should be: -rw------- (600)

# Check Docker secrets
docker compose config | grep -i key
# Should show ${VARIABLE} not actual values
```

