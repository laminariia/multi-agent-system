# ⚠️ Edge Cases & Error Scenarios

**Version:** 1.0  
**Scope:** All unhandled scenarios identified in architecture gap analysis

---

## 🎯 Edge Case Matrix

| Category | Scenario | Probability | Impact | Priority |
|----------|----------|-------------|--------|----------|
| Platform | Account banned | Low | Critical | P0 |
| Platform | Rate limit hit | Medium | High | P1 |
| Client | Project cancelled mid-work | Medium | High | P1 |
| Client | 10+ wins simultaneously | Low | High | P1 |
| Outreach | Email bounce > 10% | Medium | Medium | P2 |
| Agent | Dev fails after 3 retries | Low | Medium | P2 |
| System | LLM API down | Low | Critical | P0 |

---

## 🚨 P0: Critical Scenarios

### 1. Platform Account Banned

**Trigger:** Login fails + ban/suspension message detected

**Detection:**
```python
class AccountHealthChecker:
    BAN_INDICATORS = [
        "account has been suspended",
        "временно заблокирован",
        "access denied",
        "permanently banned",
        "ToS violation"
    ]
    
    async def check_login(self, account: PlatformAccount) -> AccountStatus:
        try:
            session = await self.login(account)
            return AccountStatus.HEALTHY
        except LoginFailedError as e:
            if any(indicator in str(e).lower() for indicator in self.BAN_INDICATORS):
                return AccountStatus.BANNED
            return AccountStatus.CREDENTIALS_INVALID
```

**Response:**
1. 🚨 Immediate HITL alert (Telegram + Dashboard)
2. Pause all operations on that platform
3. Switch to backup account if available
4. Log incident for analysis

```python
async def handle_account_ban(account: PlatformAccount, reason: str):
    # 1. Update account status
    await db.update_account(account.id, status="banned", ban_reason=reason)
    
    # 2. Create urgent HITL
    await create_hitl_request(
        type="alert",
        priority="urgent",
        title=f"🚨 {account.platform} ACCOUNT BANNED",
        payload={
            "account_id": account.id,
            "platform": account.platform,
            "username": account.username,
            "reason": reason,
            "active_bids": await count_active_bids(account.id),
            "active_projects": await count_active_projects(account.id)
        },
        available_actions=["acknowledge", "switch_backup", "manual_check"]
    )
    
    # 3. Pause platform operations
    await pause_platform_agent(account.platform)
    
    # 4. Check for backup account
    backup = await get_backup_account(account.platform)
    if backup:
        await notify_telegram(
            f"Backup account available: {backup.username}. "
            f"Reply 'SWITCH' to activate."
        )
```

---

### 2. LLM API Down / Degraded

**Trigger:** Repeated 5xx errors or timeouts from LLM provider

**Response:**
1. Activate fallback LLM (Gemini ↔ Claude failover)
2. Pause non-critical operations
3. Queue critical operations for retry
4. Alert if all providers down

```python
class LLMFailoverManager:
    PROVIDERS = [
        {"name": "gemini", "priority": 1, "for_tasks": ["analysis", "bid_gen"]},
        {"name": "claude", "priority": 2, "for_tasks": ["code_gen", "complex"]},
        {"name": "openai", "priority": 3, "for_tasks": ["fallback"]},  # Emergency only
    ]
    
    async def get_available_provider(self, task_type: str) -> Optional[str]:
        for provider in sorted(self.PROVIDERS, key=lambda x: x["priority"]):
            if task_type in provider["for_tasks"] or "fallback" in provider["for_tasks"]:
                if await self.check_health(provider["name"]):
                    return provider["name"]
        return None
    
    async def handle_provider_failure(self, provider: str, error: Exception):
        self.failure_counts[provider] += 1
        
        if self.failure_counts[provider] >= 3:
            # Switch to fallback
            fallback = await self.get_available_provider("fallback")
            
            if fallback:
                logger.warning(f"{provider} failed 3x. Switching to {fallback}")
                await self.activate_fallback(fallback)
            else:
                # All providers down
                await create_hitl_request(
                    type="alert",
                    priority="urgent",
                    title="🚨 ALL LLM PROVIDERS DOWN",
                    payload={"error": str(error)},
                    available_actions=["pause_system", "wait_and_retry"]
                )
```

