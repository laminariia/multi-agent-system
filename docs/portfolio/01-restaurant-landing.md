# Лендинг ресторана с онлайн-бронированием

## Клиент
Семейный ресторан "Оливье", Москва, ул. Покровка. Работает с 2018 года, 80 посадочных мест, кухня — авторская русская и европейская. Владелец — Андрей Котов, управляет рестораном лично.

## Задача
Ресторан принимал бронирования только по телефону и через Instagram. В пиковые часы администратор не успевал отвечать на звонки, терялись до 15-20 заявок в неделю. Нужен был современный сайт с онлайн-бронированием, актуальным меню и галереей интерьера, который работал бы как основная точка входа для новых гостей.

## Решение
Разработал одностраничный лендинг с акцентом на визуал: полноэкранная галерея интерьера и блюд, анимированные переходы между секциями, адаптивная верстка для всех устройств. Система бронирования позволяет выбрать дату, время, количество гостей и зал (основной или VIP). Бронь мгновенно попадает в Telegram-бот администратора с кнопками "Подтвердить" / "Отклонить". Гость получает подтверждение на email и SMS. Меню загружается из Supabase — владелец обновляет позиции и цены через простую админ-панель без привлечения разработчика. Реализовал SEO-оптимизацию: мета-теги, schema.org разметка для ресторанов, Open Graph для соцсетей. Настроил Яндекс.Метрику и Google Analytics с целями на бронирование.

## Результат
- Бронирования через сайт: +40% за первый месяц (с 0 до 85 бронирований/мес)
- Загрузка главной страницы: 1.2 сек (Lighthouse Performance 94/100)
- Мобильный трафик: 68% посетителей — со смартфонов
- Отказы администратора от телефонных броней: -35%
- Отзыв клиента: "Сайт окупился за 3 недели. Гости бронируют в 2 часа ночи, а мы просто подтверждаем утром в Telegram" — Андрей Котов, владелец

## Стек
Next.js 14, Tailwind CSS, Framer Motion, Supabase (PostgreSQL + Auth + Storage), Telegram Bot API, Nodemailer, Vercel (хостинг), Яндекс.Метрика

## Сроки
3 недели (дизайн — 5 дней, разработка — 10 дней, тестирование и запуск — 4 дня)

---

### Текст для платформы (RU)

Разработал лендинг для семейного ресторана в Москве с полноценной системой онлайн-бронирования. Сайт включает: интерактивное меню с фильтрацией по категориям, галерею интерьера с lazy-loading, форму бронирования столиков с выбором зала и времени. Все заявки мгновенно приходят в Telegram-бот администратора с кнопками подтверждения. Гость получает уведомление на email. Владелец управляет меню через простую админку — не нужен программист для обновления цен и позиций. Стек: Next.js, Tailwind CSS, Supabase, Telegram Bot API. Результат: +40% бронирований через сайт за первый месяц, скорость загрузки 1.2 сек, Lighthouse 94/100. Мобильная версия адаптирована для 68% трафика со смартфонов. SEO-оптимизация с schema.org разметкой. Проект сдан за 3 недели под ключ. Если вашему заведению нужен сайт, который реально приносит гостей, а не просто "висит в интернете" — напишите, обсудим задачу.

### Platform Text (EN)

Built a landing page for a family restaurant in Moscow with a fully functional online reservation system. The site features an interactive menu with category filtering, a lazy-loaded interior gallery, and a table booking form with hall and time slot selection. All reservations are instantly pushed to the manager's Telegram bot with confirm/decline buttons. Guests receive email confirmations automatically. The owner manages menu items and prices through a simple admin panel — no developer needed for updates. Tech stack: Next.js, Tailwind CSS, Supabase, Telegram Bot API. Results: +40% bookings via the website in the first month, 1.2s page load time, Lighthouse Performance score 94/100. Mobile-responsive design handles 68% of traffic from smartphones. SEO-optimized with schema.org markup for restaurants. Delivered in 3 weeks, end-to-end. If your restaurant needs a website that actually brings in guests — not just sits on the internet — let's talk.
