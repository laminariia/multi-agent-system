# Бот автоматизации email-рассылок

## Клиент
Интернет-магазин **"HomeStyle"**, Москва. Ниша: E-commerce, товары для дома и интерьера. 25 000+ клиентов в базе, средний чек 4 500 руб, 8 000 заказов/мес. Email-маркетинг велся вручную через MailChimp с нерегулярными рассылками.

## Задача
Email-канал приносил менее 5% выручки при базе 25 000 контактов. Open rate 18%, recovery брошенных корзин не было, сегментация отсутствовала. Клиент хотел систему автоматических email-цепочек: welcome-серия для новых подписчиков, триггер на брошенную корзину, реактивация неактивных клиентов, персонализация на основе истории покупок и поведения на сайте, A/B тесты тем и контента писем.

## Решение
Спроектировал и разработал систему email-автоматизации с event-driven архитектурой. Backend отслеживает события пользователей через трекинг-пиксель и JavaScript SDK на сайте: просмотр товара, добавление в корзину, покупка, время на странице. События попадают в очередь Celery и запускают соответствующие цепочки. Реализовал 6 автоматических цепочек: welcome-серия (5 писем за 14 дней с прогрессивным вовлечением), брошенная корзина (3 письма: напоминание через 1 час, скидка 5% через 24 часа, последний шанс через 72 часа), post-purchase (благодарность + рекомендации на основе покупки), реактивация (для клиентов без покупок 60+ дней), день рождения (персональная скидка), товар снова в наличии. Персонализация через collaborative filtering: анализ истории покупок и просмотров для рекомендаций. A/B тестирование: автоматический сплит аудитории, отслеживание open rate и click rate, автовыбор победителя через 4 часа. Отправка через SendGrid с отслеживанием доставки, открытий и кликов. Админка на React: визуальный конструктор цепочек, шаблоны писем, аналитика по каждой кампании.

## Результат
- **Open rate: 32%** (было 18%)
- **Recovery брошенных корзин: 12%** (ранее 0%)
- **+15%** повторных покупок за 3 месяца
- Email-канал вырос до **14% выручки** (было менее 5%)
- ROI email-маркетинга: **4200%** (расходы на SendGrid 3 000 руб/мес, доход от канала 126 000 руб/мес)
- A/B тесты показали, что персонализированные темы дают **+27%** к open rate

> "Email всегда был для нас мертвым каналом. Мы отправляли раз в месяц акцию и получали 18% открытий. Сейчас это 32%, и каждое письмо приносит продажи. Особенно впечатлил recovery корзин - это деньги, которые мы просто теряли." -- Елена Миронова, директор по маркетингу HomeStyle

## Стек
Python 3.12, Celery, Redis (брокер очередей), PostgreSQL, SendGrid API, React 18 (админка), JavaScript SDK (трекинг), Docker, Flower (мониторинг Celery)

## Сроки
7 недель: 1 неделя - аудит текущего email-маркетинга, проектирование цепочек, 2 недели - backend (event tracking, Celery workers, логика цепочек), 1 неделя - интеграция SendGrid, шаблоны писем, 1 неделя - A/B тестирование и рекомендательный движок, 1 неделя - админка на React, 1 неделя - тестирование, деплой и калибровка цепочек на реальных данных.

---

### Текст для платформы (RU)
Разработал систему автоматических email-рассылок для интернет-магазина (25 000 клиентов, 8 000 заказов/мес). Event-driven архитектура: JS SDK отслеживает действия на сайте, события попадают в Celery и запускают цепочки. 6 автоматических цепочек: welcome (5 писем), брошенная корзина (напоминание, скидка 5%, последний шанс), post-purchase рекомендации, реактивация 60+ дней, день рождения, товар в наличии. Персонализация через collaborative filtering: рекомендации на основе истории покупок. A/B тесты: автосплит, автовыбор победителя через 4 часа. Админка с визуальным конструктором цепочек и аналитикой. Результат: open rate вырос с 18% до 32%, recovery корзин 12%, повторные покупки +15%, доля email в выручке выросла с 5% до 14%, ROI 4200%. Стек: Python, Celery, Redis, PostgreSQL, SendGrid, React. Срок: 7 недель. Готов настроить email-автоматизацию для вашего магазина.

### Platform Text (EN)
Built an email automation system for an e-commerce store (25,000 customers, 8,000 orders/month). Event-driven architecture: JavaScript SDK tracks user behavior on-site, events flow through Celery workers to trigger email sequences. Implemented 6 automated chains: welcome series (5 emails over 14 days), abandoned cart recovery (reminder, 5% discount, last chance), post-purchase recommendations, 60-day re-engagement, birthday offers, and back-in-stock alerts. Personalization powered by collaborative filtering based on purchase and browsing history. Built-in A/B testing with automatic audience split and winner selection after 4 hours. React admin panel with visual chain builder and per-campaign analytics. Delivery via SendGrid with full tracking (opens, clicks, bounces). Results: open rate increased from 18% to 32%, cart recovery at 12%, repeat purchases up 15%, email channel grew from 5% to 14% of revenue, 4,200% ROI. Tech stack: Python, Celery, Redis, PostgreSQL, SendGrid API, React. Delivered in 7 weeks. Available for email automation setup for e-commerce businesses.
