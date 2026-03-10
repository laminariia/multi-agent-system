# Дашборд аналитики для бизнеса

## Клиент
Маркетинговое агентство **"DataPulse"**, Москва. Ниша: B2B, performance-маркетинг. 15 клиентов на постоянном обслуживании, бюджеты от 200 000 до 2 000 000 руб/мес на рекламу.

## Задача
Агентство тратило 20+ часов в месяц на ручной сбор данных из разных рекламных кабинетов и формирование отчетов в Google Sheets. Клиенты жаловались на устаревшие данные и задержки. Нужен был единый дашборд: данные из Яндекс.Метрики, Google Analytics 4, VK Ads, Telegram Ads в одном месте, автоматическое обновление каждые 15 минут, генерация PDF-отчетов по расписанию.

## Решение
Спроектировал и разработал аналитическую платформу с мультитенантной архитектурой. Каждый клиент агентства получает персональный дашборд с доступом по ссылке и PIN-коду. Backend собирает данные через API рекламных платформ: Яндекс.Метрика (API отчетов), Google Analytics 4 (Data API), VK Ads (маркетинговый API), Telegram Ads (MTProto API). Данные нормализуются в единую модель: показы, клики, конверсии, расход, CPA, ROAS по каждому каналу и кампании. Реализовал интерактивные графики с drill-down: от общей картины до конкретной кампании и объявления. Cron jobs обновляют данные каждые 15 минут, при аномалиях (расход +50% за день, CTR падение >30%) отправляются алерты в Telegram менеджеру. PDF-отчеты генерируются через Puppeteer по расписанию (еженедельно и ежемесячно) и отправляются на email клиента.

## Результат
- **15 клиентов** агентства подключены в первый месяц
- Экономия **20 часов/мес** на ручных отчетах
- Клиенты видят данные в реальном времени (обновление каждые 15 минут)
- **3 новых клиента** пришли в агентство после демо дашборда
- Среднее время формирования PDF-отчета: **12 секунд**

> "Дашборд стал нашим главным конкурентным преимуществом. Клиенты впервые видят все каналы рядом и понимают, куда уходит бюджет. Это инструмент, который продает наши услуги за нас." -- Дмитрий Волков, CEO DataPulse

## Стек
Next.js 14, TypeScript, Recharts, Tailwind CSS, Node.js, PostgreSQL, Prisma ORM, cron jobs (node-cron), Puppeteer (PDF-генерация), Vercel, Telegram Bot API (алерты)

## Сроки
8 недель: 1 неделя - проектирование и UI/UX прототип, 3 недели - backend (API-интеграции, нормализация данных, cron), 2 недели - frontend (дашборды, графики, фильтры), 1 неделя - PDF-генерация и алерты, 1 неделя - тестирование, деплой и онбординг клиентов.

---

### Текст для платформы (RU)
Разработал аналитическую платформу для маркетингового агентства с 15 клиентами. Единый дашборд объединяет данные из Яндекс.Метрики, Google Analytics 4, VK Ads и Telegram Ads с автообновлением каждые 15 минут. Мультитенантная архитектура: каждый клиент видит только свои данные, доступ по ссылке и PIN-коду. Интерактивные графики (Recharts) с drill-down от общей картины до конкретного объявления. Данные нормализуются в единую модель: показы, клики, конверсии, расход, CPA, ROAS. Алерты в Telegram при аномалиях (скачок расхода, падение CTR). PDF-отчеты генерируются автоматически через Puppeteer и отправляются клиентам по расписанию. Результат: экономия 20 часов/мес на отчетах, 3 новых клиента пришли после демо. Стек: Next.js, TypeScript, Recharts, Node.js, PostgreSQL, Puppeteer, Vercel. Срок: 8 недель. Готов разработать аналогичный дашборд для вашего агентства или бизнеса.

### Platform Text (EN)
Developed a multi-tenant analytics platform for a marketing agency managing 15 clients. The dashboard aggregates data from Yandex.Metrica, Google Analytics 4, VK Ads, and Telegram Ads with automatic updates every 15 minutes. Each client gets a personalized dashboard with link + PIN access. Built interactive charts (Recharts) with drill-down capability from overview to individual ad-level metrics. All data is normalized into a unified model: impressions, clicks, conversions, spend, CPA, and ROAS across channels. Implemented Telegram alerts for anomalies (50%+ spend spike, 30%+ CTR drop) and automated PDF report generation via Puppeteer on weekly/monthly schedules. Results: 20 hours/month saved on manual reporting, 3 new clients acquired after dashboard demos, PDF reports generated in 12 seconds. Tech stack: Next.js, TypeScript, Recharts, Node.js, PostgreSQL, Prisma ORM, Puppeteer, Vercel. Delivered in 8 weeks from design to production. Available for custom analytics dashboard development for agencies and businesses.
