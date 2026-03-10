# GeoScout Agent: Спецификация

## Назначение

Пространственное сканирование городов для обнаружения офлайн-бизнесов без сайтов -- целевых клиентов для Pipeline B (B2B outreach). Использует H3 гексагональную сетку + Overpass API (OpenStreetMap) для систематического покрытия территории.

**Файлы:** `src/agents/geo_scout.py`, `src/geo/h3_scanner.py`, `src/geo/overpass.py`
**LLM:** не используется (чистая data pipeline)
**Класс:** `GeoScoutAgent(ConstrainedAgent)`, agent_name=`"geoscout"`

## Архитектура (data flow)

```
Входной город (state)
  -> Nominatim geocoding -> BoundingBox
    -> H3 generate_hexagons(bbox, resolution=8) -> list[hex_id]
      -> для каждого hex: hex_to_bbox() -> Overpass query_businesses()
        -> фильтрация (без website) -> дедупликация (osm_id)
          -> PostgreSQL (таблица leads) -> state -> Outreach Agent
```

## Входы

- `state["artifacts"]["_scan_city"]` -- название города (приоритет)
- `state["project"]["requirements"]` -- fallback

При отсутствии города: `status="failed"`, `errors += "No city specified"`.

## Allowed Tools

`geocode_city`, `scan_hexagons`, `query_overpass`

## H3 гексагоны

**Библиотека:** `h3` v4 (Python bindings)

### Resolution

**Resolution 8** (default) -- edge length ~461 м, площадь ~0.74 км^2 на гексагон. Подходит для city-level сканирования.

### Алгоритм

1. `geocode_city(city)` -> `BoundingBox(min_lat, min_lon, max_lat, max_lon)`
2. `generate_hexagons(bbox, resolution=8)`:
   - Четыре угла bbox -> `h3.LatLngPoly(outer_ring)`
   - `h3.polygon_to_cells(polygon, resolution)` -> set of cell IDs
   - Сортировка для детерминированного порядка
3. `hex_to_bbox(hex_id)`:
   - `h3.cell_to_boundary(hex_id)` -> list of (lat, lon)
   - Min/max lat/lon -> `BoundingBox`

### Ограничение

`_MAX_HEXAGONS_PER_SCAN = 50` -- защита от rate-limit Overpass. Если город генерирует > 50 гексагонов, берутся первые 50 (логируется warning `geoscout_truncating_hexagons`).

## Nominatim Geocoding

**URL:** `https://nominatim.openstreetmap.org/search`
**User-Agent:** `MultiAgentService/4.2 (geo-scanner; contact@example.com)` (обязателен по TOS)
**Timeout:** 10 сек
**Формат ответа:** JSON, `limit=1`

Ответ Nominatim содержит `boundingbox: [south, north, west, east]` -> парсится в `BoundingBox`.

При ненайденном городе: `ValueError("City not found: ...")`.

## Overpass API

**URL:** `https://overpass-api.de/api/interpreter`
**Клиент:** `OverpassClient` (`src/geo/overpass.py`)

### Rate limiting

- `rate_limit = 1.0` сек между запросами (enforced через `_rate_wait()` с `time.monotonic()`)
- `timeout = 30.0` сек на HTTP-запрос
- Lazy-init `httpx.AsyncClient`

### Overpass QL запрос

```
[out:json][timeout:25];
(
  node["amenity"~"restaurant|cafe|bar|..."]["name"](south,west,north,east);
  node["shop"~"convenience|supermarket|..."]["name"](south,west,north,east);
);
out body;
```

### Категории бизнесов

**Amenities (10):** restaurant, cafe, bar, fast_food, beauty, hairdresser, dentist, doctors, pharmacy, veterinary

**Shops (12):** convenience, supermarket, clothes, shoes, furniture, electronics, hardware, bakery, butcher, florist, optician

### Фильтрация (в Python)

`_parse_element()` отбрасывает элементы:
- С тегами `website` или `contact:website` (у них уже есть сайт)
- Без тега `name` (невозможно идентифицировать)

### GeoLead (dataclass)

```python
@dataclass
class GeoLead:
    osm_id: int            # OSM node ID (ключ дедупликации)
    name: str              # Название бизнеса
    category: str          # amenity или shop тег
    lat: float
    lon: float
    address: str | None    # addr:street + addr:housenumber
    city: str | None       # из параметра или addr:city
    phone: str | None      # phone или contact:phone
    h3_index: str | None   # предвычисленный H3 cell (resolution 8)
    raw_tags: dict          # все OSM-теги
```

## Дедупликация

**Уровень 1 (in-memory):** `seen_osm_ids: set[int]` -- в рамках одного сканирования.
**Уровень 2 (DB):** `_store_leads()` проверяет `SELECT Lead WHERE osm_id == geo_lead.osm_id` перед INSERT.

## Хранение в БД (таблица `leads`)

```python
Lead(
    name=geo_lead.name,
    category=geo_lead.category,
    address=geo_lead.address,
    city=city,
    latitude=Decimal(str(geo_lead.lat)),
    longitude=Decimal(str(geo_lead.lon)),
    h3_index=geo_lead.h3_index,
    phone=geo_lead.phone,
    osm_id=geo_lead.osm_id,
    status="new",
)
```

`status="new"` -- готов к enrichment в Outreach Agent.

## Расширения (не реализовано)

- **Яндекс.Карты** -- API для российских городов (дополнительное покрытие)
- **2GIS** -- API для детальной информации о бизнесах в РФ/СНГ
- **Социальные сети** -- парсинг Instagram/VK для поиска бизнесов без сайтов
- **Business Analyzer** -- LLM-анализ потенциала бизнеса для создания сайта
- **Lead Scorer** -- ML-скоринг лидов по вероятности конверсии

## State выход

```python
artifacts["_geo_scan_results"] = {
    "city": "Berlin",
    "hexagons_total": 120,
    "hexagons_scanned": 50,
    "leads_found": 340,
    "leads_stored": 280,
}
artifacts["_lead_osm_ids"] = [osm_id1, osm_id2, ...]
next_agent = "outreach"
```

## Ограничения и rate limits

| Сервис | Лимит | Стратегия |
|--------|-------|-----------|
| Nominatim | 1 req/sec (TOS) | Один запрос на вызов |
| Overpass | ~10,000 req/day | `rate_limit=1.0s`, max 50 гексагонов |
| H3 | CPU-only | Нет ограничений |
| PostgreSQL | connection pool | `get_db_session()` context manager |

## Ключевые методы

| Метод | Сигнатура | Назначение |
|-------|----------|------------|
| `geocode_city` | `async (city: str, timeout=10.0) -> BoundingBox` | Geocoding через Nominatim |
| `generate_hexagons` | `(bbox: BoundingBox, resolution=8) -> list[str]` | H3 тайлинг bbox |
| `hex_to_bbox` | `(hex_id: str) -> BoundingBox` | Конвертация гексагона в bbox |
| `OverpassClient.query_businesses` | `async (bbox, city=None) -> list[GeoLead]` | Запрос бизнесов без сайтов |
| `GeoScoutAgent._store_leads` | `async (leads, city) -> int` | Persist в PostgreSQL |
| `geo_scout_node` | `async (state: dict) -> dict` | LangGraph node (dict signature!) |

**Важно:** `geo_scout_node` использует сигнатуру `dict[str, Any]` вместо `AgentState` -- это workaround для бага LangGraph 1.0.8 с реконструкцией state из TypedDict (см. debugging.md).