---

## 🟠 P1: High-Impact Scenarios

### 3. Client Cancels Project Mid-Work

**Trigger:** 
- Client sends cancellation message
- Milestone refund request detected
- Project deleted from platform

**Detection keywords:** "cancel", "refund", "don't need", "changed mind", "отмена"

**Response:**
```python
async def handle_project_cancellation(project_id: str, reason: str):
    project = await get_project(project_id)
    
    # Calculate work done
    progress = await calculate_project_progress(project_id)
    hours_spent = await calculate_hours_spent(project_id)
    
    # Determine response strategy
    if progress < 20:
        strategy = "full_refund_likely"
    elif progress < 80:
        strategy = "partial_payment_negotiate"
    else:
        strategy = "full_payment_request"
    
    await create_hitl_request(
        type="project_cancellation",
        priority="urgent",
        title=f"Project cancelled: {project.title}",
        payload={
            "project_id": project_id,
            "client_reason": reason,
            "progress_percent": progress,
            "hours_spent": hours_spent,
            "agreed_amount": project.agreed_amount,
            "suggested_strategy": strategy,
            "draft_response": generate_cancellation_response(strategy, progress)
        },
        available_actions=[
            "accept_refund",
            "negotiate_partial",
            "request_full_payment",
            "custom_response"
        ]
    )
    
    # Pause project work
    await update_project(project_id, status="cancelled_pending")
```

---

### 4. 10+ Projects Won Simultaneously

**Trigger:** Won more projects than capacity allows

**Detection:** Daily wins > `MAX_CONCURRENT_PROJECTS - current_active`

**Response:**
```python
class CapacityOverflowHandler:
    MAX_CONCURRENT = 5
    
    async def handle_multiple_wins(self, new_wins: list[Bid]):
        current_active = await count_active_projects()
        capacity_left = self.MAX_CONCURRENT - current_active
        
        if len(new_wins) <= capacity_left:
            return await self.accept_all(new_wins)
        
        # Need to prioritize
        ranked = await self.rank_by_priority(new_wins)
        
        to_accept = ranked[:capacity_left]
        to_negotiate = ranked[capacity_left:]
        
        await create_hitl_request(
            type="capacity_overflow",
            priority="urgent",
            title=f"🎉 {len(new_wins)} projects won! Capacity: {capacity_left}",
            payload={
                "recommended_accept": [
                    {"title": b.job.title, "amount": b.bid_amount, "score": b.score}
                    for b in to_accept
                ],
                "recommended_delay": [
                    {"title": b.job.title, "amount": b.bid_amount, "score": b.score}
                    for b in to_negotiate
                ],
                "total_value": sum(b.bid_amount for b in new_wins)
            },
            available_actions=[
                "accept_recommended",
                "accept_all_overload",
                "custom_selection"
            ]
        )
    
    async def rank_by_priority(self, bids: list[Bid]) -> list[Bid]:
        """Rank by: deadline urgency, amount, client rating."""
        return sorted(bids, key=lambda b: (
            -b.job.deadline_priority,  # Urgent first
            -b.bid_amount,             # Higher value
            -b.job.client_rating       # Better clients
        ))
```

**Delayed Start Template:**
```
Hi! Great news - I'd love to work on this project!

I'm finishing up a current commitment and can start on [DATE].
Would a [X]-day delay work for you? 

I want to give your project my full attention rather than 
juggling too many things at once.
```

---

### 5. Platform Rate Limit Hit

**Trigger:** 429 response or rate limit message

