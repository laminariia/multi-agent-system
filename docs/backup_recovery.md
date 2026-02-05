# 🔄 Backup & Recovery Plan

**Version:** 1.0  
**Recovery Time Objective (RTO):** < 1 hour  
**Recovery Point Objective (RPO):** < 24 hours

---

## 📊 Backup Strategy Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        BACKUP ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────┐    ┌────────────────┐    ┌────────────────┐         │
│  │   PostgreSQL   │    │     Redis      │    │    Secrets     │         │
│  │   Database     │    │   Sessions     │    │  (Vault/ENV)   │         │
│  └───────┬────────┘    └───────┬────────┘    └───────┬────────┘         │
│          │                     │                     │                   │
│          ▼                     ▼                     ▼                   │
│  ┌────────────────┐    ┌────────────────┐    ┌────────────────┐         │
│  │  Daily Dump    │    │  RDB Snapshot  │    │  Encrypted     │         │
│  │  pg_dump       │    │  Every 1 hour  │    │  1Password/    │         │
│  └───────┬────────┘    └───────┬────────┘    │  Doppler       │         │
│          │                     │             └────────────────┘         │
│          ▼                     ▼                                         │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │              BACKUP STORAGE (Encrypted)                      │        │
│  │                                                              │        │
│  │  Primary: Local /backups (7 days)                            │        │
│  │  Secondary: S3/Backblaze B2 (30 days)                        │        │
│  │  Archive: Glacier (90 days, disaster recovery)               │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🗄️ Database Backup

### Automated Daily Backup

```bash
#!/bin/bash
# /scripts/backup_database.sh
# Run via cron: 0 3 * * * /scripts/backup_database.sh

set -e

BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/mas_$DATE.sql.gz"
S3_BUCKET="s3://your-backup-bucket/postgres"

# Create backup
docker compose exec -T postgres pg_dump -U mas mas | gzip > $BACKUP_FILE

# Upload to S3
aws s3 cp $BACKUP_FILE $S3_BUCKET/

# Keep only 7 days locally
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

# Verify backup
gunzip -t $BACKUP_FILE && echo "Backup verified: $BACKUP_FILE"

# Alert on failure
if [ $? -ne 0 ]; then
    curl -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" \
         -d "chat_id=$TELEGRAM_CHAT_ID" \
         -d "text=🚨 Database backup FAILED at $DATE"
fi
```

### Manual Backup

```bash
# Quick backup before risky operations
docker compose exec postgres pg_dump -U mas mas > backup_$(date +%Y%m%d).sql

# With compression
docker compose exec postgres pg_dump -U mas mas | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore Database

```bash
# 1. Stop services that write to DB
docker compose stop api agent-orchestrator

# 2. Restore from backup
gunzip -c backup_20260130.sql.gz | docker compose exec -T postgres psql -U mas mas

# 3. Restart services
docker compose start api agent-orchestrator

# 4. Verify data integrity
docker compose exec api python scripts/verify_db_integrity.py
```

---

## 💾 Redis Session Backup

### Configuration

```bash
# redis.conf
appendonly yes
appendfsync everysec
save 3600 1      # Snapshot every hour if 1 key changed
save 300 100     # Snapshot every 5 min if 100 keys changed
save 60 10000    # Snapshot every min if 10000 keys changed
```

### Manual Backup

```bash
# Trigger RDB snapshot
docker compose exec redis redis-cli BGSAVE

# Copy snapshot
docker cp mas_redis_1:/data/dump.rdb ./backups/redis/
```

### Restore Redis

```bash
# 1. Stop Redis
docker compose stop redis

# 2. Copy backup
docker cp dump.rdb mas_redis_1:/data/

# 3. Restart
docker compose start redis
```

---

## 🔐 Platform Account Backup Strategy

### Multi-Account Setup (Recommended)

```yaml
# Platform account configuration
accounts:
  freelancer:
    primary:
      username: "main_freelancer"
      email: "main@yourdomain.com"
      status: active
    backup:
      username: "backup_freelancer"
      email: "backup@yourdomain.com"
      status: standby
      
  upwork:
    # ⚠️ Upwork has strict ToS - single account only!
    primary:
      username: "upwork_main"
      status: active
      note: "HITL only - no automation!"
      
  kwork:
    primary:
      username: "kwork_main"
      status: active
    backup:
      username: "kwork_backup"
      status: standby
      
  flru:
    primary:
      username: "flru_main"
      status: active
```

### Account Failover Procedure

```python
async def handle_platform_ban(platform: str, account: str):
    """
    Handle account suspension/ban.
    """
    # 1. Immediately pause all platform activity
    await pause_platform_activity(platform)
    
    # 2. Alert operators
    await notify_agent.send_critical_alert(
        f"🚨 Account {account} banned on {platform}!"
    )
    
    # 3. Check for backup account
    backup = await get_backup_account(platform)
    
    if backup and backup.status == "standby":
        # 4. Switch to backup (requires HITL confirmation)
        approval = await request_hitl_approval(
            type="account_switch",
            data={
                "platform": platform,
                "from_account": account,
                "to_account": backup.username
            }
        )
        
        if approval.granted:
            await switch_active_account(platform, backup)
            await resume_platform_activity(platform)
    else:
        # 5. No backup - manual intervention required
        await create_incident(
            severity="critical",
            title=f"No backup account for {platform}",
            runbook="Create new account manually"
        )
