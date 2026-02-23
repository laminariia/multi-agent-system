# Onboarding for Claude Code

Guide for new developers joining the Multi-Agent Service project.

## First Setup

1. Clone the repo
2. `cp .env.example .env` — fill in secrets (see comments in `.env.example`)
3. Create a Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
   pip install -r requirements.txt
   ```
4. Install Claude Code CLI: https://docs.anthropic.com/en/docs/claude-code
5. Run `claude` in the project root — it will auto-read `CLAUDE.md` and `.claude/rules/`

## MCP Servers

On first launch, Claude Code will prompt to approve MCP servers from `.mcp.json`:
- **context7** — library documentation lookup
- **sequential-thinking** — complex reasoning
- **exa** — web search for research
- **playwright** — browser automation and testing
- **semgrep** — static code analysis (requires `pip install semgrep` or `pipx install semgrep`)
- **serena** — semantic code understanding (requires `pip install serena` or `pipx install serena`)
- **sentry** — production error monitoring
- **railway** — deployment management (requires `railway login`)

Optional personal MCP servers (not in project config):
- **vestige** — personal long-term memory (install from https://github.com/nshkrdotcom/vestige)

## Project Knowledge Structure

| File | What it contains |
|------|-----------------|
| `CLAUDE.md` | Main file — tech stack, rules, pipelines, project structure |
| `RULES.md` | Code conventions and rules |
| `TECH_STACK.md` | Full stack with rationale |
| `.claude/rules/patterns.md` | Canonical code patterns (agent, state, HITL, routing, tests) |
| `.claude/rules/debugging.md` | Known gotchas (LangGraph, Railway, Playwright, testing) |
| `docs/` | 25+ detailed documents per subsystem |

**Important**: `.claude/rules/` files are auto-loaded by Claude Code at session start. You don't need to manually reference them.

## Team Mode

Agents in `.claude/agents/` are ready for use in complex tasks:

| Agent | Scope | What it does |
|-------|-------|-------------|
| Feature Worker | `src/` only | Implements features, fixes bugs |
| Quality Worker | `tests/` only | Writes tests, code review |
| Research Worker | `docs/` only | Research, documentation |
| Infra Worker | `docker/`, `alembic/`, CI | Infrastructure, migrations |
| DB Migration Reviewer | read-only | Reviews Alembic migrations |

For complex tasks (4+ files), Claude automatically creates a team and delegates work.

## Slash Commands

Project-specific commands available via `/command-name`:
- `/dashboard-sync` — check if frontend needs updating after backend changes
- `/code-review` — structured code review
- `/deploy` — Railway deployment with pre-flight checks

## Running Tests

```bash
# Quick check (unit tests only)
pytest tests/unit/ -x

# Full test suite (2087+ tests)
pytest tests/ -x

# Linting
ruff check src/ tests/

# Format check
ruff format --check src/ tests/
```

## Key Architecture Notes

- **10 agents** in two pipelines (A: freelance, B: outreach)
- **Litestar** framework (NOT FastAPI)
- **Valkey** for caching (NOT Redis) — env var `VALKEY_URL`
- **LangGraph** for orchestration — see `.claude/rules/debugging.md` for critical gotchas
- HITL (Human-in-the-Loop) gates at bid submission and final delivery
- All LLM calls via **OpenRouter** (`OPENROUTER_API_KEY`)

## Deploy

Two Railway services:
- **API**: `railway up --detach` (from project root)
- **Dashboard**: `railway up --detach --service dashboard --path-as-root dashboard`

See `/deploy` command for full pre-flight checklist.

## Getting Help

- `CLAUDE.md` → Documentation Index — links to all `docs/` files
- `.claude/rules/debugging.md` — if something breaks, check here first
- `docs/edge_cases.md` — error handling, failure scenarios, alerts
