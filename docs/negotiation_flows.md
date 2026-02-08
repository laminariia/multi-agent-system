# 🔄 Negotiation & Communication Flows

**Version:** 1.0  
**Scope:** Client communication between bid acceptance and project completion

---

## 📊 Negotiation State Machine

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        NEGOTIATION STATE MACHINE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐             │
│  │   BID    │───▶│ AWAITING │───▶│ ACCEPTED │───▶│ PLANNING │             │
│  │   SENT   │    │ RESPONSE │    │          │    │          │             │
│  └──────────┘    └────┬─────┘    └──────────┘    └──────────┘             │
│                       │                                                     │
│           ┌───────────┼───────────┐                                        │
│           ▼           ▼           ▼                                        │
│     ┌──────────┐ ┌──────────┐ ┌──────────┐                                │
│     │ QUESTION │ │ COUNTER  │ │ REJECTED │                                │
│     │          │ │  OFFER   │ │          │                                │
│     └──────────┘ └──────────┘ └──────────┘                                │
│           │           │                                                    │
│           ▼           ▼                                                    │
│     ┌──────────┐ ┌──────────┐                                             │
│     │  HITL    │ │  HITL    │                                             │
│     │ REQUIRED │ │ REQUIRED │                                             │
│     └──────────┘ └──────────┘                                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Negotiation Scenarios

### 1. Client Questions (Pre-Hire)

| Trigger | Detection | Response | HITL |
|---------|-----------|----------|------|
| Clarification request | Keywords: "can you", "how would", "what if" | Bid Agent generates answer | Optional review |
| Technical question | Code/framework specific terms | Dev Agent + Bid Agent | Optional |
| Timeline question | "how long", "when", "deadline" | Bid Agent (use estimation from original bid) | No |
| References request | "portfolio", "examples", "similar" | Auto-respond with portfolio link | No |

**Auto-Response Template:**
```python
class ClientQuestionHandler:
    QUESTION_TYPES = {
        "clarification": {
            "patterns": ["can you", "how would you", "what's your approach"],
            "handler": "generate_clarification_response",
            "hitl_required": False
        },
        "technical": {
            "patterns": ["react", "node", "api", "database"],
            "handler": "generate_technical_response",
            "hitl_required": True  # Complex, needs review
        },
        "timeline": {
            "patterns": ["deadline", "how long", "when can"],
            "handler": "generate_timeline_response",
            "hitl_required": False
        },
        "pricing": {
            "patterns": ["price", "cost", "budget", "discount"],
            "handler": None,  # Always HITL
            "hitl_required": True
        }
    }
    
    async def handle_question(self, message: str, context: BidContext) -> Response:
        question_type = self.classify_question(message)
        
        if question_type["hitl_required"]:
            return await self.create_hitl_request(
                type="client_question",
                message=message,
                suggested_response=await self.generate_draft(message, context)
            )
        
        return await self.auto_respond(question_type["handler"], message, context)
```

---

### 2. Counter Offers (Price Negotiation)

**Detection:**
- Keywords: "budget is", "can you do it for", "lower price", "discount"
- Number mentioned < original bid

**Response Strategy:**

```python
class CounterOfferHandler:
    # Never auto-accept counter offers
    ALWAYS_HITL = True
    
    async def analyze_counter(
        self, 
        original_bid: Decimal, 
        counter_amount: Decimal,
        project_complexity: str
    ) -> CounterAnalysis:
        diff_percent = (original_bid - counter_amount) / original_bid * 100
        
        return CounterAnalysis(
            original=original_bid,
            counter=counter_amount,
            difference_percent=diff_percent,
            recommendation=self.get_recommendation(diff_percent),
            suggested_response=self.generate_response(diff_percent)
        )
    
    def get_recommendation(self, diff_percent: float) -> str:
        if diff_percent <= 10:
            return "ACCEPT_POSSIBLE"  # Small discount, likely acceptable
        elif diff_percent <= 25:
            return "NEGOTIATE"  # Counter with middle ground
        else:
            return "DECLINE_OR_REDUCE_SCOPE"  # Too low, scope reduction needed
    
    def generate_response(self, diff_percent: float) -> str:
        if diff_percent <= 10:
            return "I can work with that budget. Let's proceed!"
        elif diff_percent <= 25:
            return f"I understand your budget. Would ${middle_ground} work? I can adjust the timeline slightly."
        else:
            return "That's below my minimum for this scope. I could offer a reduced version for that budget - would that interest you?"
```

