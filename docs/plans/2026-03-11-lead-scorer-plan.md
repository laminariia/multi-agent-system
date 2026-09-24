# Lead Scorer — Implementation Plan

**Task**: Phase 3, Task #11 from gap-analysis.md
**Spec**: `docs/Full_work/specs/sales-agent-spec.md` §Lead Scorer
**Date**: 2026-03-11

## Problem

Pipeline B generates leads but has no automated scoring to prioritize them.
Without scoring, all leads get equal treatment regardless of potential value.

## Design

### Package: `src/core/lead_scorer.py`

**Data models** (dataclasses):

- `ScoringRule` — name, points, condition (callable), description
- `MatchedRule` — name, points, description (rule that matched)
- `ScoringResult` — lead_id, total_score, temperature, analysis_tier, matched_rules
- `WebsiteCheck` — exists, ssl, status_code, load_time_ms
- `RatingInfo` — google_rating, yandex_rating, review_count
- `SocialActivity` — is_dead (bool), last_post_days (int|None)
- `AnalysisResult` — lead_id, tier, website, rating, social_activity, seo, lighthouse

Note: AnalysisResult types are lightweight stubs consumed by Lead Scorer,
later shared with Business Analyzer (#12).

**SCORING_RULES** (7 rules per spec):

| Rule | Points | Condition |
|------|--------|-----------|
| no_website | +3 | website doesn't exist |
| no_mobile | +2 | website exists but not mobile-friendly |
| active_business | +2 | review_count >= 10 AND google_rating >= 4.0 |
| dead_social | +1 | social_activity.is_dead == True |
| high_value_category | +1 | lead.category in HIGH_VALUE_CATEGORIES |
| few_reviews | -1 | review_count < 10 |
| good_website | -2 | lighthouse.performance > 80 |

**HIGH_VALUE_CATEGORIES** — 18 bilingual categories per spec.

**LeadScorer** class:
- `score(lead, analysis) -> ScoringResult` — apply all rules, sum points, classify
- `_classify_temperature(score)` — hot (>=5), warm (>=3), cold (<3)
- `_determine_analysis_tier(score)` — deep (>=5), medium (>=3), quick (<3)

### Edge Cases

- Rule condition raises (missing data) → skip rule silently
- Negative total score → clamp to 0
- No rules match → cold, quick tier
- Analysis with partial data (only quick tier) → only applicable rules fire

### Files

| File | Change |
|------|--------|
| `src/core/lead_scorer.py` | NEW — scorer + analysis models |
| `tests/unit/test_lead_scorer.py` | NEW — scoring tests |
