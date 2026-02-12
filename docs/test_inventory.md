# Test Inventory

> Auto-generated on 2026-02-13. Update by running: `python -c "..." > docs/test_inventory.md`

## Summary

| Category    | Files | Tests | Coverage Gate |
|-------------|------:|------:|---------------|
| Unit        |    85 | 1,979 | 70% min       |
| Integration |    10 |    76 | --            |
| E2E         |     4 |    39 | --            |
| Load        |     3 |   n/a | --            |
| **Total**   |**102**|**2,094**|             |

## Unit Tests (85 files, 1,979 tests)

### API Layer
| File | Tests | Description |
|------|------:|-------------|
| test_api_main.py | 32 | Litestar app init, middleware, lifespan |
| test_api_routes_hitl.py | 55 | HITL approval/reject/bulk resolve endpoints |
| test_api_routes_settings.py | 47 | Settings CRUD, credential encryption |
| test_api_routes_orchestrator.py | 35 | Orchestrator status, start/stop, goals |
| test_api_routes_agents.py | 28 | Agent listing, control, heartbeat |
| test_api_guards.py | 25 | Auth guards, JWT validation |
| test_api_routes_jobs.py | 21 | Job listing, filtering, detail |
| test_api_routes_auth.py | 20 | Login, register, token refresh |
| test_api_websocket.py | 20 | WebSocket channels, pub/sub |
| test_api_dependencies.py | 13 | DI container, dependency providers |
| test_api_health.py | 10 | Health endpoint, readiness probes |
| test_api_routes_metrics.py | 9 | Prometheus metrics endpoint |
| test_api_routes_pipeline_b_detail.py | 6 | Pipeline B lead detail routes |

### Agents
| File | Tests | Description |
|------|------:|-------------|
| test_base_agent_extended.py | 41 | Base agent lifecycle, heartbeat, retry |
| test_bid_agent_extended.py | 21 | Bid generation, platform formatting |
| test_scout_agent_extended.py | 24 | Job scanning, dedup, filtering |
| test_dev_packager_extended.py | 30 | Dev+Packager integration |
| test_critic_agent.py | 15 | Code review, Semgrep gating |
| test_planner_agent.py | 13 | Task decomposition, dependency graph |
| test_content_agent.py | 9 | Copywriting, proposal drafts |
| test_design_agent.py | 9 | UI/UX mockup generation |
| test_bid_agent.py | 8 | Basic bid generation |
| test_dev_agent.py | 8 | Code generation, sandbox |
| test_packager_agent.py | 9 | Deliverable packaging |
| test_scout_agent.py | 6 | Basic scout scan |
| test_scout_agent_db.py | 9 | Scout DB persistence |
| test_pipeline_b.py | 40 | GeoScout + Outreach pipeline |

### Core
| File | Tests | Description |
|------|------:|-------------|
| test_core_models.py | 89 | SQLAlchemy models, relationships |
| test_encryption.py | 55 | Fernet encrypt/decrypt, key rotation |
| test_core_config.py | 31 | Settings, env validation |
| test_core_database.py | 24 | DB pool, async sessions |
| test_state.py | 26 | LangGraph AgentState management |
| test_json_repair.py | 31 | Malformed JSON recovery |
| test_exceptions.py | 29 | Custom exception hierarchy |
| test_heartbeat.py | 6 | Heartbeat monitor |
| test_loop_detector.py | 7 | Infinite loop detection |
| test_semantic_cache.py | 6 | Basic semantic cache |
| test_semantic_cache_extended.py | 28 | Cache TTL, invalidation, similarity |
| test_monitoring.py | 6 | Prometheus metrics helpers |
| test_tracing.py | 14 | LangSmith tracing integration |

### Graph & HITL
| File | Tests | Description |
|------|------:|-------------|
| test_graph_routing.py | 47 | LangGraph edge routing logic |
| test_graph_builders_and_hitl.py | 34 | Graph construction, HITL nodes |
| test_hitl_resume.py | 25 | HITL checkpoint resume flow |
| test_hitl_worker_additions.py | 29 | Worker HITL queue processing |
| test_hitl_bulk_resolve.py | 8 | Bulk approve/reject |
| test_jobs_scan_pipeline.py | 12 | Full job scan flow |

### Platform Adapters
| File | Tests | Description |
|------|------:|-------------|
| test_adapters_freelancer.py | 35 | Freelancer.com API client |
| test_fl_ru_adapter.py | 40 | FL.ru RSS parsing |
| test_kwork_adapter.py | 15 | Kwork scraper |
| test_upwork_adapter.py | 14 | Upwork monitoring |

