# ⚖️ Legal & Compliance Guide

**Version:** 1.0  
**Jurisdiction:** Russia (primary), international clients  
**Disclaimer:** Consult a qualified lawyer for specific legal advice

---

## 📋 Overview

This document covers legal structure, taxation, and data compliance for operating a freelance automation business in Russia with international clients.

---

## 1. Legal Entity Options

### Comparison Table

| Option | Самозанятый | ИП | ООО |
|--------|-------------|-----|-----|
| **Registration** | 5 min (app) | 3-5 days | 2-4 weeks |
| **Annual revenue limit** | 2.4M ₽ | Unlimited | Unlimited |
| **Tax rate** | 4-6% | 6% (УСН) | 6-15% (УСН) |
| **Employees** | ❌ No | ✅ Yes | ✅ Yes |
| **Liability** | Personal | Personal | Limited |
| **Foreign clients** | ⚠️ Limited | ✅ Yes | ✅ Yes |
| **Currency control** | ⚠️ Complex | ⚠️ Some | ✅ Easier |

### Recommendation

```
Phase 1 (MVP): Самозанятый
- Quick start
- Low tax (4% for individuals, 6% for legal entities)
- Good for testing (<2.4M ₽/year)

Phase 2 (Scale): ИП на УСН 6%
- When revenue exceeds 2.4M ₽/year
- Or when hiring team members
- Better for currency operations

Phase 3 (Growth): ООО
- When need limited liability
- Multiple founders
- Significant revenue (>10M ₽/year)
```

---

## 2. Самозанятый (Self-Employed)

### Registration
1. Download "Мой налог" app
2. Register with passport
3. Link bank account
4. Done in 5 minutes

### Tax Rates
- **4%** — payments from physical persons
- **6%** — payments from legal entities and foreign clients

### Limitations
- Max revenue: **2.4M ₽/year** (~$26,000)
- No employees
- Cannot deduct expenses
- Some activities prohibited (resale, mining, etc.)

### Foreign Payments
```
⚠️ Important: Foreign currency payments are complex for самозанятый

Options:
1. Freelance platforms (Freelancer, Upwork) → Direct to Russian card
   - Platform handles conversion
   - Shows as domestic payment
   
2. Direct wire transfer → Russian bank
   - Requires currency control documents
   - Bank may request contract + invoice
   - 2-5 day processing
   
3. PayPal/Payoneer → Russian bank
   - Easier, but fees 3-5%
   - Still need to declare income
```

### Invoicing
Generate invoices in "Мой налог" app after each payment.

---

## 3. ИП (Individual Entrepreneur)

### Registration
1. Prepare documents (passport, INN, application)
2. Submit to налоговая or via Госуслуги
3. Pay госпошлина (800 ₽) or free via digital
4. Wait 3-5 business days

### Tax Options

| Regime | Rate | When to Use |
|--------|------|-------------|
| УСН "Доходы" | 6% | Low expenses (<60% of revenue) |
| УСН "Доходы минус расходы" | 15% | High expenses (hosting, APIs, etc.) |
| ПСН (Patent) | Fixed | Predictable income, specific activities |

### Recommended: УСН 6%
- Simple accounting
- Quarterly advance payments
- Annual declaration
- Страховые взносы: ~45,000 ₽/year (2024). **Проверить актуальные ставки на 2026 год**

### Foreign Currency Operations
```
Required for each foreign payment:

1. Contract with client (can be platform ToS)
2. Invoice (счёт)
3. Act of completed work (акт)
4. Bank паспорт сделки (if contract > $50,000)

Documents to keep:
- All contracts
- Invoice copies
- Payment confirmations
- Correspondence proving work done
```

---

## 4. Currency Control (Валютный контроль)

### Requirements for Foreign Payments

| Amount | Requirements |
|--------|--------------|
| < $200 | Minimal — bank may not ask |
| $200 - $3,000 | Invoice + contract excerpt |
| $3,000 - $50,000 | Full contract + invoice + supporting docs |
| > $50,000 | Паспорт сделки required |

