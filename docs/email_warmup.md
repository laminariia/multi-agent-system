# Email Warm-up Protocol (Pipeline B)

> Без warm-up 90%+ писем попадут в спам!

## Timeline

| Период | Emails/day | Engagement | Действия |
|--------|------------|------------|----------|
| Week 1-2 | 5-10 | 80% opens | Только warm-up сеть |
| Week 3 | 20 | 70% | Первые тесты |
| Week 4 | 30 | 65% | Анализ inbox placement |
| Week 5-6 | 50 | 60% | Production ready |

## Инфраструктура

```yaml
domains:
  - primary: yourbrand.com         # НЕ для cold outreach!
  - outreach_1: out1-yourbrand.com # Warmup отдельно
  - outreach_2: out2-yourbrand.com # Backup

dns_records:
  spf: "v=spf1 include:_spf.google.com ~all"
  dkim: enabled
  dmarc: "v=DMARC1; p=quarantine"

warmup_service: "Instantly.ai"  # Встроенный warmup

monitoring:
  - Google Postmaster Tools
  - MXToolbox blacklist check
  - Bounce rate alerts (>2% -> pause)
```
