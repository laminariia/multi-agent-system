# Research: Инструменты для автоматического создания визуалов портфолио

> Дата: 2026-02-25
> Контекст: 10 проектов в `docs/portfolio/`, нужны визуалы для Kwork, FL.ru, Fiverr, Freelancer.com, YouDo, Telegram
> Типы проектов: лендинги, Telegram-боты, CRM, SaaS-дашборд, PWA, AI-агенты, no-code автоматизация

---

## Сводная таблица инструментов

| Инструмент | Тип | Цена | CLI/API | Время/проект | Качество | Для нас |
|-----------|-----|------|---------|-------------|---------|---------|
| **Playwright (свой)** | Screenshot | Бесплатно | Python API | 1-2 мин | Отличное | **РЕКОМЕНДОВАН** |
| **shot-scraper** | Screenshot CLI | Бесплатно | CLI (pip) | 30 сек | Отличное | Хорош для batch |
| **CSS Device Mockups** | Обертка | Бесплатно | HTML/CSS | +1 мин | Отличное | **РЕКОМЕНДОВАН** |
| **moqq** | Device mockup | Бесплатно | CLI (npm) | 30 сек | Среднее | Альтернатива |
| **Pillow composite** | Image processing | Бесплатно | Python | 1 мин | Хорошее | Нет зависимостей |
| **Pencil.dev** | AI Design | Бесплатно (beta) | VS Code ext | 5-10 мин | Отличное | Для новых проектов |
| **v0.dev** | AI UI gen | Бесплатно (200 кр/мес) | Web | 3-5 мин | Высокое | Генерация компонентов |
| **Dynamic Mockups** | Device mockup API | Бесплатно (3/день) | REST API | 1 сек | Профессиональное | Лимит 3/день |
| **Mockuuups Studio** | Device mockup API | Free dev plan | REST API | 1-2 сек | Профессиональное | Платный для объема |
| **shots.so** | Device mockup | Бесплатно | Только Web | 2-3 мин | Профессиональное | Ручной |
| **DeviceShots** | Device mockup | Бесплатно | Только Web | 2-3 мин | Хорошее | Ручной |
| **Screely** | Browser frame | Бесплатно | Web + ext | 1 мин | Хорошее | Ручной |
| **DALL-E 3** | AI generation | $0.04/img | API | 15-30 сек | Среднее (UI) | Текст читаем, но не пиксельно |
| **Midjourney** | AI generation | $10/мес | Discord | 1-2 мин | Среднее (UI) | Текст нечитаем |

---

## 1. Playwright (уже есть в проекте)

### Текущее состояние
- `src/browser/stealth.py` -- `StealthPage.screenshot(**kwargs)` проксирует `page.screenshot()`
- Playwright 1.58.0 + playwright-stealth 2.0.2 установлены в venv
- `StealthConfig`: viewport 1920x1080, headless, proxy support

### Возможности для портфолио

**Скриншот HTML через `page.setContent()`:**
```python
# Можно рендерить HTML без сервера
await page.set_content(html_string)
await page.screenshot(path="output.png", full_page=True)
```

**Retina-качество:**
```python
context = await browser.new_context(
    viewport={"width": 1440, "height": 900},
    device_scale_factor=2,  # Retina 2x
)
# Выход: 2880x1800 px изображение
```

**Device emulation (мобильные):**
```python
# Встроенные пресеты
iphone = playwright.devices["iPhone 14 Pro"]
context = await browser.new_context(**iphone)
```

**Full page screenshot:**
```python
await page.screenshot(full_page=True, type="png")
```

### Подход: HTML-шаблоны + Playwright screenshot

Для каждого проекта:
1. Создать HTML-страницу (лендинг/дашборд/бот-интерфейс) с реалистичными данными
2. Рендерить через `page.setContent(html)` -- сервер не нужен
3. Снять скриншот desktop (1440x900 @2x) + mobile (390x844 @2x)
4. Обернуть в device mockup (см. ниже)

