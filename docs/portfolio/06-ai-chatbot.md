# AI-чатбот поддержки клиентов

## Клиент
Онлайн-школа **"SkyLearn"**, Москва. Ниша: EdTech, онлайн-образование. 15 000+ активных студентов, 120+ курсов по программированию, дизайну и маркетингу.

## Задача
Служба поддержки не справлялась с потоком обращений: 800+ тикетов в день, среднее время ответа 47 минут, NPS поддержки 6.2. Клиент хотел AI-чатбот для первой линии: ответы на FAQ, навигация по каталогу курсов, помощь с техническими проблемами (доступ к урокам, сертификаты, оплата), автоматическая эскалация на оператора при сложных кейсах.

## Решение
Разработал AI-чатбот на базе RAG-архитектуры с глубокой интеграцией в инфраструктуру клиента. Бот индексирует базу знаний (300+ статей, FAQ, описания курсов) через pgvector с автоматическим обновлением при изменении контента. Реализовал semantic search по базе знаний с порогом релевантности 0.82 для точных ответов. Чатбот понимает контекст диалога (до 10 сообщений) и умеет уточнять вопрос, если запрос неоднозначный. Интеграция с CRM клиента: бот подтягивает данные о студенте (купленные курсы, статус оплаты, прогресс) для персонализированных ответов. Реализовал умную эскалацию: бот передает оператору полный контекст диалога, категорию проблемы и предложенное решение. Виджет на React встроен в LMS-платформу клиента с адаптивным дизайном и поддержкой темной темы.

## Результат
- **70%** обращений закрываются без оператора (было 0%)
- Среднее время ответа: **3 секунды** (было 47 минут)
- NPS поддержки вырос с **6.2 до 8.4**
- Нагрузка на операторов снизилась на **65%** (сократили смену с 8 до 3 операторов)
- Точность ответов: **94%** (по результатам ежемесячного аудита)

> "Мы ожидали, что бот будет закрывать 40-50% обращений. 70% - это превзошло все прогнозы. Студенты пишут, что поддержка стала лучшей на рынке EdTech." -- Анна Соколова, COO SkyLearn

## Стек
Python 3.12, LangChain, OpenAI API (GPT-4o-mini + text-embedding-3-large), FastAPI, PostgreSQL + pgvector, React 18 (виджет), WebSocket, Docker, Sentry

## Сроки
6 недель: 1 неделя - проектирование и прототип, 2 недели - backend + RAG pipeline, 1 неделя - виджет и интеграция с LMS, 1 неделя - тестирование и обучение на реальных данных, 1 неделя - деплой и мониторинг.

---

### Текст для платформы (RU)
Разработал AI-чатбот поддержки для онлайн-школы с 15 000 студентов. Бот на базе RAG-архитектуры (LangChain + pgvector) индексирует 300+ статей базы знаний и отвечает на вопросы студентов за 3 секунды вместо 47 минут ожидания оператора. Реализовал semantic search с порогом 0.82, контекст диалога на 10 сообщений, интеграцию с CRM для персонализации (данные о курсах, оплате, прогрессе), умную эскалацию с передачей контекста оператору. Виджет на React встроен в LMS с адаптивным дизайном и темной темой. Результат: 70% обращений закрываются без оператора, NPS поддержки вырос с 6.2 до 8.4, нагрузка на операторов снизилась на 65%. Точность ответов 94% по ежемесячному аудиту. Стек: Python, LangChain, OpenAI API, FastAPI, PostgreSQL + pgvector, React, WebSocket, Docker. Срок реализации: 6 недель от ТЗ до продакшена. Готов реализовать аналогичное решение для вашего бизнеса.

### Platform Text (EN)
Built an AI-powered customer support chatbot for an EdTech platform with 15,000+ active students. The solution uses RAG architecture (LangChain + pgvector) to index 300+ knowledge base articles and deliver accurate answers in 3 seconds instead of 47-minute operator wait times. Implemented semantic search with 0.82 relevance threshold, 10-message conversation context, CRM integration for personalized responses (course data, payment status, progress tracking), and intelligent escalation that passes full context to human agents. The React widget is embedded directly into the client's LMS with responsive design and dark mode support. Results: 70% of inquiries resolved without human intervention, support NPS increased from 6.2 to 8.4, operator workload reduced by 65%, answer accuracy at 94% per monthly audit. Tech stack: Python, LangChain, OpenAI API, FastAPI, PostgreSQL + pgvector, React, WebSocket, Docker. Delivered in 6 weeks from requirements to production. Available for similar AI chatbot implementations for your business.
