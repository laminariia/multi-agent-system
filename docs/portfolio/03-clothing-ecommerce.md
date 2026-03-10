# Интернет-магазин одежды

## Клиент
Бренд "NORD" (стилизовано NORD), Москва. Молодёжная streetwear-одежда: худи, футболки, аксессуары. Основан в 2022 году двумя дизайнерами, продавали через Instagram и маркетплейсы. Средний чек 4 500 руб, аудитория 18-30 лет. Основатель — Дмитрий Волков.

## Задача
Бренд вырос из Instagram-магазина и потерял контроль: комиссии маркетплейсов съедали маржу, не было аналитики по клиентам, невозможно было строить email-маркетинг. Нужен был собственный интернет-магазин с полным циклом: каталог с фильтрацией, корзина, оплата картой и через СБП, личный кабинет, интеграция с CDEK для доставки. Критично — мобильный опыт на уровне приложения, потому что 80% аудитории заходят с телефона.

## Решение
Разработал полноценный e-commerce на Next.js с серверным рендерингом для SEO и скорости. Каталог: фильтрация по размеру, цвету, категории, цене; сортировка по новизне и популярности. Карточка товара с зумом фото, таблицей размеров, отзывами покупателей. Корзина сохраняется между сессиями (localStorage + серверная синхронизация для авторизованных). Оплата: интеграция YooKassa (карты, СБП, Apple Pay). Доставка: автоматический расчёт стоимости и сроков через CDEK API по введённому адресу. Личный кабинет: история заказов, трекинг доставки в реальном времени, избранное, управление адресами. Админ-панель: управление товарами, заказами, промокодами, аналитика продаж по дням/неделям/месяцам. Фотографии товаров оптимизированы: WebP с fallback, responsive images, lazy loading. Изображения хранятся в S3-совместимом хранилище с CDN.

## Результат
- 500+ заказов в месяц через собственный магазин (ранее 100% через маркетплейсы)
- Конверсия: 3.2% (средняя по fashion e-commerce — 1.5-2%)
- Средний чек: 4 500 руб
- Скорость загрузки: 1.8 сек на мобильном (3G), Lighthouse 91/100
- Возврат покупателей: 28% делают повторный заказ в течение 60 дней
- Комиссии маркетплейсов: экономия ~180 000 руб/мес
- Отзыв клиента: "Наконец-то мы знаем своих клиентов по именам, а не по номерам заказов на Wildberries. Магазин выглядит как приложение" — Дмитрий Волков, основатель NORD

## Стек
Next.js 14, TypeScript, Tailwind CSS, PostgreSQL 16, Prisma ORM, YooKassa API, CDEK API, Amazon S3, Vercel, Яндекс.Метрика e-commerce

## Сроки
6 недель (дизайн UI/UX — 8 дней, разработка фронтенда — 14 дней, бэкенд и интеграции — 12 дней, тестирование и запуск — 8 дней)

---

### Текст для платформы (RU)

Разработал интернет-магазин для московского streetwear-бренда с нуля. Полный цикл: каталог с фильтрацией по размеру/цвету/категории, корзина с сохранением между сессиями, оплата через YooKassa (карты, СБП, Apple Pay), автоматический расчёт доставки CDEK по адресу. Личный кабинет: история заказов, трекинг в реальном времени, избранное. Админ-панель: управление товарами, промокодами, аналитика продаж. Мобильная версия на уровне приложения — 80% трафика со смартфонов, загрузка 1.8 сек на 3G. Результат: 500+ заказов/мес, конверсия 3.2% (вдвое выше средней по нише), экономия ~180 000 руб/мес на комиссиях маркетплейсов. Стек: Next.js, TypeScript, PostgreSQL, YooKassa, CDEK API, S3. Проект сдан за 6 недель. Если вашему бренду нужен свой интернет-магазин, который продаёт, а не просто показывает товары — давайте обсудим.

### Platform Text (EN)

Built a complete e-commerce store for a Moscow-based streetwear brand from scratch. Full purchase cycle: product catalog with size/color/category filters, persistent shopping cart, payment via cards and Apple Pay, automatic shipping cost calculation through CDEK API. Customer dashboard with order history, real-time delivery tracking, and wishlists. Admin panel for managing products, promo codes, and sales analytics. Mobile-first design handles 80% of traffic from smartphones with 1.8s load time on 3G. Results: 500+ orders/month, 3.2% conversion rate (double the fashion e-commerce average), saving ~$2,000/month in marketplace commissions. Tech stack: Next.js, TypeScript, PostgreSQL, Prisma, YooKassa payment gateway, CDEK API, S3 storage. Delivered in 6 weeks end-to-end. If your brand needs an online store that actually converts — not just displays products — reach out and let's discuss your requirements.