**Плюсы:**
- Уже установлен, код есть, 0 дополнительных зависимостей
- Пиксельно точные скриншоты, никаких артефактов
- Поддержка анимаций (wait for animation), lazy-load, dark mode
- Device emulation для 143+ устройств

**Минусы:**
- Нужно создать HTML для каждого проекта (1-3 часа на проект)
- Для CRM/дашбордов с auth нужны фейковые данные
- Для ботов (Telegram) нужен mockup интерфейса Telegram

**Время:** ~2-3 часа на проект (HTML + screenshot + обработка)

### Источники
- [Playwright Screenshots docs](https://playwright.dev/docs/screenshots)
- [Playwright Emulation docs](https://playwright.dev/docs/emulation)
- [Rendering screenshots with Playwright](https://screenshotone.com/blog/how-to-render-screenshots-with-playwright/)

---

## 2. shot-scraper (CLI-обертка над Playwright)

Simon Willison's CLI tool. Установка: `pip install shot-scraper && shot-scraper install`.

```bash
# Один скриншот
shot-scraper https://example.com -o output.png --width 1440 --height 900

# Из YAML batch файла
shot-scraper multi shots.yml

# Конкретный CSS-селектор
shot-scraper https://example.com --selector ".hero-section" -o hero.png

# С JavaScript (подождать загрузку)
shot-scraper https://example.com --javascript "document.querySelector('.cookie-banner').remove()"

# Локальный HTML файл
shot-scraper file:///path/to/file.html -o output.png
```

**Плюсы:**
- Минимум кода, batch через YAML
- Поддержка CSS-селекторов, JS-инъекций, auth-контекстов
- Идеален для CI/CD pipeline (скриншоты обновляются автоматически)

**Минусы:**
- Нет device mockup обертки -- только чистый скриншот
- Дополнительная зависимость (хотя Playwright уже есть)

**Время:** ~30 сек на скриншот, ~5 мин настройка batch

### Источники
- [shot-scraper GitHub](https://github.com/simonw/shot-scraper)
- [shot-scraper PyPI](https://pypi.org/project/shot-scraper/)

---

## 3. Device Mockup генераторы

### 3.1 CSS Device Mockups (для Playwright рендера)

**Лучший подход для нас**: создать HTML с CSS device frame, вставить скриншот, рендерить через Playwright.

Доступные библиотеки:

| Библиотека | Устройства | Лицензия | URL |
|-----------|-----------|---------|-----|
| **devices.css** | MacBook Pro, iPhone X/8, iPad Pro, Galaxy S8, Surface Studio | MIT | [CSS-Device-Mockups](https://github.com/callmenick/CSS-Device-Mockups) |
| **html5-device-mockups** | iPhone, iPad, MacBook, iMac, Apple Watch | MIT | [GitHub](https://github.com/pixelsign/html5-device-mockups) |
| **Flowbite Device Mockups** | MacBook, iPhone, iPad, Android, Desktop | MIT | [Flowbite docs](https://flowbite.com/docs/components/device-mockups/) |
| **Preline Devices** | Аналогичный набор, Tailwind CSS | MIT | [Preline docs](https://preline.co/docs/devices.html) |
| **device-mockup** Web Component | Laptop, phone, tablet | MIT | [CSS Script](https://www.cssscript.com/device-mockup-component/) |

**Workflow:**
```html
<!-- mockup-template.html -->
<div class="device-macbook">
  <div class="screen">
    <img src="screenshot-desktop.png" />
  </div>
</div>
```
1. Playwright: снимаем скриншот проекта
2. Вставляем в HTML-шаблон с CSS device frame
3. Playwright: рендерим шаблон в финальное изображение

**Плюсы:**
- Бесплатно, open-source, никаких API-ключей
- Полный контроль над внешним видом
- Можно комбинировать устройства (MacBook + iPhone рядом)
- Рендерится идеально через Playwright

**Минусы:**
- Нужно подготовить HTML-шаблоны (1 раз)
- CSS mockups выглядят хорошо, но не фотореалистично (3D тени, блики)

### 3.2 moqq (Node.js CLI)

```bash
npm i -g moqq
moqq-up --pc screenshot.png --iphone_x mobile.png -w 1200 -h 800 -b "#f5f5f5" -o mockup.png
```

**Плюсы:**
- Одна команда = готовый mockup
- PC + iPhone + iPad в одном изображении

**Минусы:**
- Только PC mockup (не MacBook), ограниченный набор устройств
- Использует Jimp (Node.js) -- отдельная зависимость
- Качество среднее, устаревший дизайн фреймов
- Не обновлялся давно

### 3.3 Dynamic Mockups (API)

```bash
curl -X POST https://api.dynamicmockups.com/v1/renders \
  -H "x-api-key: YOUR_KEY" \
  -d '{"mockup_uuid": "...", "smart_objects": [{"uuid": "...", "image_url": "..."}]}'
```

**Плюсы:**
- Фотореалистичные 3D mockups (MacBook, iPhone, iPad в реальных сценах)
- < 1 сек на рендер
- Бесплатный API-ключ

**Минусы:**
- **3 рендера/день бесплатно** -- для 10 проектов x 3 устройства = 30 рендеров, ~10 дней на бесплатном плане
- Pro план: от $9/мес
- Зависимость от внешнего API

### 3.4 shots.so

- Бесплатный web-инструмент для mockups
- **Нет API** -- только ручная работа через браузер
- Качество отличное, много шаблонов
- Время: 2-3 минуты на mockup вручную

### 3.5 Mockuuups Studio (API)

- 5000+ шаблонов, автоматический screenshot по URL
- Free developer plan для тестирования
- Платный для production использования
- API берет URL и сам делает скриншот + вставляет в mockup

### Источники
- [CSS-Device-Mockups](https://github.com/callmenick/CSS-Device-Mockups)
- [html5-device-mockups](https://github.com/pixelsign/html5-device-mockups)
- [Flowbite Device Mockups](https://flowbite.com/docs/components/device-mockups/)
- [moqq npm](https://www.npmjs.com/package/moqq)
- [Dynamic Mockups API](https://dynamicmockups.com/mockup-generator-api/)
- [shots.so](https://shots.so/)
- [Mockuuups Studio API](https://mockuuups.studio/api/)
- [Device Shots](https://deviceshots.com/)

---

## 4. Pencil.dev

### Что это
AI-native design canvas, встроенный в VS Code/Cursor. Хранит дизайны как `.pen` файлы в git.

### MCP интеграция
Pencil работает как MCP-сервер для Claude Code:
- Claude читает координаты, токены, структуру из `.pen` файла
- Генерирует pixel-perfect React/HTML/CSS код
- Двусторонняя связь: дизайн <-> код

### Для портфолио
- Можно описать текстом: "Создай дашборд SaaS-платформы с графиками расходов по каналам"
- Pencil сгенерирует визуальный макет
- Экспорт в HTML/React, затем Playwright screenshot

**Плюсы:**
- Бесплатно (beta)
- AI-генерация по текстовому описанию
- Файлы в git, версионируются
- MCP-интеграция с Claude Code

**Минусы:**
- Beta -- может быть нестабильно
- Генерирует дизайн компонентов, не полных страниц
- Требует VS Code/Cursor
- Не заменяет Playwright для скриншотов

**Вердикт:** Полезен для **новых проектов** (Design Agent), но для текущих 10 портфолио-проектов -- overengineering.

### Источники
- [Pencil.dev](https://www.pencil.dev/)
- [Pencil AI Integration docs](https://docs.pencil.dev/getting-started/ai-integration)
- [Pencil VS Code extension](https://marketplace.visualstudio.com/items?itemName=highagency.pencildev)
- [Pencil.dev Review 2026](https://invernessdesignstudio.com/pencil-dev-review-the-complete-guide-to-ai-vibe-coding-for-2026)

---

## 5. AI-подходы

### 5.1 v0.dev (Vercel)

Генерирует React-компоненты из текстового описания. shadcn/ui + Tailwind CSS.

**Для портфолио:**
1. Описать проект: "Dashboard for marketing agency with charts showing ad spend by channel"
2. v0 генерирует React-компонент с реалистичными данными
3. Рендерить через Playwright или экспортировать из v0

**Плюсы:**
- Бесплатно (200 credits/мес, хватит на 10 проектов)
- Высокое качество UI, современный дизайн
- shadcn/ui -- тот же стек что в нашем dashboard
- Можно итерировать промптами

**Минусы:**
- Нет API для автоматизации -- только web-интерфейс
- Генерирует React, нужна среда для рендера
- Не все типы проектов подходят (Telegram-боты, no-code)

**Вердикт:** Хорош для дашбордов (07-SaaS, 05-CRM), лендингов (01), e-commerce (03, 10). Не подходит для ботов и автоматизаций.

### 5.2 DALL-E 3

**Для портфолио:** Может генерировать изображения UI по описанию.

```
Prompt: "Professional SaaS analytics dashboard on a MacBook screen, showing marketing metrics: CPA, ROAS, conversion funnel. Dark theme, modern design, Tailwind CSS style. Photorealistic mockup."
```

**Плюсы:**
- API ($0.04/изображение), полная автоматизация
- Хорошо рендерит текст (в отличие от Midjourney)
- Быстро (15-30 сек)

**Минусы:**
- UI выглядит "почти реально", но при увеличении видны артефакты
- Нельзя точно контролировать контент (какие данные на графиках)
- Для фрилансера это может выглядеть как обман -- скриншоты не настоящие
- $0.04 x 30 изображений = ~$1.20 (дешево)

**Вердикт:** НЕ рекомендуется. Риск потери доверия клиентов. AI-сгенерированные "скриншоты" легко отличить при близком рассмотрении.

### 5.3 Midjourney

**Минусы сверх DALL-E:**
- Текст нечитаем -- критично для UI скриншотов
- Нет API -- только Discord
- $10/мес минимум
- Еще менее контролируемый результат

**Вердикт:** НЕ рекомендуется для UI/скриншотов. Подходит только для абстрактных иллюстраций.

### Источники
- [v0.dev](https://v0.dev/)
- [v0.dev FAQ](https://v0.dev/faq)
- [Midjourney for UI design](https://blog.logrocket.com/ux-design/using-midjourney-generate-ui-designs/)

---

## 6. Pillow (Python, без зависимостей)

Если Pillow не установлен (`pip install Pillow`), можно composite скриншот в device frame:

```python
from PIL import Image

# Загружаем фрейм устройства (MacBook, заранее подготовленный PNG с прозрачностью)
frame = Image.open("macbook-frame.png")
screenshot = Image.open("screenshot.png")

# Ресайз скриншота под экран
screenshot = screenshot.resize((1280, 800))

# Вставляем в нужную позицию
frame.paste(screenshot, (145, 78))  # координаты экрана в фрейме
frame.save("mockup.png")
```

**Плюсы:**
- Минимум зависимостей
- Полный контроль
- Работает оффлайн

**Минусы:**
- Нужны PNG-фреймы устройств (найти/нарисовать)
- Ручная калибровка координат
- Нет 3D-эффектов, перспективы

**Вердикт:** Рабочий fallback, но CSS device mockups + Playwright дают лучший результат с меньшими усилиями.

---

## 7. Рекомендуемый подход

### Стратегия: Playwright + CSS Mockups + v0.dev (гибрид)

Три уровня в зависимости от типа проекта:

### Уровень 1: HTML-шаблон + Playwright (для всех 10 проектов)

Создаем минимальные HTML-страницы с реалистичными данными, рендерим через Playwright:

| Проект | Тип HTML | Сложность создания HTML |
|--------|---------|----------------------|
| 01 Лендинг ресторана | Полный лендинг (hero + menu + booking) | Средняя (2-3 ч) |
| 02 Telegram-бот | Mockup Telegram-интерфейса (HTML) | Легкая (1 ч) |
| 03 Интернет-магазин | Каталог + карточка товара | Средняя (2 ч) |
| 04 AI-рекрутинг | Дашборд с таблицей кандидатов | Средняя (2 ч) |
| 05 CRM автосервиса | CRM интерфейс с сайдбаром | Средняя (2-3 ч) |
| 06 AI-чатбот | Виджет чата на странице | Легкая (1 ч) |
| 07 SaaS-дашборд | Дашборд с графиками | Сложная (3-4 ч) |
| 08 PWA доставки | 3 интерфейса (клиент, курьер, админ) | Сложная (3-4 ч) |
| 09 No-code автоматизация | Схема n8n workflow | Легкая (1 ч) |
| 10 AI e-commerce | Виджет чата + каталог | Средняя (2 ч) |

**Итого создание HTML:** ~20-25 часов

### Уровень 2: v0.dev для ускорения (бесплатно)

Для проектов 01, 03, 04, 05, 07, 08, 10 -- использовать v0.dev для генерации React-компонентов:
1. Описать проект промптом
2. v0 генерирует компонент с реалистичными данными
3. Скопировать HTML/CSS, рендерить через Playwright

**Экономия:** ~50% времени на создание HTML (10-12 часов вместо 20-25)

### Уровень 3: CSS Device Mockups (финальная обертка)

После получения скриншотов -- обернуть в device mockups:

```
portfolio-mockup-template.html:
┌──────────────────────────────────────────┐
│  [MacBook mockup]     [iPhone mockup]    │
│  ┌──────────────┐     ┌─────┐            │
│  │  screenshot  │     │ mob │            │
│  │  desktop.png │     │ ile │            │
│  │              │     │.png │            │
│  └──────────────┘     └─────┘            │
│         Gradient/solid background        │
└──────────────────────────────────────────┘
```

Один HTML-шаблон, переиспользуется для всех проектов. Меняем только скриншоты и фон.

### Pipeline автоматизации (для Portfolio Agent)

```python
async def generate_portfolio_visuals(project_id: str, html_content: str):
    """Полный pipeline: HTML -> screenshot -> mockup -> save"""

    browser = StealthBrowser(StealthConfig(headless=True))
    await browser.launch()

    # 1. Desktop screenshot
    ctx_desktop = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=2,
    )
    page = await ctx_desktop.new_page()
    await page.set_content(html_content)
    await page.wait_for_load_state("networkidle")
    desktop_bytes = await page.screenshot(full_page=False, type="png")

    # 2. Mobile screenshot
    ctx_mobile = await browser.new_context(
        **playwright.devices["iPhone 14 Pro"],
        device_scale_factor=3,
    )
    page_mobile = await ctx_mobile.new_page()
    await page_mobile.set_content(html_content)
    mobile_bytes = await page_mobile.screenshot(full_page=False, type="png")

    # 3. Device mockup (CSS template)
    mockup_html = MOCKUP_TEMPLATE.format(
        desktop_img=base64.b64encode(desktop_bytes).decode(),
        mobile_img=base64.b64encode(mobile_bytes).decode(),
        bg_color=project_config["bg_color"],
    )
    ctx_mockup = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        device_scale_factor=2,
    )
    page_mockup = await ctx_mockup.new_page()
    await page_mockup.set_content(mockup_html)
    mockup_bytes = await page_mockup.screenshot(type="png")

    # 4. Save
    save_path = f"portfolio/projects/{project_id}/screenshots/"
    Path(save_path).mkdir(parents=True, exist_ok=True)
    Path(f"{save_path}/desktop.png").write_bytes(desktop_bytes)
    Path(f"{save_path}/mobile.png").write_bytes(mobile_bytes)
    Path(f"{save_path}/mockup.png").write_bytes(mockup_bytes)

    await browser.close()
```

---

## 8. Самый быстрый путь (прямо сейчас, без подписок)

### Минимум усилий, максимум результата:

**Шаг 1 (30 мин):** Установить shot-scraper, скачать CSS-Device-Mockups
```bash
pip install shot-scraper
shot-scraper install
git clone https://github.com/pixelsign/html5-device-mockups.git /tmp/device-mockups
```

**Шаг 2 (2 ч):** Для каждого из 10 проектов -- написать минимальный HTML
- Использовать v0.dev для генерации (бесплатно, 200 credits)
- Для простых: Tailwind CDN + inline HTML
- Для ботов: HTML mockup Telegram-чата (шаблонов полно на CodePen)

**Шаг 3 (1 ч):** Batch скриншоты через shot-scraper
```yaml
# shots.yml
- url: file:///path/to/01-restaurant.html
  output: portfolio/01/desktop.png
  width: 1440
  height: 900
  device_scale_factor: 2
- url: file:///path/to/01-restaurant.html
  output: portfolio/01/mobile.png
  width: 390
  height: 844
  device_scale_factor: 3
# ... повторить для всех проектов
```

**Шаг 4 (1 ч):** Обернуть в mockups
- HTML шаблон с CSS device frame
- Playwright рендер финального mockup
- ИЛИ: shots.so вручную (2-3 мин на проект = 30 мин на все)

**Итого:** ~4-5 часов на все 10 проектов

### Самый-самый быстрый путь (2 часа):
1. shots.so (web) -- загрузить скриншоты, выбрать MacBook frame, скачать
2. Для мобильных -- MockUPhone (бесплатно, web)
3. Результат: 10 desktop mockups + 10 mobile mockups за 2 часа ручной работы

---

## 9. Что НЕ стоит делать

1. **AI-генерация "фейковых" скриншотов** (DALL-E/Midjourney) -- клиенты на фрилансе внимательны, подделку заметят, репутационный урон
2. **Figma API** -- требует Pro-подписку ($12/мес), сложная интеграция, overkill для скриншотов
3. **Smartmockups** -- закрылся / объединился с другими сервисами, ненадежно
4. **Платные API для batch** -- Dynamic Mockups, Mockuuups Studio хороши, но платные при объеме > 3/день
5. **Pencil.dev для существующих проектов** -- полезен для новых, но для 10 готовых описаний проще сделать HTML

---

## 10. Итоговая рекомендация

### Для текущих 10 проектов (one-time):
**v0.dev (генерация HTML) + Playwright (скриншоты) + CSS Device Mockups (обертка)**
- 0 расходов
- ~5 часов работы
- Профессиональный результат
- Воспроизводимо (обновить скриншоты = перезапустить скрипт)

### Для Portfolio Agent (автоматизация):
**Playwright (встроен в проект) + CSS Device Mockup шаблоны**
- Используем существующий `StealthBrowser`
- HTML-шаблоны mockups в `portfolio/templates/`
- Pipeline: `page.setContent(html)` -> screenshot -> mockup template -> final image
- Полная автоматизация, 0 внешних зависимостей

### Для проектов без визуального UI (боты, автоматизации):
**HTML mockup интерфейсов** (Telegram chat, n8n workflow diagram, CLI output)
- Много готовых CSS-шаблонов Telegram-чата на CodePen/GitHub
- n8n workflow можно визуализировать как блок-схему (Mermaid -> SVG -> Playwright)

### Приоритет реализации
1. Подготовить CSS device mockup шаблон (MacBook + iPhone) -- 1 час
2. Создать HTML для 3 самых простых проектов (01, 02, 06) -- 3 часа
3. Написать batch-скрипт (Python/shot-scraper) -- 1 час
4. Проверить качество, итерировать
5. Доделать оставшиеся 7 проектов -- 4-5 часов
