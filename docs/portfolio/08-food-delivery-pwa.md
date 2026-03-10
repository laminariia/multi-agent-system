# PWA доставки еды

## Клиент
Сервис доставки **"Вкусно.Быстро"**, Екатеринбург. Ниша: локальная доставка еды из ресторанов. 45 ресторанов-партнеров, 30 курьеров, зона покрытия - центральная часть города (радиус 8 км).

## Задача
Клиент работал через Instagram и WhatsApp: клиенты писали заказы в директ, менеджеры вручную передавали их ресторанам. Потери заказов, ошибки в адресах, невозможность масштабироваться. Нужно PWA-приложение: каталог ресторанов и блюд, корзина с кастомизацией, онлайн-оплата, отслеживание курьера на карте в реальном времени, push-уведомления о статусе заказа, работа при плохом интернете.

## Решение
Разработал полноценное PWA-приложение с тремя интерфейсами: клиентский (заказ), курьерский (доставка) и административный (управление). Клиентская часть: каталог с фильтрами (кухня, рейтинг, время доставки, диетические предпочтения), детальные карточки блюд с модификаторами (убрать лук, двойной сыр), корзина с промокодами, адрес на карте с подсказками DaData. Оплата через YooKassa: банковские карты, SBP, Apple Pay. Отслеживание курьера через WebSocket: координаты обновляются каждые 5 секунд, ETA пересчитывается на основе реальной скорости. Push-уведомления через Firebase Cloud Messaging: принял заказ, готовится, курьер выехал, доставлен. Service Workers кеширует меню и статические ресурсы для offline-просмотра. Курьерское приложение: список активных заказов, навигация через Leaflet, отметка о доставке с фото. Админка: управление меню, статистика заказов, настройка зон доставки на карте.

## Результат
- **2000+ заказов/мес** через PWA (ранее 300-400 через Instagram)
- **40%** пользователей установили PWA на домашний экран
- Время загрузки: **1.2 секунды** (Lighthouse Performance 92)
- Средний чек вырос на **23%** (благодаря рекомендациям и модификаторам)
- Потери заказов снизились с **~15%** до менее **1%**
- Offline-режим: меню и история заказов доступны без интернета

> "Раньше мы теряли заказы в переписках. Сейчас все автоматизировано, курьеры видят маршрут, клиенты следят за доставкой. За 3 месяца выросли с 45 до 70 ресторанов." -- Артем Краснов, основатель "Вкусно.Быстро"

## Стек
React 18, PWA (Service Workers, Web App Manifest), Node.js, Express, Socket.io, YooKassa API, Leaflet + OpenStreetMap, Firebase Cloud Messaging, PostgreSQL, DaData API, Docker, Nginx

## Сроки
10 недель: 2 недели - проектирование, UI/UX дизайн, 3 недели - backend (API, WebSocket, интеграция с YooKassa и DaData), 3 недели - frontend (клиентское PWA, курьерское приложение, админка), 1 неделя - тестирование и оптимизация, 1 неделя - деплой, настройка push-уведомлений и онбординг ресторанов.

---

### Текст для платформы (RU)
Разработал PWA-приложение для сервиса доставки еды в Екатеринбурге (45 ресторанов, 30 курьеров). Три интерфейса: клиентский (каталог, корзина, оплата, отслеживание), курьерский (заказы, навигация, фотоотчет) и админка (меню, статистика, зоны доставки). Каталог с фильтрами и модификаторами блюд, промокоды, адрес через DaData. Оплата YooKassa (карты, SBP, Apple Pay). Курьер на карте в реальном времени через WebSocket (обновление каждые 5 сек), push-уведомления через Firebase. Service Workers для offline-работы: меню доступно без интернета. Результат: рост с 400 до 2000+ заказов/мес, 40% установили PWA, средний чек +23%, потери заказов снизились с 15% до 1%. Lighthouse Performance 92, загрузка 1.2 сек. Стек: React, PWA, Node.js, Socket.io, YooKassa, Leaflet, PostgreSQL. Срок: 10 недель. Готов разработать PWA для вашего сервиса доставки.

### Platform Text (EN)
Built a full-featured PWA for a local food delivery service (45 restaurants, 30 couriers). Three interfaces: customer (catalog, cart, payment, live tracking), courier (orders, navigation, delivery proof), and admin (menu management, analytics, delivery zones). Features include smart filters, dish customizers, promo codes, address autocomplete, and online payments (cards, SBP, Apple Pay via YooKassa). Real-time courier tracking via WebSocket with 5-second updates and dynamic ETA. Push notifications through Firebase Cloud Messaging for order status updates. Service Workers cache menus and static assets for offline browsing. Results: orders grew from 400 to 2,000+/month, 40% of users installed the PWA, average order value increased 23%, order loss dropped from 15% to under 1%. Lighthouse Performance score: 92, load time: 1.2s. Tech stack: React, PWA, Node.js, Socket.io, YooKassa, Leaflet, PostgreSQL, Docker. Delivered in 10 weeks. Available for PWA development for delivery and marketplace platforms.
