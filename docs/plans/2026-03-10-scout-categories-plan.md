# P2.11 Scout Categories + Free-Text Queries

## Spec Reference
`dev-cycle-spec.md` lines 387-435

## Storage
Uses existing `PlatformAccount` table with sentinel `__scout_config__`.
Credentials JSON structure:
```json
{
  "categories_auto": ["landings", "bots", "api_backend", "design", "content", "small_fixes"],
  "categories_suggest": ["mobile_apps", "ml_ai", "devops", "consulting", "ecommerce", "system_integration"],
  "custom_rules": ["Если в заказе Figma — брать", "Заказы до $50 — пропускать"]
}
```

## Default Categories
Auto-take: landings, bots, api_backend, design, content, small_fixes
Suggest (HITL): mobile_apps, ml_ai, devops, consulting, ecommerce, system_integration

## API Endpoints (SettingsController)
- `GET /api/v1/settings/scout-config` — returns current categories + custom rules
- `PUT /api/v1/settings/scout-config` — updates categories + custom rules

## Scout Integration
- `ScoutAgent._score_job()` loads scout config from DB
- If job category matches "suggest" list → score_modifier -0.2 → HITL
- Custom rules sent to LLM with job description → LLM returns action: take/suggest/skip

## Dashboard (Settings page)
- New section "Scout Configuration"
- Two-column checklist: "AI берёт сам" and "Предложить мне"
- Custom rules: list of text inputs with add/remove buttons

## Files Changed
- `src/api/routes/settings.py` (EDIT) — GET/PUT scout-config endpoints
- `src/api/schemas.py` (EDIT) — ScoutConfigSchema
- `src/agents/scout.py` (EDIT) — load and apply scout config in scoring
- `dashboard/app/routes/_app.settings.tsx` (EDIT) — scout config UI
- `tests/unit/test_scout_categories.py` (NEW)
