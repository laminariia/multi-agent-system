# Pipeline C: Client Growth & Upselling

> **Status**: STUB -- specification document for future implementation.
> **Owner**: SalesAgent + OutreachAgent
> **Priority**: P3 (post-MVP)

## 1. Overview

Pipeline C handles client retention and upselling after project delivery. It activates automatically 30 days after a project is marked as `completed` in Pipeline A or a deal is closed in Pipeline B.

### Goals

- Maintain long-term client relationships
- Detect upsell and cross-sell opportunities
- Generate recurring revenue from satisfied clients
- Build portfolio of repeat-client case studies

### Trigger Condition

```
project.status == "completed"
AND (now() - project.completed_at) >= 30 days
AND client.satisfaction_survey_sent == false
```

## 2. Pipeline Stages

### Stage 1: Satisfaction Survey (Day 30)

**Trigger**: 30 days after project delivery.

**Actions**:
1. Send personalized satisfaction survey via email/Telegram
2. Survey covers: overall satisfaction (1-5), communication rating, deliverable quality, likelihood to recommend (NPS)
3. If no response within 5 days, send one follow-up reminder
4. Record responses in Deal Memory

**Outputs**:
- `satisfaction_score`: 1-5 rating
- `nps_score`: 0-10 rating
- `feedback_text`: free-form client feedback
- `improvement_areas`: categorized feedback

**Decision Gate**:
- Score >= 4: proceed to Stage 2 (upsell detection)
- Score 3: trigger manual review (HITL), possible service recovery
- Score <= 2: trigger service recovery workflow, do NOT proceed to upsell

### Stage 2: Upsell Opportunity Detection (Day 35+)

**Trigger**: Satisfaction score >= 4 OR manual override.