```

---

## 🌐 Disaster Recovery Procedures

### Scenario 1: Server Failure

| Step | Action | Time |
|------|--------|------|
| 1 | Spin up new VPS from image | 5 min |
| 2 | Pull latest Docker images | 5 min |
| 3 | Restore database from S3 | 15 min |
| 4 | Update DNS to new IP | 5-30 min |
| 5 | Verify all services healthy | 10 min |
| **Total** | | **~45 min** |

### Scenario 2: Database Corruption

```bash
# 1. Stop all writes
docker compose stop api agent-orchestrator

# 2. Assess corruption
docker compose exec postgres pg_dumpall -U mas > /tmp/check.sql 2>&1
cat /tmp/check.sql | grep -i error

# 3. If minor corruption, try repair
docker compose exec postgres psql -U mas -c "REINDEX DATABASE mas;"

# 4. If major corruption, restore from backup
docker compose exec postgres dropdb -U mas mas
docker compose exec postgres createdb -U mas mas
gunzip -c /backups/latest.sql.gz | docker compose exec -T postgres psql -U mas mas

# 5. Recalculate any in-flight projects
docker compose exec api python scripts/recalculate_project_states.py
```

### Scenario 3: LLM API Outage

```python
LLM_FALLBACK_CHAIN = [
    ("gemini-flash", "primary"),
    ("claude-opus", "fallback_1"),     # More expensive but reliable
    ("gemini-pro", "fallback_2"),      # Alternative Gemini model
    ("openai-gpt4", "fallback_3"),     # Emergency only
]

async def call_llm_with_fallback(prompt: str, task_type: str) -> str:
    for model, role in LLM_FALLBACK_CHAIN:
        try:
            return await call_llm(model, prompt)
        except RateLimitError:
            await log_event(f"Rate limited on {model}, trying next...")
            continue
        except APIError as e:
            await log_event(f"API error on {model}: {e}")
            continue
    
    # All models failed - queue for HITL
    await queue_for_human_processing(prompt, task_type)
    raise AllModelsFailedError("All LLM providers unavailable")
```

### Scenario 4: Platform Mass-Ban

```
⚠️ CRITICAL: If all accounts on a platform are banned

1. IMMEDIATELY stop all activity on that platform
2. Do NOT create new accounts (will be IP-linked)
3. Assess: Is this automation detection or TOS violation?
4. If automation detection:
   - Review anti-bot measures
   - Consider residential proxy rotation
   - Increase human-like delays
   - Wait 30-90 days before retry
5. If permanent ban:
   - Focus on other platforms
   - Consider white-label through partner agencies
```

---

## 📋 Recovery Testing Schedule

| Test Type | Frequency | Last Tested | Next Due |
|-----------|-----------|-------------|----------|
| Database restore | Monthly | - | - |
| Redis restore | Quarterly | - | - |
| Full DR simulation | Annually | - | - |
| Account failover | Semi-annually | - | - |
| LLM failover | Monthly | - | - |

### Recovery Test Checklist

```markdown
## Database Recovery Test

- [ ] Create test database backup
- [ ] Wipe test database
- [ ] Restore from backup
- [ ] Run integrity checks
- [ ] Verify all tables present
- [ ] Check row counts match
- [ ] Test application functionality
- [ ] Document restore time: ___ minutes
- [ ] Document any issues: ___
```

---

## 📞 Incident Response Contacts

| Role | Contact | Escalation Time |
|------|---------|-----------------|
| Primary On-Call | Telegram @operator1 | Immediate |
| Secondary On-Call | Telegram @operator2 | 15 min |
| Infrastructure Lead | Phone +7... | 30 min |
| Database Admin | Email dba@... | 1 hour |

---

## 📊 Backup Verification

```python
# scripts/verify_backup.py
async def verify_latest_backup():
    """
    Daily verification of backup integrity.
    """
    # 1. Check backup exists
    latest = get_latest_backup()
    if not latest:
        await alert("No backup found in last 24 hours!")
        return False
    
    # 2. Verify file integrity
    if not verify_checksum(latest):
        await alert(f"Backup checksum failed: {latest}")
        return False
    
    # 3. Test restore to temp database
    temp_db = create_temp_database()
    try:
        restore_to_database(latest, temp_db)
        row_count = get_row_counts(temp_db)
        
        # 4. Compare with production
        prod_counts = get_row_counts("mas")
        if abs(row_count - prod_counts) > 100:
            await alert(f"Backup row count mismatch: {row_count} vs {prod_counts}")
            return False
            
    finally:
        drop_database(temp_db)
    
    await log("Backup verification passed")
    return True
```