**Response:**
```python
class RateLimitRecovery:
    async def handle_rate_limit(self, platform: str, retry_after: int):
        # Log event
        await log_event("rate_limit_hit", platform=platform)
        
        # Pause platform operations
        await redis.setex(f"platform_paused:{platform}", retry_after + 60, "1")
        
        # Notify if long pause (> 1 hour)
        if retry_after > 3600:
            await notify_telegram(
                f"⚠️ {platform} rate limited for {retry_after // 3600}h. "
                f"Operations paused."
            )
        
        # Schedule resume
        await scheduler.add_job(
            self.resume_platform,
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=retry_after),
            args=[platform]
        )
```

---

## 🟡 P2: Medium-Impact Scenarios

### 6. Email Bounce Rate > 10%

**Trigger:** Campaign bounce rate exceeds threshold

**Response:**
```python
async def monitor_campaign_health(campaign_id: str):
    stats = await get_campaign_stats(campaign_id)
    
    bounce_rate = stats.bounce_count / stats.sent_count if stats.sent_count > 0 else 0
    
    if bounce_rate > 0.10:
        # Pause campaign
        await pause_campaign(campaign_id)
        
        # Analyze bounces
        bounced_leads = await get_bounced_leads(campaign_id)
        patterns = analyze_bounce_patterns(bounced_leads)
        
        await create_hitl_request(
            type="campaign_health",
            priority="normal",
            title=f"Campaign bounce rate {bounce_rate:.0%}",
            payload={
                "campaign_id": campaign_id,
                "bounce_count": stats.bounce_count,
                "bounce_rate": bounce_rate,
                "patterns": patterns,
                "recommendations": [
                    "Review email content for spam triggers",
                    "Verify enrichment source quality",
                    "Check domain reputation"
                ]
            },
            available_actions=["resume", "pause", "stop_permanently"]
        )
```

---

### 7. Dev Agent Fails After 3 Retries

**Trigger:** Task completed with errors 3 consecutive times

**Response:**
```python
class AgentRetryManager:
    MAX_RETRIES = 3
    
    async def handle_task_failure(self, agent: str, task_id: str, error: str):
        retry_count = await redis.incr(f"task_retry:{task_id}")
        
        if retry_count >= self.MAX_RETRIES:
            # Escalate to HITL
            task = await get_task(task_id)
            
            await create_hitl_request(
                type="agent_failure",
                priority="normal",
                title=f"{agent} agent failed on task",
                payload={
                    "task_id": task_id,
                    "task_description": task.description,
                    "agent": agent,
                    "error": error,
                    "retry_count": retry_count,
                    "logs": await get_task_logs(task_id),
                    "suggestions": [
                        "Simplify task requirements",
                        "Break into smaller subtasks",
                        "Manual intervention needed"
                    ]
                },
                available_actions=[
                    "retry_with_different_approach",
                    "break_into_subtasks",
                    "assign_manually",
                    "skip_task"
                ]
            )
            
            # Mark task as blocked
            await update_task(task_id, status="blocked")
        else:
            # Retry with backoff
            delay = 30 * (2 ** retry_count)  # Exponential backoff
            logger.info(f"Retrying task {task_id} in {delay}s (attempt {retry_count + 1})")
            await asyncio.sleep(delay)
            await retry_task(task_id)
```

---

## 🔧 Recovery Procedures

### Automatic Recovery

```python
RECOVERY_PROCEDURES = {
    "account_ban": {
        "auto_actions": ["pause_platform", "switch_backup"],
        "requires_hitl": True,
        "escalation_time": "immediate"
    },
    "rate_limit": {
        "auto_actions": ["pause_and_wait", "resume_when_cleared"],
        "requires_hitl": False,  # Unless > 1 hour
        "escalation_time": "1_hour"
    },
    "llm_down": {
        "auto_actions": ["failover_to_backup"],
        "requires_hitl": True,  # If all providers down
        "escalation_time": "5_minutes"
    },
    "project_cancellation": {
        "auto_actions": ["pause_work", "calculate_refund"],
        "requires_hitl": True,
        "escalation_time": "immediate"
    },
    "capacity_overflow": {
        "auto_actions": ["rank_by_priority"],
        "requires_hitl": True,
        "escalation_time": "immediate"
    }
}
```

### Manual Recovery Checklist

