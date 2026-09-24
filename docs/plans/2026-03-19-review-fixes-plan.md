# Review Fixes Plan — 84 Issues

> Date: 2026-03-19
> Source: 5 chain reviews after Wave 1-3 implementation
> Strategy: Parallel fix agents grouped by file ownership

---

## Fix Groups (by file ownership to avoid conflicts)

### Group A: graph.py fixes (SEQUENTIAL — single file)
**Agent: fix-graph**
- [C1.1] Design Agent revision index: add is_revision check before increment
- [C1.2] "reject" action routes to planner_node, not END
- [H1.3] design_feedback never cleared → add to update_state
- [H1.4] Add design_review + design_client_approval to _RESUMABLE_TYPES
- [H2.1] HITL #1 Lead Card gate missing → add hitl_lead_card_node
- [H2.2] HITL #3 Concept Approved missing → add hitl_concept_approved_node
- [H2.3] _lead_replied flag never set → document entry mechanism
- [H2.4] _concept_edited flag never set → set in _apply_concept_review
- [M2.1] next_agent="planner" dead code in sales_agent → clean up
- [M1.1] _route_after_design dead code → remove

### Group B: sales_agent.py fixes
**Agent: fix-sales**
- [C2.1] SQL wildcard injection: add _escape_like to 3 ILIKE queries
- [C2.2] Deal.value/Deal.category → Deal.budget, remove Deal.category
- [H2.5] Broad exception catch → narrow to specific exceptions
- [M2.2] DealUpdateSchema status pattern → expand regex

### Group C: email system fixes
**Agent: fix-email**
- [C3.1] Add suppression check before send in send_approved_emails
- [C3.2] Unify placeholder format: warmup_templates → {{first_name}} style
- [H3.1] Enforce production gate in send_approved_emails
- [H3.2] Add advance_stage scheduled task config
- [H3.3] Replace hash() with hashlib.sha256 for A/B stability
- [H3.4] Bridge BounceStats → WarmupManager.record_send
- [M3.1] Add business hours filter
- [M3.2] Change bare Exception to error-level log

### Group D: notifications + telegram wiring
**Agent: fix-notifications**
- [C4.1] hmac.compare_digest for webhook secret
- [C4.2] time.time() instead of time.monotonic() in batch.py
- [H4.1] Register TelegramWebhookController in main.py
- [H4.2] Wire rate_limiter into bot handler/commands
- [H4.3] Wire quiet_hours into notification router
- [H4.4] Document phone call as future (or stub Twilio)
- [M4.1] HTTPS validation for webhook URL
- [M4.2] Align rate limit values to spec (30/min general)
- [M4.3] Fix quiet hours default start_hour 22→23
- [M4.4] Fix deliver_queued TOCTOU race

### Group E: design.py + pencil_mcp.py
**Agent: fix-design**
- [H1.5] needs_design field: add to state, Planner sets it, routing checks it
- [M1.2] Add code export to create_design flow
- [M1.3] preview_link propagation to HITL payload
- [M1.4] PencilMCPClient singleton instead of per-call
- [M1.5] Path validation for pen_file_path

### Group F: adapters fixes
**Agent: fix-adapters**
- [H5.1] Add circuit breaker to YouDo/Fiverr/ProfiRu constructors
- [H5.2] Register default rate limits for 3 new platforms
- [M5.1] Fiverr login redirect → SessionExpiredError not CaptchaDetectedError
- [M5.2] Add _SUBMIT_GUARD to YouDo and ProfiRu
- [L5.1] Extract shared code into _browser_adapter_base.py (if time)

### Group G: agents fixes (portfolio, webscout, tg_aggregator)
**Agent: fix-agents**
- [H5.3] Portfolio: implement _take_screenshots stub (Playwright)
- [H5.4] WebScout: source field from lead_data, not hardcoded "web_search"
- [M5.3] TG Aggregator: CRON_INTERVAL 15→5 per spec
- [M5.4] TG Aggregator: use extract_json instead of custom parser
- [M5.5] Portfolio: call _run_audit from _execute
- [M5.6] Portfolio: stub _save_to_portfolio filesystem method
- [M5.7] WebScout: move Decimal import to module level

### Group H: capability_registry + execution_cloaking + gdpr
**Agent: fix-core**
- [C5.1] Execution Cloaking: LLM-generated messages instead of static templates
- [C5.2] GDPR: erasure cascade includes consent/access/breach tables
- [H5.5] CapReg: add REJECT_CATEGORIES + CapabilityReporter
- [H5.6] GDPR consent endpoint: add auth guards
- [H5.7] GDPR list_dsars: add list_access_requests to GDPRManager
- [M5.8] Execution Cloaking: dispatch actually sends via channels
- [M5.9] GDPR breach status transition validation
- [M5.10] CapReg: confidence decay mechanism

### Group I: Telegram bot design HITL buttons
**Agent: fix-tg-buttons**
- [H1.6] Add 6 design HITL callback handlers to bot handler
- [M2.3] Lead scores persist to DB in geo_scout
- [M2.4] Analyzer tier selection from lead score
- [M2.5] Touch sequences DB persistence stub

### Group J: Remaining LOW items
**Agent: fix-low**
- [L] StrEnum for Severity
- [L] httpx.AsyncClient reuse in TelegramChannel
- [L] touch_count/last_contacted_at updates
- [L] DealMemory persistence stub
- [L] Stale deal detection stub
- [L] _utcnow import cleanup
- [L] MASException status_code handling
- [L] Decimal import placement
- [L] random.choice seed for warmup templates
- [L] advance_stage boundary condition docs
