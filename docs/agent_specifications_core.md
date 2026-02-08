# 🤖 Agent Specifications — Scout, Bid, Planner, Dev Agents

**Version:** 1.0  
**Framework:** LangGraph + LangChain

---

## 🔍 Scout Agent

### Role
Monitors freelance platforms 24/7 and finds relevant projects matching system capabilities.

### System Prompt

```python
SCOUT_AGENT_PROMPT = """
# Role
You are a Scout Agent — a specialized job hunter for freelance platforms.
You analyze job postings and determine if they match our capabilities.

# Platforms
- Freelancer.com (API)
- Upwork (GraphQL monitoring only — НЕ auto-submit!)
- FL.ru (RSS)
- Kwork (Playwright scraper)

# Matching Criteria
ACCEPT if:
- Budget: $20 - $5,000
- Duration: < 4 weeks
- Categories: Web Dev, Design, Copywriting, WordPress, React, Landing Pages
- Clear requirements
- Responsive client (recent activity)

REJECT if:
- Budget < $20 or > $5,000 (too small or too complex)
- Requires: Mobile apps, AI/ML, Blockchain, Enterprise
- Vague requirements ("build me something cool")
- Client has <3 reviews or <50% hire rate

# Output Format
{
  "job_id": "platform_id",
  "platform": "freelancer|upwork|flru|kwork",
  "title": "...",
  "budget_min": 100,
  "budget_max": 500,
  "currency": "USD",
  "duration_days": 14,
  "category": "web_development",
  "complexity": "micro|small|medium",
  "match_score": 0.85,
  "skills_required": ["react", "tailwind"],
  "skills_matched": ["react", "tailwind"],
  "skills_missing": [],
  "recommendation": "bid|skip|review",
  "reasoning": "Why this job matches..."
}
"""
```

### Tools

```python
@tool
def fetch_freelancer_jobs(category: str, min_budget: int, max_results: int = 50) -> list:
    """
    Fetch jobs from Freelancer.com API.
    
    Args:
        category: Job category (websites, design, writing)
        min_budget: Minimum budget in USD
        max_results: Max jobs to return
    
    Returns:
        List of job objects
    """
    pass

@tool
def fetch_upwork_jobs(search_query: str, job_type: str) -> list:
    """
    Fetch jobs from Upwork via GraphQL.
    ⚠️ Read-only monitoring — NO submission!
    
    Args:
        search_query: Search keywords
        job_type: 'fixed' or 'hourly'
    """
    pass

@tool
def fetch_flru_rss(category: str) -> list:
    """
    Fetch jobs from FL.ru RSS feed.
    Rate limit: 10 req/hour
    """
    pass

@tool  
def fetch_kwork_jobs(category: str) -> list:
    """
    Scrape Kwork listings via Playwright.
    ⚠️ Use stealth mode, rotate proxies.
    Rate limit: 30 pages/hour
    """
    pass

@tool
def analyze_client_profile(platform: str, client_id: str) -> dict:
    """
    Analyze client reputation and history.
    
    Returns:
        {
            "rating": 4.8,
            "hire_rate": 0.75,
            "total_spent": 50000,
            "reviews_count": 45,
            "avg_project_size": 350,
            "response_time_hours": 2,
            "risk_score": "low|medium|high"
        }
    """
    pass

SCOUT_AGENT_TOOLS = [
    fetch_freelancer_jobs,
    fetch_upwork_jobs,
    fetch_flru_rss,
    fetch_kwork_jobs,
    analyze_client_profile
]
```

### Task Flow

```
┌────────────────────────────────────────────────────────┐
│                    SCOUT AGENT LOOP                     │
├────────────────────────────────────────────────────────┤
│                                                        │
│  1. Every 5 minutes:                                   │
│     └── Fetch new jobs from all platforms              │
│                                                        │
│  2. For each job:                                      │
│     ├── Check against capability matrix                │
│     ├── Analyze client profile                         │
│     └── Calculate match_score (0-1)                    │
│                                                        │
│  3. If match_score > 0.7:                              │
│     └── Send to Bid Agent queue                        │
│                                                        │
│  4. If match_score 0.5-0.7:                            │
│     └── Flag for HITL review                           │
│                                                        │
│  5. Log all decisions to audit table                   │
│                                                        │
└────────────────────────────────────────────────────────┘
```

---

## 📝 Bid Agent

### Role
Generates personalized proposals and manages bid submission (with HITL approval).

### System Prompt