| Scenario | Steps |
|----------|-------|
| Account Banned | 1. Check email for details 2. Appeal if possible 3. Register new account 4. Update credentials |
| All LLMs Down | 1. Check provider status pages 2. Wait for recovery 3. Clear queue of expired jobs |
| Data Corruption | 1. Stop all agents 2. Restore from backup 3. Verify integrity 4. Resume operations |

---

## 🔥 Client Dispute Escalation

### Types of Disputes

| Dispute Type | Trigger | Response Time |
|--------------|---------|---------------|
| Quality complaint | Client claims work is incomplete/wrong | 24h |
| Non-payment | Client refuses to pay after delivery | 48h |
| Scope creep | Client wants more than agreed | 24h |
| Cancellation refund | Client wants refund after work started | 24h |
| Plagiarism claim | Client claims work is copied | Immediate |

### Detection

```python
DISPUTE_KEYWORDS = {
    "quality": ["not what I asked", "doesn't work", "bugs", "wrong", "incomplete"],
    "payment": ["won't pay", "refund", "dispute", "chargeback", "scam"],
    "scope": ["also need", "you should add", "this wasn't included", "more features"],
    "plagiarism": ["copied", "plagiarism", "not original", "seen this before"]
}

async def detect_dispute_signal(message: Message) -> Optional[DisputeType]:
    text = message.content.lower()
    for dispute_type, keywords in DISPUTE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return dispute_type
    return None
```

### Escalation Flow

```python
async def handle_dispute(project_id: str, dispute_type: str, client_message: str):
    project = await get_project(project_id)
    
    # Gather evidence
    evidence = {
        "original_requirements": project.requirements,
        "delivered_files": await list_deliverables(project_id),
        "message_history": await get_conversation(project_id),
        "milestones_completed": await get_completed_milestones(project_id),
        "time_tracked": await get_time_logs(project_id)
    }
    
    # Generate defense strategy based on dispute type
    if dispute_type == "quality":
        strategy = await generate_quality_defense(evidence, client_message)
    elif dispute_type == "payment":
        strategy = await generate_payment_reminder(evidence)
    elif dispute_type == "scope":
        strategy = await generate_scope_clarification(evidence, client_message)
    elif dispute_type == "plagiarism":
        strategy = "immediate_hitl"  # Never auto-respond to plagiarism
    
    await create_hitl_request(
        type="dispute",
        priority="urgent",
        title=f"⚠️ Dispute: {dispute_type.upper()} - {project.title}",
        payload={
            "project_id": project_id,
            "dispute_type": dispute_type,
            "client_message": client_message,
            "evidence_summary": evidence,
            "suggested_response": strategy,
            "platform": project.platform,
            "project_value": project.agreed_amount
        },
        available_actions=[
            "send_suggested_response",
            "escalate_to_platform",
            "offer_partial_refund",
            "stand_firm",
            "custom_response"
        ]
    )
```

### Platform-Specific Dispute Resolution

| Platform | Dispute System | Our Approach |
|----------|---------------|--------------|
| Freelancer | Dispute Resolution Center | Gather evidence, respond within 24h |
| Upwork | Mediation → Arbitration | Avoid disputes (HITL prevents most) |
| FL.ru | Admin moderation | Russian language response templates |

### Response Templates

**Quality Dispute:**
```
Hi [Client],

I understand your concerns, and I want to make this right.

Looking at the original requirements:
[Quote requirements]

And what was delivered:
[List deliverables with links]

I'd be happy to address any specific issues. Could you please clarify:
1. Which specific functionality isn't working as expected?
2. What exactly differs from what you envisioned?

Let's work together to get this resolved.
```

**Scope Creep Response:**
```
Hi [Client],

Thanks for the additional ideas! These features sound great.

However, they weren't part of our original agreement:
[Quote original scope]

I'd be happy to implement these as a separate milestone for an additional $[X].
Alternatively, we can discuss adjusting the current scope.

What would work best for you?
```

---

## 💀 Complete LLM Outage Recovery

### Scenario: All Providers Down

