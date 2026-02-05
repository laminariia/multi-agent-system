# 📱 Telegram Bot Specification

**Version:** 1.0  
**Framework:** python-telegram-bot 21.x  
**Purpose:** Mobile HITL interface for urgent approvals and system monitoring

---

## 🏗️ Bot Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TELEGRAM BOT ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                     │
│  │  Telegram   │◀──▶│  Webhook    │◀──▶│  FastAPI    │                     │
│  │   Cloud     │    │  Handler    │    │  Backend    │                     │
│  └─────────────┘    └─────────────┘    └─────────────┘                     │
│                                                                             │
│  Features:                                                                  │
│  ├── 📳 Push notifications for urgent HITL                                 │
│  ├── ⚡ Quick approve/reject with inline buttons                            │
│  ├── 📊 Status commands (/status, /pending, /stats)                         │
│  └── 🔗 Deep links to Dashboard                                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🤖 Bot Commands

### `/start`
Welcome message and link account.

```python
async def start_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    # Check if already linked
    linked_user = await db.get_user_by_telegram_id(user_id)
    
    if linked_user:
        await update.message.reply_text(
            f"✅ Welcome back, {linked_user.name}!\n\n"
            f"Use /status for system overview or /pending for HITL queue."
        )
    else:
        # Generate link code
        link_code = generate_verification_code()
        await redis.setex(f"telegram_link:{link_code}", 600, user_id)
        
        await update.message.reply_text(
            "👋 Welcome to MAS Bot!\n\n"
            f"To link your account, enter this code in Dashboard Settings:\n\n"
            f"🔑 `{link_code}`\n\n"
            "This code expires in 10 minutes.",
            parse_mode="Markdown"
        )
```

### `/status`
System health overview.

```python
async def status_command(update: Update, context: CallbackContext):
    user = await verify_user(update.effective_user.id)
    if not user:
        return await update.message.reply_text("⚠️ Account not linked. Use /start")
    
    health = await get_system_health()
    
    status_emoji = "🟢" if health["status"] == "healthy" else "🔴" if health["status"] == "error" else "🟡"
    
    message = f"""
{status_emoji} **System Status**

**Agents:**
{format_agent_status(health["agents"])}

**Queue:**
📥 Pending HITL: {health["hitl_pending"]} ({health["hitl_urgent"]} urgent)

**Today:**
💼 Bids sent: {health["today"]["bids_sent"]}
✅ Won: {health["today"]["won"]}
📋 Active projects: {health["active_projects"]}

**Budget:**
💰 Spent: ${health["budget_today"]:.2f} / ${health["budget_limit"]:.2f}
"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Open Dashboard", url=DASHBOARD_URL)],
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_status")]
    ])
    
    await update.message.reply_text(message, parse_mode="Markdown", reply_markup=keyboard)
```

### `/pending`
List pending HITL items.

```python
async def pending_command(update: Update, context: CallbackContext):
    user = await verify_user(update.effective_user.id)
    if not user:
        return await update.message.reply_text("⚠️ Account not linked. Use /start")
    
    items = await get_pending_hitl(limit=10)
    
    if not items:
        return await update.message.reply_text("✅ No pending items! You're all caught up.")
    
    for item in items:
        await send_hitl_card(update.effective_chat.id, item)

async def send_hitl_card(chat_id: int, item: HITLItem):
    """Send formatted HITL item with action buttons."""
    
    priority_emoji = "🚨" if item.priority == "urgent" else "📋"
    
    if item.type == "bid_approval":
        message = f"""
{priority_emoji} **New Bid for Approval**

**Job:** {item.payload["job_title"][:50]}...
**Platform:** {item.payload["platform"]}
**Bid Amount:** ${item.payload["bid_amount"]:,.0f}
**Client Rating:** ⭐ {item.payload["client_rating"]}

📝 *Proposal preview:*
_{item.payload["proposal_text"][:200]}..._
"""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"hitl:approve:{item.id}"),
                InlineKeyboardButton("❌ Skip", callback_data=f"hitl:skip:{item.id}")
            ],
            [
                InlineKeyboardButton("⏸️ Later", callback_data=f"hitl:later:{item.id}"),
                InlineKeyboardButton("🔗 View Full", url=f"{DASHBOARD_URL}/hitl/{item.id}")
            ]
        ])
    
    elif item.type == "code_review":
        message = f"""
{priority_emoji} **Code Review Required**

**Project:** {item.payload["project_name"]}
**Files:** {item.payload["files_count"]} files
**Quality Score:** {item.payload["quality_score"]:.0%}

⚠️ Semgrep: {item.payload["security_issues"]} issues
"""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"hitl:approve:{item.id}"),
                InlineKeyboardButton("🔍 Review in Dashboard", url=f"{DASHBOARD_URL}/hitl/{item.id}")
            ]
        ])
    
    elif item.type == "delivery":
        message = f"""
{priority_emoji} **Delivery Ready**

**Project:** {item.payload["project_name"]}
**Client:** {item.payload["client_name"]}
**Deadline:** {item.payload["deadline"]}

Files packaged and ready for delivery.
"""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 Deliver", callback_data=f"hitl:approve:{item.id}"),
                InlineKeyboardButton("🔍 Review", url=f"{DASHBOARD_URL}/hitl/{item.id}")
            ],
            [
                InlineKeyboardButton("⏸️ Hold", callback_data=f"hitl:later:{item.id}")
            ]
        ])
    
    await bot.send_message(chat_id, message, parse_mode="Markdown", reply_markup=keyboard)
```

