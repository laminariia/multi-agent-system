# Project Rules — MAS

> Short, actionable rules. Auto-loaded by session-init and implement phases.
> Add new rules via accumulated patches or explicit decisions.
> Each rule = one line, no ambiguity.

## Stack Rules

- Backend is **Litestar**, NOT FastAPI. Never import from fastapi.
- Launch with `litestar --app src.api.main:app run --reload`, NOT uvicorn.
- Cache is **Valkey 8.1** (Redis-compatible). Use `valkey://` scheme.
- DB is **PostgreSQL 16** via `timescale/timescaledb-ha:pg16` Docker image.
- Embeddings are **OpenAI text-embedding-3-large** (3072 dim). NOT SentenceTransformer, NOT text-embedding-004.
- WebSocket via **Litestar ChannelsPlugin**, NOT Socket.IO.
- DB env var is `DATABASE_URL`, NOT `POSTGRES_URL`.
- All LLM calls go through **OpenRouter** (`OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL`).
- Embeddings go through **OpenAI direct** (`OPENAI_API_KEY`).

## LLM Assignment Rules

- Scout/Bid/Content/Packager/GeoScout/Outreach use **DeepSeek V3.2** (`deepseek/deepseek-v3.2`).
- Planner uses **Claude Opus 4.6** (`anthropic/claude-opus-4.6`).
- Dev (complex) uses **Claude Opus 4.6**, Dev (standard) uses **Claude Sonnet 4.5**.
- Design uses **NanoBanana Pro** = Gemini 3 Pro Image (`google/gemini-3-pro-image-preview`).
- Critic uses **Claude Sonnet 4.5** (`anthropic/claude-sonnet-4.5`).

## Code Rules

- ALWAYS pass credentials from Settings object, never rely on `os.environ` for `.env` values.
- Pydantic `.env` does NOT export to `os.environ`. Pass explicitly.
- Use `token_unique_jwt_id` for JWT JTI, NEVER `token_extras={"jti": ...}`.
- No `eval()`, `exec()`, `os.system()`, `subprocess.call(shell=True)` in production code.
- All Pydantic models use strict mode where possible.
- HTTP POST endpoints that don't create resources return 200, not 201.
- Health check path is `/health`, not `/api/v1/health`.
- Scout batch size is 10 jobs per LLM call (not 25 — prevents timeout).
- LLM request timeout is 120s (not 60s).
- BROWSER_HEADLESS defaults to True.

## Git Rules

- Work ONLY on `auto/{date}/{slug}` branches or `dev`.
- Never touch `main`/`master` directly.
- Checkpoint with git tag before major changes.
- Commit messages follow conventional commits (feat/fix/docs/refactor/test/chore).

## Dashboard Sync Rules

- EVERY backend feature that adds/changes API endpoints MUST include dashboard changes.
- New CRUD API → new dashboard page + sidebar link. No exceptions.
- After implementing any backend feature, run `/dashboard-sync` before marking complete.
- Dashboard routes live in `dashboard/app/routes/`, sidebar in `sidebar-nav.tsx`.
- If dashboard changes are deferred, explicitly note it in commit message and create a follow-up task.

## Testing Rules

- Tests accompany features — never standalone "add tests" goals.
- Tests must test REAL behavior, not just mock returns.
- `ruff check src/` must pass with zero errors before any commit.
- `pytest tests/unit/ -x` must pass before marking any goal completed.

## Platform Rules

- Freelancer.com = Primary, FL.ru = Primary, Kwork = Secondary, Upwork = Optional.
- Upwork FORBIDS auto-submit. Only HITL for Upwork bids.
- Browser automation requires playwright-stealth + residential proxies.

## Agent Isolation Rules

- Feature Worker: owns `src/`, never touches `tests/`.
- Quality Worker: owns `tests/`, reads but never modifies `src/`.
- Infra Worker: owns `docker/`, `alembic/`, CI configs. Never touches `src/`.
- Research Worker: read-only for code, write for `docs/`.

## Production Safety Rules

- Reject default JWT_SECRET_KEY and ENCRYPTION_KEY when DEBUG=False.
- Never expose stack traces in API error responses.
- All external API calls must have timeout and retry logic.
- Rate limiting required on all public endpoints.

---

*Last updated: 2026-02-21. Source: accumulated patches + CLAUDE.md conventions.*