**HITL Interface for Counter Offers:**
```json
{
  "type": "counter_offer",
  "priority": "normal",
  "title": "Client counter offer: $1,500 (originally $2,000)",
  "payload": {
    "original_bid": 2000,
    "counter_amount": 1500,
    "difference_percent": 25,
    "recommendation": "NEGOTIATE",
    "suggested_responses": [
      {"action": "accept", "text": "I can work with $1,500..."},
      {"action": "counter", "text": "Would $1,750 work?", "amount": 1750},
      {"action": "decline", "text": "Unfortunately $1,500 is below my minimum..."}
    ],
    "client_message": "Can you do it for $1,500?"
  },
  "available_actions": ["accept", "counter", "decline", "custom_response"]
}
```

---

### 3. Scope Changes (Pre-Hire)

**Detection:**
- New requirements mentioned
- "Also", "additionally", "one more thing"
- Feature not in original job post

**Handling:**

```python
class ScopeChangeHandler:
    async def detect_scope_change(
        self, 
        message: str, 
        original_requirements: list
    ) -> Optional[ScopeChange]:
        prompt = f"""
        Original job requirements:
        {original_requirements}
        
        Client message:
        {message}
        
        Is the client asking for something NOT in the original requirements?
        If yes, return JSON: {{"is_scope_change": true, "new_items": [...]}}
        If no, return: {{"is_scope_change": false}}
        """
        
        result = await llm.generate(prompt)
        return ScopeChange(**result) if result["is_scope_change"] else None
    
    async def handle_scope_change(self, change: ScopeChange, context: BidContext):
        # Calculate additional cost
        additional_estimate = await self.estimate_additional_work(change.new_items)
        
        return await self.create_hitl_request(
            type="scope_change",
            payload={
                "new_items": change.new_items,
                "additional_estimate": additional_estimate,
                "suggested_responses": [
                    f"Happy to include that! It would add approximately ${additional_estimate} and {days} days to the timeline.",
                    f"Great addition! Let's discuss this as a Phase 2 after the initial delivery.",
                    f"That's definitely possible. Want me to revise the proposal with this included?"
                ]
            }
        )
```

---

### 4. Client Silence (No Response)

**Follow-up Schedule:**

| Days Since Message | Action | HITL |
|--------------------|--------|------|
| 2 days | Gentle reminder | Auto |
| 5 days | Second follow-up | Auto |
| 10 days | Final check-in | Auto |
| 14 days | Archive as "No Response" | Auto |

**Auto Follow-up Templates:**

```python
FOLLOW_UP_TEMPLATES = {
    2: "Hi! Just checking in on this. Let me know if you have any questions!",
    5: "Wanted to follow up on my proposal. I'm still available and interested in helping with this project.",
    10: "Final check-in - I'll assume you've moved in a different direction if I don't hear back. Best of luck with your project!",
}

class FollowUpScheduler:
    async def schedule_follow_ups(self, bid_id: str):
        for days, template in FOLLOW_UP_TEMPLATES.items():
            await scheduler.add_job(
                self.send_follow_up,
                trigger="date",
                run_date=datetime.now() + timedelta(days=days),
                args=[bid_id, template]
            )
    
    async def send_follow_up(self, bid_id: str, message: str):
        bid = await get_bid(bid_id)
        
        # Check if still awaiting response
        if bid.status != "awaiting_response":
            return  # Already responded, skip
        
        await platform_adapter.send_message(bid.job_id, message)
        await log_event("follow_up_sent", bid_id=bid_id)
```

