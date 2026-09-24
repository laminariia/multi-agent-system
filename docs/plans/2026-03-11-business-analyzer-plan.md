# Business Analyzer — Implementation Plan

**Task**: Phase 3, Task #12 from gap-analysis.md
**Spec**: `docs/Full_work/specs/sales-agent-spec.md` §Business Analyzer
**Date**: 2026-03-11

## Problem

Pipeline B leads need automated analysis before scoring and outreach.
No automated system to assess website quality, social presence, or competitive landscape.

## Design

### File: `src/core/business_analyzer.py`

**BusinessAnalyzer** class — three-tier analysis engine:

| Tier | Trigger | What |
|------|---------|------|
| quick (~5s) | All leads | Website exists, rating, social presence |
| medium (~30s) | Score >= 3 | + Lighthouse, SEO, social activity |
| deep (2-3m) | Score >= 5 | + Traffic, technologies, competitors, Battlecard |

**Methods**:
- `analyze(lead, tier="quick") -> AnalysisResult` — main entry point
- `_check_website(url) -> WebsiteCheck` — HTTP probe
- `_check_social_presence(lead) -> SocialPresence`
- `_get_rating(lead) -> RatingInfo` — Google/Yandex rating
- `_run_lighthouse(url) -> dict` — Lighthouse performance
- `_check_basic_seo(url) -> dict` — title, meta, mobile
- `_check_social_activity(lead) -> dict` — last post dates
- `_estimate_traffic(url) -> dict` — SimilarWeb (placeholder)
- `_detect_technologies(url) -> list[str]` — Wappalyzer (placeholder)
- `_get_competitors_nearby(lead) -> list[dict]`
- `_generate_battlecard(lead, result) -> dict` — LLM-generated

**Additional models** (in business_analyzer.py):
- `SocialPresence` — instagram, vk, telegram, facebook booleans

**Shared models** (imported from lead_scorer.py):
- `WebsiteCheck`, `RatingInfo`, `AnalysisResult`

### Phase 3 Scope

Core structure + quick/medium tier logic. Deep tier tools are placeholders
that return empty results (require external API keys for SimilarWeb, Wappalyzer).
Battlecard generation deferred to SalesAgent integration.

### Edge Cases

- No URL → WebsiteCheck(exists=False), skip lighthouse/seo
- HTTP timeout → WebsiteCheck(exists=False)
- External API failure → graceful fallback to empty result
- Unknown tier → default to "quick"

### Files

| File | Change |
|------|--------|
| `src/core/business_analyzer.py` | NEW |
| `src/core/lead_scorer.py` | EXTEND AnalysisResult with social field |
| `tests/unit/test_business_analyzer.py` | NEW |