### Bot (Telegram)
| File | Tests | Description |
|------|------:|-------------|
| test_bot_orchestrator.py | 51 | Orchestrator commands, callbacks |
| test_bot_notifications.py | 38 | Push notifications |
| test_bot_commands.py | 35 | General bot commands |
| test_bot_keyboards.py | 34 | Inline keyboard builders |
| test_bot_handler.py | 17 | Handler registration |
| test_bot_dashboard_sync.py | 10 | Dashboard-bot sync |
| test_bot_main.py | 11 | Bot startup/shutdown |

### Security & Sandbox
| File | Tests | Description |
|------|------:|-------------|
| test_sandbox.py | 57 | E2B/Docker sandbox execution |
| test_semgrep_gate.py | 12 | Semgrep static analysis gate |
| test_security_headers.py | 23 | Nginx security headers |
| test_env_validation.py | 22 | Environment variable validation |
| test_sentry_config.py | 31 | Sentry SDK configuration |

### Enrichment & Geo
| File | Tests | Description |
|------|------:|-------------|
| test_enrichment_modules.py | 28 | Hunter.io, Apollo.io, OSINT |
| test_geo_modules.py | 28 | H3 indexing, Overpass API |
| test_email_sender.py | 23 | Email delivery, templates |

### Browser
| File | Tests | Description |
|------|------:|-------------|
| test_stealth_browser.py | 14 | Anti-detect, stealth patches |
| test_session_manager.py | 10 | Cookie persistence, rotation |
| test_browser_pool.py | 9 | Browser pool management |

### Knowledge Base
| File | Tests | Description |
|------|------:|-------------|
| test_knowledge_cli.py | 28 | Knowledge ingestion CLI |
| test_knowledge_embedding.py | 26 | Embedding generation |
| test_knowledge_ingestion.py | 26 | Document ingestion pipeline |
| test_knowledge_retrieval.py | 8 | RAG retrieval |

### Workers & CLI
| File | Tests | Description |
|------|------:|-------------|
| test_user_registration.py | 61 | User registration flow |
| test_worker_tasks.py | 23 | Background task execution |
| test_worker_scheduler.py | 19 | Cron scheduling |
| test_worker_queue.py | 18 | Valkey queue processing |
| test_worker_main.py | 12 | Worker entry point |
| test_cli_seed_data.py | 26 | Database seeding CLI |
| test_cli_create_user.py | 15 | User creation CLI |
| test_credential_loader.py | 10 | DB credential loader |
| test_settings_test_credential.py | 10 | Settings credential tests |

### Other
| File | Tests | Description |
|------|------:|-------------|
| test_bugfix_501.py | 26 | Regression for bugfix #501 |
| test_safety_improvements.py | 8 | Safety module hardening |
| test_llm_client.py | 19 | LLM client fallback/retry |
| test_orchestrator_parsers.py | 17 | Goal/status YAML parsing |

## Integration Tests (10 files, 76 tests)

| File | Tests | Description |
|------|------:|-------------|
| test_sentry_integration.py | 18 | Sentry DSN, breadcrumbs, transactions |
| test_metrics_endpoint.py | 10 | Prometheus /metrics with live app |
| test_api_endpoints.py | 10 | API endpoint smoke tests |
| test_full_pipeline.py | 10 | Pipeline A end-to-end |
| test_production_graph.py | 9 | LangGraph production config |
| test_pipeline_b_flow.py | 7 | Pipeline B GeoScout+Outreach |
| test_browser_adapter_flow.py | 4 | Browser + adapter integration |
| test_checkpoint_storage.py | 3 | LangGraph checkpoint persistence |
| test_semgrep_critic_flow.py | 3 | Semgrep + Critic pipeline |
| test_bid_pipeline.py | 2 | Bid generation + submission |

## E2E Tests (4 files, 39 tests)

| File | Tests | Description |
|------|------:|-------------|
| test_auth.py | 13 | Login, register, session flows |
| test_pages.py | 12 | Dashboard page navigation |
| test_hitl.py | 8 | HITL approval UI workflow |
| test_full_pipeline_e2e.py | 6 | Full system E2E with browser |

## Load Tests

| File | Description |
|------|-------------|
| locustfile.py | Locust load testing scenarios (API, WebSocket, HITL) |
| conftest_load.py | Load test fixtures and config |
| validate_results.py | Post-run validation of load test results |

## Running Tests

```bash
# Unit tests with coverage
pytest tests/unit/ -v --cov=src --cov-fail-under=70 --cov-report=html:htmlcov

# Integration tests (requires postgres + valkey)
pytest tests/integration/ -v --timeout=300

# E2E tests (requires full stack running)
pytest tests/e2e/ -v --timeout=300

# Load tests
cd tests/load && locust -f locustfile.py --headless -u 50 -r 10 -t 5m

# All checks (see scripts/run_all_checks.sh)
bash scripts/run_all_checks.sh
```