**Actions**:
1. Analyze original project scope and deliverables
2. Scan client's current website/business for new needs:
   - Technology gaps (outdated stack, missing features)
   - Competitor analysis (what competitors have that client doesn't)
   - Industry trends applicable to client's business
   - Seasonal opportunities (holiday campaigns, new product launches)
3. Cross-reference with our capability registry
4. Score opportunities by:
   - Revenue potential (estimated budget)
   - Likelihood of acceptance (based on relationship strength)
   - Delivery feasibility (team availability, skill match)
   - Strategic value (case study potential, referral potential)

**Outputs**:
- `opportunities`: list of scored upsell opportunities
- `recommended_approach`: personalized pitch strategy
- `estimated_budget`: range for each opportunity
- `priority_ranking`: ordered by composite score

**LLM Tier**: Tier 1 (Claude Opus) for opportunity analysis, Tier 5 (Gemini Flash) for data extraction.

### Stage 3: Proposal Generation & Outreach (Day 40+)

**Trigger**: At least one opportunity with score >= 0.7.

**Actions**:
1. Generate tailored proposal for top opportunity
2. Reference original project success metrics
3. Include relevant portfolio pieces
4. Apply Touch Sequence from Pipeline B (adapted for existing clients):
   - Touch 1 (Day 0): Value-add email (industry insight, free tip)
   - Touch 2 (Day 3): Proposal email with specific opportunity
   - Touch 3 (Day 7): Follow-up with case study
   - Touch 4 (Day 14): Final check-in, offer consultation call
5. HITL review before sending proposal

**HITL Gate**: `upsell_proposal_review`
- Available actions: `approve`, `edit`, `reject`, `defer`
- Deferred proposals re-enter queue after 30 days

**Outputs**:
- `proposal_document`: generated proposal
- `outreach_sequence`: scheduled touch points
- `conversion_result`: accepted/declined/no_response

## 3. State Machine

```
SURVEY_PENDING
  → SURVEY_SENT
    → SURVEY_RESPONDED
      → ANALYZING_OPPORTUNITIES (score >= 4)
      → SERVICE_RECOVERY (score <= 2)
      → MANUAL_REVIEW (score == 3)
    → SURVEY_EXPIRED (no response after 2 reminders)

ANALYZING_OPPORTUNITIES
  → OPPORTUNITIES_FOUND
    → PROPOSAL_DRAFTING
      → HITL_REVIEW
        → OUTREACH_ACTIVE
          → CONVERTED (client accepts)
          → DECLINED (client declines)
          → NO_RESPONSE (all touches exhausted)
        → PROPOSAL_DEFERRED
      → NO_VIABLE_OPPORTUNITIES
  → NO_OPPORTUNITIES

SERVICE_RECOVERY
  → RECOVERY_COMPLETE → re-enter at SURVEY_PENDING (after 60 days)
  → CLIENT_LOST
```

## 4. Integration Points

### With Pipeline A

- **Input**: Completed projects with client info, deliverables, timeline
- **Trigger**: `project.status = "completed"` event
- **Data**: Project artifacts, client communication history, satisfaction metrics

### With Pipeline B

- **Shared**: Touch Sequence engine, email templates, suppression list
- **Reuse**: Lead scoring (adapted for existing clients), Business Analyzer
- **Shared**: Deal Memory for client context persistence

### With Existing Agents

| Agent | Role in Pipeline C |
|-------|-------------------|
| **SalesAgent** | Generates proposals, manages conversation |
| **OutreachAgent** | Executes email/Telegram touch sequence |
| **GeoScout** | Re-scans client's local market for changes |
| **Critic** | Reviews proposal quality before HITL |
| **Packager** | Formats final proposal document |

### With Dashboard

- New `/growth` page showing:
  - Clients eligible for outreach (30+ days post-delivery)
  - Active upsell pipelines with stage indicators
  - Conversion funnel metrics
  - Revenue attribution (upsell vs new client)

## 5. Data Model

### New Table: `client_growth` (Table 23)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | |
| `project_id` | UUID FK → projects | Original completed project |
| `deal_id` | UUID FK → deals | Original deal (if from Pipeline B) |
| `client_email` | TEXT | Client contact email |
| `client_name` | TEXT | Client name/company |
| `stage` | TEXT | Current pipeline stage |
| `satisfaction_score` | INT | 1-5 survey rating |
| `nps_score` | INT | 0-10 NPS rating |
| `feedback_text` | TEXT | Free-form feedback |
| `opportunities` | JSONB | Detected upsell opportunities |
| `proposal_id` | UUID | Generated proposal reference |
| `conversion_result` | TEXT | accepted/declined/no_response |
| `revenue_generated` | NUMERIC | Revenue from upsell (if converted) |
| `created_at` | TIMESTAMPTZ | Pipeline entry time |
| `updated_at` | TIMESTAMPTZ | Last stage change |
| `next_action_at` | TIMESTAMPTZ | Scheduled next action |

### Indexes

- `idx_client_growth_stage` ON `stage`
- `idx_client_growth_next_action` ON `next_action_at` WHERE `stage NOT IN ('CONVERTED', 'DECLINED', 'CLIENT_LOST')`
- `idx_client_growth_project` ON `project_id`

## 6. Metrics & KPIs

| Metric | Target | Measurement |
|--------|--------|-------------|
| Survey response rate | >= 60% | responses / surveys sent |
| Client satisfaction (avg) | >= 4.0 | average satisfaction_score |
| NPS | >= 50 | (promoters - detractors) / total |
| Upsell detection rate | >= 40% | opportunities found / eligible clients |
| Proposal acceptance rate | >= 25% | converted / proposals sent |
| Revenue per upsell | >= $2,000 | average revenue_generated |
| Time to upsell | <= 60 days | avg(conversion_date - trigger_date) |

## 7. Configuration

```python
PIPELINE_C_CONFIG = {
    # Timing
    "survey_delay_days": 30,
    "survey_reminder_days": 5,
    "survey_expiry_days": 15,
    "opportunity_analysis_delay_days": 5,
    "proposal_defer_days": 30,

    # Thresholds
    "min_satisfaction_for_upsell": 4,
    "min_opportunity_score": 0.7,
    "max_concurrent_proposals": 5,

    # Touch sequence (existing client variant)
    "touch_delays_days": [0, 3, 7, 14],
    "max_touches": 4,

    # Budget
    "llm_budget_per_analysis": 0.50,  # USD
    "max_monthly_pipeline_c_spend": 25.0,  # USD
}
```

## 8. Security & Compliance

- All client data subject to GDPR retention policies
- Unsubscribe link in every outreach email
- Suppression list integration (shared with Pipeline B)
- No outreach to clients who opted out
- Survey data encrypted at rest
- HITL mandatory for all proposals

## 9. Implementation Phases

### Phase 1 (P3): Survey System
- Satisfaction survey generation and sending
- Response collection and storage
- Basic dashboard metrics

### Phase 2 (P3): Opportunity Detection
- Business re-analysis engine
- Opportunity scoring
- Integration with capability registry

### Phase 3 (P4): Proposal & Outreach
- Proposal generation with SalesAgent
- Touch sequence execution
- Full conversion tracking
- Dashboard growth page

## 10. Dependencies

- Pipeline A completion tracking (exists)
- Pipeline B Touch Sequence engine (exists)
- Deal Memory (exists)
- Email infrastructure with warm-up (exists)
- SalesAgent (exists)
- HITL system (exists)

## 11. Open Questions

1. Should Pipeline C also handle referral requests from satisfied clients?
2. Optimal survey timing -- 30 days vs project-type-dependent delay?
3. Should service recovery (low satisfaction) be a separate pipeline?
4. Integration with CRM systems (HubSpot, Pipedrive) for enterprise clients?
5. Multi-language support for international client base?