### `/stats`
Today's statistics.

```python
async def stats_command(update: Update, context: CallbackContext):
    stats = await get_daily_stats()
    
    message = f"""
📊 **Today's Performance**

**Freelance Pipeline:**
🔍 Jobs scanned: {stats["jobs_scanned"]}
✅ Qualified: {stats["qualified"]}
💼 Bids sent: {stats["bids_sent"]}
🏆 Won: {stats["won"]}
📈 Win rate: {stats["win_rate"]:.0%}

**Outreach Pipeline:**
📍 Leads discovered: {stats["leads_discovered"]}
📧 Emails sent: {stats["emails_sent"]}
💬 Replies: {stats["replies"]}

**Revenue:**
💰 Today: ${stats["revenue_today"]:,.0f}
📅 This month: ${stats["revenue_month"]:,.0f}

**Costs:**
🤖 LLM: ${stats["llm_cost"]:.2f}
☁️ Infra: ${stats["infra_cost"]:.2f}
"""
    
    await update.message.reply_text(message, parse_mode="Markdown")
```

### `/approve {id}`
Quick approve a HITL item.

### `/skip {id}`
Skip a HITL item.

---

## 📳 Push Notifications

### Notification Types

| Type | Priority | When |
|------|----------|------|
| `urgent_hitl` | High | Bid expires in < 2 hours |
| `new_hitl` | Normal | New HITL item added |
| `project_won` | High | Client accepted bid |
| `deadline_warning` | High | Project deadline in < 24h |
| `agent_error` | High | Agent failed 3+ times |
| `budget_alert` | Normal | Daily budget > 80% |

### Notification Manager

```python
class TelegramNotifier:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.rate_limiter = RateLimiter(limit=30, window=60)  # 30/min per user
    
    async def notify_hitl(self, user_id: int, item: HITLItem):
        """Send HITL notification."""
        
        # Check rate limit
        if not await self.rate_limiter.check(f"notify:{user_id}"):
            return  # Skip if rate limited
        
        # Check user notification preferences
        user = await db.get_user(user_id)
        if not self.should_notify(user, item):
            return
        
        await send_hitl_card(user.telegram_chat_id, item)
        
        # Mark as sent in DB
        await db.update_hitl(item.id, telegram_sent=True)
    
    def should_notify(self, user: User, item: HITLItem) -> bool:
        """Check user notification preferences."""
        prefs = user.settings.get("telegram_notifications", {})
        
        # Always notify for urgent
        if item.priority == "urgent":
            return True
        
        # Check type preferences
        return prefs.get(f"notify_{item.type}", True)
    
    async def notify_system_alert(self, alert_type: str, message: str):
        """Send system alert to all owners."""
        owners = await db.get_users_by_role("owner")
        
        for owner in owners:
            if owner.telegram_chat_id:
                await self.bot.send_message(
                    owner.telegram_chat_id,
                    f"⚠️ **System Alert**\n\n{message}",
                    parse_mode="Markdown"
                )
```

### Quiet Hours

