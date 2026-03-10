# 📋 Platform Policies & Automation Guidelines

**Version:** 1.0  
**Purpose:** Define safe automation boundaries for each freelance platform

---

## 🚨 Critical Rule

> **NEVER fully automate bid submission on any platform.**  
> All platforms have anti-bot detection. The safest approach is HITL for all submissions.

---

## 📊 Platform Comparison

| Platform | Automation Level | Auto-Submit | API Available | Ban Risk |
|----------|------------------|-------------|---------------|----------|
| Freelancer.com | ⚠️ Semi-auto | With caution | ✅ Yes | Medium |
| Upwork | ❌ Monitor only | **FORBIDDEN** | ⚠️ GraphQL (limited) | **High** |
| FL.ru | ✅ RSS monitoring | N/A | ✅ RSS | Low |
| Kwork | ⚠️ Scraping | With caution | ❌ None | Medium |

---

## 1. Freelancer.com

### Official Policy
Freelancer.com provides an official API and allows automation for:
- ✅ Searching jobs
- ✅ Fetching job details
- ⚠️ Submitting bids (rate limited, requires OAuth)
- ✅ Project management
- ✅ Messaging

### Risks
- **Rate limits:** 30 bids/day, 100 API calls/hour
- **Pattern detection:** Identical proposals flagged
- **Account verification:** May require phone/ID verification

### Our Strategy
```
Level: SEMI-AUTOMATED with HITL

1. Scout Agent → Fetches jobs via API ✅ (safe)
2. Bid Agent → Generates proposal ✅ (safe)
3. HITL → Human reviews & clicks submit ⚠️ (required)
4. Bid Agent → Sends via API after approval ✅ (with caution)
```

### Ban Recovery
| Scenario | Action |
|----------|--------|
| Rate limit exceeded | Wait 24h, reduce frequency |
| Account suspension (temp) | Contact support, provide verification |
| Account ban (permanent) | Use backup account (different IP, email, payment) |
| IP ban | Switch to residential proxy |

---

## 2. Upwork (опционально / только manual)

> **NOTE:** Интеграция с Upwork является **опциональной** и работает исключительно в ручном режиме. Автоматическая отправка заявок ЗАПРЕЩЕНА.

### Official Policy
> ⚠️ **Upwork ToS Section 5.3:** "Automated access to the Upwork platform is prohibited without prior written consent."

**What's explicitly forbidden:**
- ❌ Auto-submitting proposals
- ❌ Scraping job listings at scale
- ❌ Using bots to bid
- ❌ Mass messaging clients

**What's tolerated (gray area):**
- ⚠️ GraphQL API for personal dashboard data
- ⚠️ Browser extensions for personal use
- ⚠️ Manual assisted tools

### Our Strategy
```
Level: MONITORING ONLY

1. Scout Agent → Monitors via GraphQL for NEW jobs only
2. Notification → Sends Telegram alert to human
3. Human → Manually opens Upwork, reviews, submits
4. NO automated submission — ever!
```

### Detection Methods Upwork Uses
- **Browser fingerprinting** (Canvas, WebGL, fonts)
- **Mouse movement analysis** (bot vs human patterns)
- **Timing analysis** (too fast = bot)
- **IP reputation** (datacenter IPs flagged)
- **Device fingerprinting**

### If Banned
| Type | Recovery |
|------|----------|
| Temporary suspension | Appeal, wait 7-14 days |
| Permanent ban | **No recovery** — create new identity |
| Device banned | New device + new IP + new payment |

> ⚠️ **Warning:** Creating new account after ban = permanent ban of all accounts

---

## 3. FL.ru

### Policy
FL.ru provides RSS feeds for job monitoring — fully allowed.

### Our Strategy
```
Level: FULL AUTOMATION (monitoring only)

1. Scout Agent → Parses RSS feed every 5 min
2. Filters → Match criteria
3. Notification → Human decides to bid
4. Human → Submits manually on FL.ru
```

### Rate Limits
- RSS: 10 requests/hour (sufficient)
- No submission API available

---

## 4. Kwork

### Policy
No official API. Web scraping is gray area.

### Our Strategy
```
Level: CAREFUL SCRAPING

1. Scout Agent → Playwright with stealth
2. Rate limit → 30 pages/hour max
3. Proxy rotation → Every 10 requests
4. Human-like delays → 3-10 seconds between actions
5. Submission → Manual only
```

### Anti-Detection Measures
```python
# Required for Kwork scraping
playwright_stealth_config = {
    "stealth": True,
    "timezone": "Europe/Moscow",
    "locale": "ru-RU",
    "viewport": {"width": 1920, "height": 1080},
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "proxy": "residential_rotating"
}
```

---

## 🔒 Multi-Account Strategy

### Why Multiple Accounts
- Redundancy if one account is banned
- Test different niches
- Scale beyond single-account limits

### Account Setup
| Account | Platform | Purpose | Status |
|---------|----------|---------|--------|
| Primary | Freelancer | Main bidding | Active |
| Backup | Freelancer | Standby | Warm (occasional use) |
| Primary | Kwork | Russian market | Active |
| Monitoring | Upwork | Job discovery | HITL only |

### Account Isolation Rules
1. **Separate IPs** — Each account on different proxy
2. **Separate emails** — Different email providers
3. **Separate payment** — Different cards/PayPal
4. **Separate devices** — Or different browser profiles
5. **No cross-linking** — Never mention other accounts

---

## 📊 Risk Matrix

| Action | Freelancer | Upwork | FL.ru | Kwork |
|--------|------------|--------|-------|-------|
| Monitor jobs | ✅ Safe | ⚠️ Careful | ✅ Safe | ⚠️ Careful |
| Auto-generate proposals | ✅ Safe | ✅ Safe | ✅ Safe | ✅ Safe |
| Auto-submit bids | ⚠️ Risky | ❌ Banned | N/A | ❌ Manual only |
| Mass messaging | ❌ Risky | ❌ Banned | ❌ Risky | ❌ Manual only |
| Use residential proxy | ✅ Required | ✅ Required | N/A | ✅ Required |

---

## 🚨 Incident Response

### If Account Flagged
```
1. STOP all automation immediately
2. Wait 24-48 hours
3. Log in manually, complete any verification
4. Resume with reduced frequency (50% of before)
5. Monitor for 1 week before returning to normal
```

### If Account Banned
```
1. DO NOT create new account immediately
2. Document what triggered ban
3. Wait minimum 30 days
4. Use completely new identity:
   - New email
   - New IP (residential, different region)
   - New payment method
   - New device/browser profile
5. Start with minimal activity
```

---

## ✅ Summary: Safe Automation Levels

| Platform | Scout | Bid Draft | Submit | Message |
|----------|-------|-----------|--------|---------|
| Freelancer | ✅ Auto | ✅ Auto | ⚠️ HITL | ⚠️ HITL |
| Upwork | ⚠️ Monitor | ✅ Auto | ❌ Manual | ❌ Manual |
| FL.ru | ✅ Auto | ✅ Auto | — | — |
| Kwork | ⚠️ Stealth | ✅ Auto | ❌ Manual | ❌ Manual |