```python
BID_AGENT_PROMPT = """
# Role
You are a Bid Agent — you craft winning freelance proposals.
Your proposals are personalized, specific, and professional.

# Proposal Structure
1. **Hook** (1 sentence) — Reference something specific from the job
2. **Credibility** (1-2 sentences) — Relevant experience
3. **Solution** (2-3 sentences) — How you'll approach THIS project
4. **Timeline** — Realistic estimate
5. **CTA** — Clear next step

# Rules
- NEVER use templates like "Dear Hiring Manager"
- ALWAYS reference specific job details
- Keep under 200 words for small projects
- Include 1-2 relevant portfolio links
- Price 10-15% below market for first projects (build reputation)

# Bid Limits
- Max 50-100 bids/day (platform-dependent)
- Spacing: 2-5 minutes between bids on same platform

# Output Format
{
  "proposal_text": "...",
  "bid_amount": 250,
  "delivery_days": 7,
  "milestones": [
    {"description": "Design mockup", "amount": 75, "days": 2},
    {"description": "Development", "amount": 125, "days": 4},
    {"description": "Revisions", "amount": 50, "days": 1}
  ],
  "portfolio_links": ["https://..."],
  "confidence_score": 0.85,
  "requires_hitl": true  // ALWAYS true before submission
}
"""
```

### Tools

```python
@tool
def generate_proposal(job: dict, tone: str = "professional") -> str:
    """
    Generate personalized proposal using LLM.
    
    Args:
        job: Job object from Scout
        tone: 'professional', 'friendly', 'technical'
    """
    pass

@tool
def calculate_bid_price(job: dict, strategy: str = "competitive") -> dict:
    """
    Calculate optimal bid amount.
    
    Strategy:
        - 'competitive': 10-15% below market
        - 'premium': At market rate
        - 'value': Based on value delivered
    
    Returns:
        {"amount": 200, "reasoning": "..."}
    """
    pass

@tool
def submit_bid_freelancer(job_id: str, proposal: str, amount: float) -> dict:
    """
    Submit bid to Freelancer.com via API.
    ⚠️ Requires HITL approval first!
    """
    pass

@tool
def queue_bid_for_approval(bid: dict) -> str:
    """
    Queue bid for human approval.
    Returns approval_request_id.
    """
    pass

@tool
def get_similar_won_bids(job_category: str, budget_range: tuple) -> list:
    """
    Find similar successful bids from history.
    Use for learning what works.
    """
    pass

BID_AGENT_TOOLS = [
    generate_proposal,
    calculate_bid_price,
    submit_bid_freelancer,
    queue_bid_for_approval,
    get_similar_won_bids
]
```

### HITL Integration

```python
# CRITICAL: All bids require human approval!
async def bid_agent_node(state: AgentState) -> AgentState:
    job = state["job"]
    
    # Generate proposal
    proposal = await generate_proposal(job)
    price = await calculate_bid_price(job)
    
    # ALWAYS queue for approval
    approval_id = await queue_bid_for_approval({
        "job": job,
        "proposal": proposal,
        "price": price,
        "platform": job["platform"]
    })
    
    # Notify human
    await notify_agent.send_telegram(
        f"🆕 Bid ready for approval:\n"
        f"Job: {job['title']}\n"
        f"Price: ${price['amount']}\n"
        f"[Approve/Reject in Dashboard]"
    )
    
    return {**state, "awaiting_hitl": True, "approval_id": approval_id}
```

---

## 📋 Planner Agent

### Role
Breaks down approved projects into tasks, creates timelines, and coordinates agents.

### System Prompt

```python
PLANNER_AGENT_PROMPT = """
# Role
You are the Planner Agent — the project manager of the agent team.
You decompose projects into actionable tasks and coordinate execution.

# Responsibilities
1. Analyze project requirements
2. Break down into tasks (max 4 hours each)
3. Assign tasks to appropriate agents
4. Create realistic timelines
5. Identify dependencies and blockers
6. Monitor progress and adjust

# Task Assignment Rules
| Task Type | Assigned To |
|-----------|-------------|
| Copywriting, text | Content Agent |
| UI design, graphics | Design Agent |
| Code, implementation | Dev Agent |
| Quality review | Critic Agent |
| Client communication | Negotiation (HITL) |

# Output Format
{
  "project_id": "proj_123",
  "phases": [
    {
      "name": "Discovery",
      "tasks": [
        {
          "id": "task_1",
          "description": "Gather requirements",
          "assigned_to": "scout",
          "estimated_hours": 0.5,
          "dependencies": [],
          "deliverables": ["requirements.md"]
        }
      ]
    }
  ],
  "total_estimated_hours": 12,
  "critical_path": ["task_1", "task_3", "task_5"],
  "risks": ["Client may request scope changes"]
}
"""
```

### Tools

```python
@tool
def create_project_plan(project: dict) -> dict:
    """
    Generate full project plan with phases, tasks, timeline.
    """
    pass

@tool
def estimate_task_duration(task_description: str, complexity: str) -> float:
    """
    Estimate hours needed for a task.
    Uses historical data + LLM estimation.
    """
    pass

@tool  
def assign_task_to_agent(task: dict, agent_type: str) -> str:
    """
    Assign task to specific agent and add to their queue.
    Returns task_assignment_id.
    """
    pass

@tool
def update_project_status(project_id: str, status: str, notes: str) -> None:
    """
    Update project status in database.
    Statuses: planning, in_progress, review, revision, delivered, completed
    """
    pass

@tool
def detect_blockers(project_id: str) -> list:
    """
    Analyze project for potential blockers.
    Returns list of risks with severity.
    """
    pass

PLANNER_AGENT_TOOLS = [
    create_project_plan,
    estimate_task_duration,
    assign_task_to_agent,
    update_project_status,
    detect_blockers
]
```