### Recommended Setup
```
1. Open ИП account at bank with good forex support:
   - Тинькофф Бизнес
   - Точка
   - Модуль банк
   
2. Prepare template documents:
   - Service Agreement (English + Russian)
   - Invoice template
   - Act of completed work
   
3. For each project:
   - Generate invoice before work
   - Send to client for reference
   - After payment, issue акт
   - Keep all in folder for 5 years
```

---

## 5. Tax Calendar

### Самозанятый
| When | Action |
|------|--------|
| After each payment | Issue receipt in app (automatic) |
| 25th of next month | Auto-debit of tax |

### ИП на УСН
| When | Action |
|------|--------|
| Every quarter | Advance tax payment (25th) |
| 31 December | Fixed страховые взносы |
| 30 April | Annual УСН declaration |

---

## 6. GDPR & Data Compliance

### Data We Collect

| Data Type | Source | Legal Basis |
|-----------|--------|-------------|
| Client contact info | Freelance platforms | Contract performance |
| Project files | Clients | Contract performance |
| Email leads (Pipeline B) | Public sources + enrichment | Legitimate interest |

### GDPR Requirements (EU Clients)

```
For email outreach (Pipeline B):

1. Legal basis: Legitimate Interest (B2B cold email allowed)
2. Required in every email:
   - Company name
   - Physical address
   - Unsubscribe link (mandatory!)
   
3. Data retention:
   - Delete leads who don't respond after 3 emails
   - Delete all data after 12 months of no contact
   - Respond to erasure requests within 30 days
```

### Russian Data Law (152-ФЗ)

```
For Russian leads:

1. Must store personal data on Russian servers
   - Use Russian-hosted PostgreSQL
   - Or use Russian cloud (Yandex.Cloud, VK Cloud)
   
2. Consent required for:
   - Marketing emails to individuals
   - Storing personal data beyond service
   
3. Exceptions (no consent needed):
   - B2B communications
   - Contract performance
   - Public data (company emails on websites)
```

### Data Retention Policy

| Data | Retention | After Expiry |
|------|-----------|--------------|
| Active clients | Duration of relationship + 3 years | Archive |
| Completed projects | 5 years (tax requirement) | Delete |
| Email leads (no response) | 90 days | Delete |
| Email leads (responded) | 12 months | Delete if no deal |

---

## 7. Intellectual Property

### Code Generated by LLM

```
⚠️ Legal gray area

Current understanding:
- AI-generated code is NOT copyrightable (US court rulings)
- But modifications by human are copyrightable
- Client owns deliverables (standard freelance terms)

Our approach:
- All delivered code becomes client's property
- We retain right to use patterns/techniques
- No client data in future projects
```

### Using Open Source

```
For each dependency, check license:

✅ MIT, Apache 2.0, BSD → Safe for commercial use
⚠️ GPL, AGPL → Must open source derivative work
❌ Proprietary → Need license
```

---

## 8. Insurance & Liability

### Recommended Insurance

| Type | Coverage | Cost |
|------|----------|------|
| Professional Liability | Errors in delivered work | ~15,000 ₽/year |
| Cyber Insurance | Data breaches | ~30,000 ₽/year |

### Limiting Liability

Standard contract clause:
```
"Liability is limited to the total amount paid for services.
In no event shall liability exceed $10,000 or contract value,
whichever is less."
```

---

## 9. Banking Recommendations

### Best Banks for Freelance
| Bank | Pros | Cons |
|------|------|------|
| Тинькофф Бизнес | Easy forex, good app | Higher commissions |
| Точка | Low fees, API | Less forex support |
| Модуль | Good for ИП | Limited features |

### Required Accounts
1. **Business account (₽)** — for domestic operations
2. **Currency account ($)** — for foreign receipts
3. **Transit account** — auto-created for forex

---

## ✅ Checklist Before Launch

### Legal Structure
- [ ] Choose entity type (Самозанятый for MVP)
- [ ] Register with tax authority
- [ ] Open business bank account

### Documentation
- [ ] Prepare service agreement template
- [ ] Create invoice template
- [ ] Set up act template

### Compliance
- [ ] Add unsubscribe link to all emails
- [ ] Document data retention policy
- [ ] Set up data deletion schedules

### Tax
- [ ] Understand payment deadlines
- [ ] Set calendar reminders
- [ ] Keep all documents for 5 years