---

## ⚠️ Error Handling & Edge Cases

### 1. Platform Account Issues

| Issue | Detection | Response |
|-------|-----------|----------|
| Account suspended | Login fails + ban message | 🚨 CRITICAL HITL |
| Rate limited | 429 response | Pause for 1 hour, notify |
| Session expired | Redirect to login | Auto re-login attempt |
| 2FA required | 2FA prompt detected | HITL for code entry |

### 2. Project Execution Issues

| Issue | Detection | Response |
|-------|-----------|----------|
| Client unresponsive (in project) | No reply 5+ days | Auto follow-up → HITL |
| Deadline risk | < 24h remaining, < 80% complete | 🚨 URGENT HITL |
| Revision limit exceeded | revision_count >= 3 | HITL (discuss with client) |
| Bug reports from client | Negative feedback keywords | Prioritize fix, HITL if complex |

### 3. Concurrent Projects Limit

```python
# Phase 1: MAX_CONCURRENT_PROJECTS = 5, increases per tier
# Phase 2: 10, Phase 3+: 15-20
MAX_CONCURRENT_PROJECTS = 5  # Phase 1 default
MIN_QUALITY_THRESHOLD = 0.85

class ProjectCapacityManager:
    async def can_accept_new_project(self) -> tuple[bool, str]:
        active_count = await count_active_projects()
        
        if active_count >= MAX_CONCURRENT_PROJECTS:
            return False, f"At capacity ({MAX_CONCURRENT_PROJECTS} active projects)"
        
        # Check quality isn't suffering
        recent_quality = await get_average_quality_score(days=30)
        if recent_quality < MIN_QUALITY_THRESHOLD:
            return False, f"Quality score below threshold ({recent_quality:.0%})"
        
        return True, "Capacity available"
    
    async def handle_won_bid(self, bid_id: str):
        can_accept, reason = await self.can_accept_new_project()
        
        if not can_accept:
            await create_hitl_request(
                type="capacity_warning",
                title="Won bid but at capacity",
                payload={
                    "bid_id": bid_id,
                    "reason": reason,
                    "options": [
                        "Accept anyway (overload)",
                        "Negotiate delayed start",
                        "Decline politely"
                    ]
                }
            )
```

---

## 📊 Communication Analytics

Track these metrics for optimization:

| Metric | Description | Target |
|--------|-------------|--------|
| Response Time | Time to reply to client | < 2 hours |
| Conversion Rate | Questions → Hired | > 60% |
| Counter Accept Rate | Counter offers accepted | < 30% |
| Follow-up Effectiveness | Responses after follow-up | > 15% |
| Scope Creep Rate | Projects with added scope | Track only |

---

## 🗄️ Database Tables (Additional)

```sql
-- Track all client communications
CREATE TABLE client_messages (
    id              UUID PRIMARY KEY,
    bid_id          UUID REFERENCES bids(id),
    project_id      UUID REFERENCES projects(id),
    direction       VARCHAR(10) NOT NULL,  -- 'inbound', 'outbound'
    message_type    VARCHAR(30),           -- 'question', 'counter_offer', 'scope_change', 'general'
    content         TEXT NOT NULL,
    platform        VARCHAR(50),
    external_id     VARCHAR(255),          -- platform's message ID
    auto_generated  BOOLEAN DEFAULT FALSE,
    hitl_reviewed   BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Track negotiation history
CREATE TABLE negotiations (
    id              UUID PRIMARY KEY,
    bid_id          UUID REFERENCES bids(id),
    original_amount DECIMAL(10,2),
    final_amount    DECIMAL(10,2),
    rounds          INTEGER DEFAULT 0,     -- number of counter-offers
    outcome         VARCHAR(20),           -- 'accepted', 'declined', 'scope_adjusted'
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE
);
```