```python
class QuietHoursManager:
    def is_quiet_hours(self, user: User) -> bool:
        """Check if in user's quiet hours (e.g., 23:00 - 08:00)."""
        quiet_hours = user.settings.get("quiet_hours", {})
        
        if not quiet_hours.get("enabled"):
            return False
        
        user_tz = pytz.timezone(user.settings.get("timezone", "UTC"))
        now = datetime.now(user_tz)
        
        start = quiet_hours.get("start", 23)  # 23:00
        end = quiet_hours.get("end", 8)       # 08:00
        
        current_hour = now.hour
        
        if start > end:  # Overnight (e.g., 23-8)
            return current_hour >= start or current_hour < end
        else:  # Same day (e.g., 14-18)
            return start <= current_hour < end
    
    async def should_send_notification(self, user: User, priority: str) -> bool:
        if priority == "urgent":
            return True  # Always send urgent
        
        return not self.is_quiet_hours(user)
```

---

## 🔘 Inline Button Handling

```python
async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()  # Acknowledge button press
    
    data = query.data
    
    if data.startswith("hitl:"):
        action, hitl_id = data.split(":")[1:3]
        await handle_hitl_action(query, action, hitl_id)
    
    elif data == "refresh_status":
        await refresh_status_message(query)

async def handle_hitl_action(query: CallbackQuery, action: str, hitl_id: str):
    user = await verify_user(query.from_user.id)
    
    if user.role != "owner":
        await query.edit_message_text("❌ No permission to resolve HITL items.")
        return
    
    result = await resolve_hitl(hitl_id, action, user.id)
    
    if result["success"]:
        action_text = {
            "approve": "✅ Approved!",
            "skip": "⏭️ Skipped",
            "later": "⏸️ Postponed"
        }.get(action, action)
        
        await query.edit_message_text(
            query.message.text + f"\n\n{action_text} by {user.name}",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(f"❌ Error: {result['error']}")
```

---

## 🔗 Deep Links

| Link | Purpose |
|------|---------|
| `t.me/MASBot?start=link_{code}` | Link Telegram to Dashboard |
| `t.me/MASBot?start=hitl_{id}` | Open specific HITL item |
| `t.me/MASBot?start=project_{id}` | View project status |

---

## 🛡️ Security

### User Verification

```python
async def verify_user(telegram_id: int) -> Optional[User]:
    """Verify Telegram user is linked to a dashboard account."""
    
    user = await db.query(User).filter(
        User.telegram_chat_id == telegram_id
    ).first()
    
    return user

# Decorator for protected commands
def require_linked_account(func):
    async def wrapper(update: Update, context: CallbackContext):
        user = await verify_user(update.effective_user.id)
        
        if not user:
            await update.message.reply_text(
                "⚠️ Your Telegram is not linked to a MAS account.\n\n"
                "Use /start to link your account."
            )
            return
        
        # Add user to context
        context.user_data["mas_user"] = user
        return await func(update, context)
    
    return wrapper
```

### Rate Limiting

```python
class TelegramRateLimiter:
    LIMITS = {
        "commands": RateLimit(30, RateLimitWindow.MINUTE),
        "hitl_actions": RateLimit(10, RateLimitWindow.MINUTE),
    }
    
    async def check_command(self, user_id: int) -> bool:
        key = f"tg_rate:{user_id}:commands"
        return await self.check(key, self.LIMITS["commands"])
```

---

## 📊 Database Table

```sql
CREATE TABLE telegram_notifications (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    hitl_id         UUID REFERENCES hitl_queue(id),
    message_id      BIGINT,                          -- Telegram message ID
    chat_id         BIGINT,
    sent_at         TIMESTAMP WITH TIME ZONE,
    read_at         TIMESTAMP WITH TIME ZONE,
    action_taken    VARCHAR(30),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## ⚙️ Configuration

```python
# Bot settings
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.environ["TELEGRAM_WEBHOOK_URL"]
DASHBOARD_URL = os.environ["DASHBOARD_URL"]

# Notification settings
MAX_NOTIFICATIONS_PER_HOUR = 30
URGENT_THRESHOLD_HOURS = 2  # Notify urgent if expiring in < 2h

# Bot configuration
bot_config = {
    "token": TELEGRAM_BOT_TOKEN,
    "webhook": {
        "url": WEBHOOK_URL,
        "max_connections": 100,
        "allowed_updates": ["message", "callback_query"]
    }
}
```