When Gemini, Claude, AND OpenAI are all unavailable:

```python
class TotalOutageHandler:
    """Handle scenario where all LLM providers are down."""
    
    async def handle_total_outage(self, duration_minutes: int = 0):
        # 1. Immediately pause all agent operations
        await self.pause_all_agents()
        
        # 2. Send critical alert
        await send_critical_alert(
            title="🚨 TOTAL LLM OUTAGE",
            message=f"All LLM providers are down. System on hold.",
            channels=["telegram", "sms", "email"]
        )
        
        # 3. Queue all pending tasks (don't lose them)
        pending_tasks = await get_pending_tasks()
        for task in pending_tasks:
            await queue_for_recovery(task)
        
        # 4. Start health check loop
        asyncio.create_task(self.monitor_recovery())
    
    async def pause_all_agents(self):
        """Gracefully pause all agents."""
        agents = ["scout", "bid", "planner", "dev", "content", "design", 
                  "critic", "packager", "outreach", "geoscout"]
        
        for agent in agents:
            await redis.set(f"agent_paused:{agent}", "1")
            logger.warning(f"Paused {agent} agent due to LLM outage")
    
    async def monitor_recovery(self):
        """Check every 5 minutes if providers are back."""
        while True:
            await asyncio.sleep(300)  # 5 minutes
            
            providers_status = await check_all_providers()
            
            if any(p["healthy"] for p in providers_status):
                await self.resume_operations(providers_status)
                break
            else:
                # Update status
                duration = await get_outage_duration()
                await update_dashboard_status(
                    status="outage",
                    message=f"LLM outage ongoing: {duration} minutes"
                )
    
    async def resume_operations(self, providers_status: list):
        """Resume when at least one provider is back."""
        healthy = [p for p in providers_status if p["healthy"]]
        primary = healthy[0]["name"]
        
        logger.info(f"LLM provider {primary} is back online. Resuming...")
        
        # 1. Unpause agents
        agents = ["scout", "bid", "planner", "dev", "content", "design",
                  "critic", "packager", "outreach", "geoscout"]
        for agent in agents:
            await redis.delete(f"agent_paused:{agent}")
        
        # 2. Process queued tasks with priority
        queued_tasks = await get_recovery_queue()
        for task in sorted(queued_tasks, key=lambda t: t.priority):
            await retry_task(task.id)
        
        # 3. Send recovery notification
        await send_notification(
            title="✅ LLM Service Restored",
            message=f"Using {primary}. {len(queued_tasks)} tasks resuming.",
            channels=["telegram"]
        )
```

### What Still Works During Outage

| Function | Status | Notes |
|----------|--------|-------|
| Dashboard | ✅ Works | Static views only |
| HITL queue | ✅ Works | Can approve pending items |
| Manual operations | ✅ Works | Human can work directly |
| Monitoring | ✅ Works | Still collecting metrics |
| New job ingestion | ⚠️ Queued | Scout paused, RSS still collected |
| Bid submission | ❌ Blocked | Requires LLM |
| Code generation | ❌ Blocked | Requires LLM |

### Manual Fallback Actions

During extended outage (>2 hours):

1. **Scout manually** - Check platform dashboards directly
2. **Bid manually** - Use proposal templates with manual customization
3. **Communicate with clients** - Use template responses
4. **Prioritize** - Focus on in-progress projects only

---

## 📊 Monitoring & Alerting


### Key Metrics to Watch

| Metric | Warning | Critical |
|--------|---------|----------|
| Account login failures | 2/hour | 5/hour |
| LLM error rate | 5% | 20% |
| Task retry rate | 10% | 30% |
| Email bounce rate | 5% | 10% |
| Queue depth | 50 jobs | 100 jobs |
| HITL response time | > 2h | > 4h |

### Alert Channels

| Severity | Channels |
|----------|----------|
| Critical (P0) | Telegram (immediate) + Dashboard + Email |
| High (P1) | Telegram + Dashboard |
| Medium (P2) | Dashboard only |
| Low | Logs only |