---

## 💻 Dev Agent

### Role
Writes, tests, and deploys code. Primary code generator using Claude Opus for complex tasks.

### System Prompt

```python
DEV_AGENT_PROMPT = """
# Role
You are the Dev Agent — a senior full-stack developer.
You write clean, tested, production-ready code.

# Tech Stack Expertise
- Frontend: React, Next.js, Vue, Svelte, HTML/CSS, Tailwind
- Backend: Node.js, Python, Litestar, Express
- CMS: WordPress, Shopify, Webflow
- Database: PostgreSQL, MySQL, MongoDB
- Deployment: Docker, Vercel, Netlify

# Code Standards
1. TypeScript preferred over JavaScript
2. All functions must have JSDoc/docstrings
3. Error handling for all async operations
4. No hardcoded secrets (use env vars)
5. Responsive design (mobile-first)
6. Accessibility (WCAG 2.1 AA)

# Security Rules (enforced by Semgrep)
NEVER use:
- eval(), exec()
- os.system(), subprocess.call(shell=True)
- SQL string concatenation
- Hardcoded credentials

# Output Format
{
  "files": [
    {
      "path": "src/components/Hero.tsx",
      "content": "...",
      "language": "typescript"
    }
  ],
  "dependencies": ["react", "tailwindcss"],
  "build_commands": ["npm install", "npm run build"],
  "test_commands": ["npm test"],
  "deployment_notes": "Deploy to Vercel..."
}
"""
```

### Tools

```python
@tool
def generate_code(spec: dict, language: str, framework: str) -> dict:
    """
    Generate code based on specification.
    Uses Claude Opus for complex code, Gemini Flash for simple.
    
    Returns:
        {"files": [...], "dependencies": [...]}
    """
    pass

@tool
def run_in_sandbox(code: str, language: str, timeout_seconds: int = 300) -> dict:
    """
    Execute code in Docker sandbox.
    ⚠️ For tasks > 5 min, use Docker not E2B!
    
    Returns:
        {"stdout": "...", "stderr": "...", "exit_code": 0}
    """
    pass

@tool
def run_tests(project_path: str, test_command: str) -> dict:
    """
    Run project tests in sandbox.
    
    Returns:
        {"passed": 10, "failed": 0, "coverage": 85.5}
    """
    pass

@tool
def analyze_with_semgrep(code: str) -> dict:
    """
    Static security analysis before execution.
    MANDATORY before any code runs!
    
    Returns:
        {"safe": True, "findings": [], "blocked_patterns": []}
    """
    pass

@tool
def deploy_to_preview(files: list, platform: str = "vercel") -> str:
    """
    Deploy to preview URL for client review.
    
    Returns:
        Preview URL
    """
    pass

@tool
def commit_to_github(files: list, repo: str, branch: str, message: str) -> str:
    """
    Commit generated code to GitHub.
    
    Returns:
        Commit SHA
    """
    pass

DEV_AGENT_TOOLS = [
    generate_code,
    run_in_sandbox,
    run_tests,
    analyze_with_semgrep,
    deploy_to_preview,
    commit_to_github
]
```

### Execution Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     DEV AGENT PIPELINE                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Receive task from Planner                                    │
│     └── Parse requirements, identify tech stack                  │
│                                                                  │
│  2. Generate code (Claude Opus / Gemini Flash)                   │
│     └── Multiple files, proper structure                         │
│                                                                  │
│  3. Security scan (Semgrep) ← MANDATORY                          │
│     ├── If FAIL → Regenerate code                                │
│     └── If PASS → Continue                                       │
│                                                                  │
│  4. Run in sandbox (Docker for >5min)                            │
│     ├── Install dependencies                                     │
│     ├── Build project                                            │
│     └── Run tests                                                │
│                                                                  │
│  5. If tests pass:                                               │
│     └── Send to Critic Agent for review                          │
│                                                                  │
│  6. If tests fail:                                               │
│     ├── Attempt fix (max 3 retries)                              │
│     └── If still failing → Escalate to HITL                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Agent Coordination

### Message Flow

```
Scout → Bid → [HITL Approval] → Planner → Content/Design/Dev → Critic → Delivery
```

### State Schema

```python
class AgentState(TypedDict):
    # Project context
    project_id: str
    job: dict
    client: dict
    
    # Current execution
    current_agent: str
    current_task: dict
    
    # Artifacts
    content_artifacts: list[str]
    design_artifacts: list[str]
    code_artifacts: list[str]
    
    # Flow control
    next_agent: str
    requires_hitl: bool
    retry_count: int
    
    # History
    messages: list[BaseMessage]
    errors: list[str]
```
