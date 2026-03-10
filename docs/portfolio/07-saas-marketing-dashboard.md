# SaaS-дашборд для маркетинговых агентств

## Клиент
Маркетинговое агентство "MediaForce", Самара. Performance-маркетинг для малого и среднего бизнеса. 8 менеджеров, 35 клиентов на обслуживании, рекламные бюджеты от 50 000 до 800 000 руб/мес. Основатель и директор — Артём Жуков.

## Задача
Агентство использовало Google Sheets для отчётности: каждый менеджер вручную собирал данные из рекламных кабинетов, копировал в таблицу, строил графики. На отчёт одному клиенту уходило 2-3 часа, итого 80+ часов в месяц на всех. Клиенты жаловались: данные устаревшие, формат таблиц неудобный, нет мобильного доступа. Руководитель агентства хотел не просто дашборд, а SaaS-платформу: чтобы можно было подключать новых клиентов самостоятельно, без разработчика, и в перспективе — продавать подписку другим агентствам.

## Решение
Разработал SaaS-платформу с мультитенантной архитектурой и системой подписок. Три уровня доступа: суперадмин (агентство), менеджер (сотрудник), клиент (конечный пользователь). Каждый клиент получает персональный дашборд по брендированной ссылке (agency.platforma.ru/client-name) с логотипом агентства — white label. Платформа подключается к API рекламных систем: Яндекс.Директ, VK Реклама, Telegram Ads, Google Ads. Данные обновляются каждые 30 минут через фоновые задачи Celery. Дашборд показывает: расход по каналам, CPA, ROAS, конверсии, воронку лидов. Интерактивные графики с drill-down — от общей картины по месяцу до конкретного объявления. Реализовал систему алертов: если CPA превышает целевой на 30% или бюджет расходуется быстрее плана — менеджер получает уведомление в Telegram. Автоматическая генерация PDF-отчётов по расписанию (еженедельно, ежемесячно) с отправкой на email клиента. Биллинг через ЮKassa: 3 тарифных плана (Starter — 10 клиентов, Business — 30, Agency — без лимита), ежемесячная подписка с автопродлением. Панель суперадмина: управление клиентами, менеджерами, тарифами, просмотр MRR и churn.

## Результат
- 35 клиентов агентства подключены за первую неделю
- Время на отчётность: 5 часов/мес вместо 80+ (экономия 75 часов)
- 4 других агентства купили подписку в первые 2 месяца
- MRR платформы: 87 000 руб/мес (5 агентств на тарифах Business/Agency)
- Клиенты агентства: NPS вырос с 7.1 до 8.9 (удобство доступа к данным)
- Отзыв клиента: "Я хотел дашборд для своих клиентов, а получил продукт, который приносит деньги. 4 знакомых директора агентств увидели демо и подписались в тот же день" — Артём Жуков, основатель MediaForce

## Стек
Next.js 14, TypeScript, Tailwind CSS, Recharts, Python 3.12, FastAPI, PostgreSQL 16, Celery, Redis, YooKassa API (подписки), Puppeteer (PDF), Telegram Bot API (алерты), Docker, Nginx

## Сроки
10 недель (проектирование архитектуры и UI/UX — 8 дней, backend: API, мультитенантность, интеграции рекламных систем — 18 дней, frontend: дашборды, графики, админки — 14 дней, биллинг и подписки — 5 дней, PDF-генерация, алерты, white label — 5 дней, тестирование, деплой, онбординг — 8 дней)

---

### Текст для платформы (RU)

Разработал SaaS-платформу аналитических дашбордов для маркетинговых агентств. Мультитенантная архитектура: каждое агентство подключает своих клиентов, каждый клиент видит персональный дашборд с логотипом агентства (white label). Интеграции: Яндекс.Директ, VK Реклама, Telegram Ads, Google Ads — данные обновляются каждые 30 минут. Дашборд: расход, CPA, ROAS, конверсии, воронка лидов, drill-down до объявления. Алерты в Telegram при превышении CPA или перерасходе бюджета. PDF-отчёты автоматически по расписанию. Биллинг через YooKassa: 3 тарифа, подписка с автопродлением. Панель суперадмина: клиенты, менеджеры, MRR, churn. Результат: экономия 75 часов/мес на отчётности, 4 агентства купили подписку за 2 месяца, MRR 87 000 руб. Стек: Next.js, TypeScript, FastAPI, PostgreSQL, Celery, Redis, YooKassa, Recharts, Docker. Срок: 10 недель. Если вам нужна SaaS-платформа с подписками и мультитенантностью — напишите, обсудим.

### Platform Text (EN)

Built a SaaS analytics dashboard platform for marketing agencies. Multi-tenant architecture: each agency connects their clients, each client gets a personalized dashboard with the agency's branding (white label). Integrations with Yandex.Direct, VK Ads, Telegram Ads, and Google Ads — data refreshes every 30 minutes. Dashboard displays: spend by channel, CPA, ROAS, conversions, lead funnel with drill-down to individual ad level. Telegram alerts when CPA exceeds targets or budget is overspent. Automated PDF reports on weekly/monthly schedules sent to clients via email. Billing via YooKassa: 3 subscription tiers with auto-renewal. Super admin panel for managing clients, team members, MRR tracking, and churn analysis. Results: saved 75 hours/month on reporting, 4 agencies purchased subscriptions within 2 months, MRR reached $950/month. Tech stack: Next.js, TypeScript, FastAPI, PostgreSQL, Celery, Redis, YooKassa, Recharts, Docker. Delivered in 10 weeks. Available for SaaS platform development with subscriptions, multi-tenancy, and white-label features.
