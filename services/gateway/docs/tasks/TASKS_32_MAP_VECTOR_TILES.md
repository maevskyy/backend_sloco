# TASKS 32: Продакшн-карта на vector tiles (MVT из PostGIS)

Status: Implementation Ready.

Local implementation is complete and verified (`pnpm typecheck`, `pnpm test`,
`pnpm lint`, `pnpm build`). Production rollout is still pending because migration
`014_map_vector_tiles.sql` has not been applied to Supabase yet.

## Summary

Переводим карту с bbox-JSON (`/v1/map/places`) на **полноценные vector tiles
(MVT)**, генерируемые PostGIS (`ST_AsMVT`) и отдаваемые как иммутабельные тайлы
`/v1/map/tiles/{z}/{x}/{y}.mvt` за CDN. Это продакшн-вариант «как Google Maps»,
а не промежуточный: GPU-рендеринг на клиенте (Mapbox уже стоит), CDN-кэш тайлов,
масштаб на много городов, плавный пан/зум без перерисовки.

Главное, что это дёшево по клиенту: **самую дорогую часть (GPU vector-рендерер) мы
уже купили — у нас Mapbox.** MVT он потребляет нативно как `type: "vector"` source.
Бэкенд отдаёт бинарные тайлы, клиент рендерит сам. Персонализация (`isSaved`) — НЕ в
тайле (тайл общий и кэшируется), а через Mapbox `feature-state`.

Не промежуточный вариант: GeoJSON-источник пропускаем, сразу MVT.

## Почему MVT, а не текущий bbox-JSON

- **Кэшируемость/CDN**: тайл `(z,x,y,version)` иммутабелен → кэш на эдже навсегда,
  БД почти не трогается. bbox-JSON некэшируем (каждый вьюпорт уникален).
- **Плавность**: Mapbox рендерит вектор на GPU (зум/пан/коллизии лейблов), без
  перерисовки аннотаций — корень текущих лагов.
- **Масштаб**: тысячи-миллионы точек, много городов — тайлы это держат by design.
- **Генерализация по зуму**: на низком зуме отдаём только высоко-score места
  (декластеризация порогом), на высоком — все.

## Архитектура

```text
iOS (Mapbox vector source)
  → CDN (immutable tiles, version-busted)
    → Nginx
      → Gateway: GET /v1/map/tiles/:z/:x/:y.mvt
          → Redis (tile:v{N}:{z}/{x}/{y} → bytea)   [hit → отдать]
          → Postgres RPC map_tile(z,x,y)            [miss → сгенерить, положить]
isSaved: отдельный канал — GET /v1/me/saved/ids → Mapbox setFeatureState (не в тайле)
detail: tap → GET /v1/places/:id (без изменений)
```

## DB изменения

### 1. Геометрия под MVT (Web Mercator)
`places.geom` сейчас `geometry(Point, 4326)`. MVT работает в 3857. Чтобы не
трансформировать на каждый тайл — добавить генерируемую колонку + индекс.

```text
ВАЖНО: ЭТА МИГРАЦИЯ DESTRUCTIVE-ADJACENT. ALTER TABLE public.places ADD COLUMN
(generated stored) ПЕРЕЗАПИСЫВАЕТ ТАБЛИЦУ places (блокировка + время на большой
таблице). Данные не теряются, но это тяжёлый ALTER — гнать в окно, согласовать.
```

```sql
alter table public.places
  add column if not exists geom_3857 geometry(Point, 3857)
  generated always as (st_transform(geom, 3857)) stored;

create index if not exists places_geom_3857_gist
  on public.places using gist (geom_3857);
```

### 2. Порог генерализации по зуму (переиспользуем TASKS_28)
```sql
create or replace function public.map_tile_min_score(z int)
returns numeric language sql immutable as $$
  select case
    when z <= 10 then 92
    when z <= 12 then 86
    when z <= 14 then 76
    when z <= 16 then 66
    else 56
  end;
$$;
```

### 3. RPC генерации тайла
```sql
create or replace function public.map_tile(z int, x int, y int)
returns bytea language sql stable as $$
  with bounds as (
    select st_tileenvelope(z, x, y) as env
  ),
  mvtgeom as (
    select
      st_asmvtgeom(p.geom_3857, b.env, 4096, 64, true) as geom,
      p.id,
      p.name,
      p.category,
      p.primary_type,
      p.price_level,
      p.map_visibility_score,
      p.primary_photo_path
    from public.places p, bounds b
    where p.geom_3857 && b.env
      and coalesce(p.map_visibility_score, 0) >= public.map_tile_min_score(z)
  )
  select coalesce(
    st_asmvt(mvtgeom.*, 'places', 4096, 'geom', 'id'),  -- feature_id = places.id
    ''::bytea
  )
  from mvtgeom;
$$;
```
- `'id'` как `feature_id_name` → у MVT-фичи id = `places.id` → клиенту нативно
  доступен `feature.id` для `setFeatureState` (персонализация).
- Лёгкие атрибуты (name/category/score/photo path) — для стиля/лейблов; богатые
  детали по тапу из `/v1/places/:id`.

## Эндпоинт (gateway)

`GET /v1/map/tiles/:z/:x/:y.mvt`
- Валидация `z/x/y` (z в диапазоне serving-уровней, x/y в пределах `2^z`).
- Порядок: Redis `tile:v{N}:{z}/{x}/{y}` → hit отдать; miss → RPC `map_tile` →
  сохранить в Redis (TTL длинный) → отдать.
- Заголовки: `Content-Type: application/vnd.mapbox-vector-tile`,
  `Cache-Control: public, max-age=31536000, immutable`, `ETag: "v{N}"` → CDN кэширует.
- **Бинарь через прямое PG-соединение, не supabase-js.** supabase-js/PostgREST
  криво отдаёт `bytea` (base64). Для тайлов завести пулед-PG клиент (postgres.js
  или `pg` через Supabase pooler 6543) → `bytea` приходит как `Buffer`. Это
  заодно закрывает дыру «нет пулинга» из плана масштабирования.
- Пустой тайл (`''::bytea`) → отдавать 204/пустое тело, не ошибку.

`GET /v1/map/config`
- Public config endpoint for the iOS client:
  `{ tileVersion, tileUrlTemplate, sourceLayer: "places" }`.
  `tileVersion` is the cache-busting `DATA_VERSION`.

## Версионирование и инвалидция

- Глобальная `tile_version N` (строка в таблице `meta` или env), **бампается
  пайплайном импорта мест**. Входит в Redis-ключ и в URL/заголовок тайла.
- Клиентский тайл-URL содержит версию (`/tiles/{z}/{x}/{y}.mvt?v={N}` или путь),
  чтобы CDN и Mapbox инвалидировали кэш при новой версии данных.

## Персонализация (isSaved) — вне тайла

- Тайл общий → `isSaved` НЕ в нём.
- Новый лёгкий эндпоинт `GET /v1/me/saved/ids` (auth) → `{ placeIds: number[] }`
  (есть `savedPlacesService.getSavedPlaceIds`, обернуть в контроллер).
- Клиент применяет `setFeatureState({source, sourceLayer:'places', id}, {isSaved:true})`
  и стилит по `feature-state`. GPU, без перезагрузки тайлов.

## Генерализация / кластеры

- Базово: порог `map_tile_min_score(z)` прореживает плотность на низком зуме +
  Mapbox сам прячет пересекающиеся лейблы (collision) и сортирует по
  `symbol-sort-key` (score). Этого достаточно для POI-карты «как Google».
- Числовые кластеры («23 места») — НЕ нативны для vector-source (это GeoJSON-фича).
  Если понадобятся — отдельный агрегат-слой тайла (precompute по суб-ячейкам).
  Пометить как опциональный follow-up, в продакшн-MVP не нужен.

## Инфраструктура

- Эндпоинт в gateway (Fastify route, бинарный ответ) за Nginx → CDN. Один сервис,
  как сейчас.
- Альтернатива по росту: вынести в `Martin`/`pg_tileserv` (готовые MVT-серверы из
  PostGIS). Не сейчас — gateway+RPC достаточно и проще поддерживать.

## Файлы

```text
supabase/migrations/0NN_map_vector_tiles.sql   — geom_3857 (ALTER, тяжёлый) + map_tile_min_score + map_tile RPC
src/lib/pg.ts                                  — пулед прямой PG-клиент для bytea (новый)
src/modules/map/common/map.tiles.ts            — валидация z/x/y, serving-уровни, version
src/modules/map/stores/map-tile.store.ts       — вызов map_tile через pg, bytea→Buffer (+ measureDependencyMetric)
src/modules/map/services/map-tile.service.ts   — cache(redis) → store → bytes
src/modules/map/controllers/map.controller.ts  — GET /v1/map/tiles/:z/:x/:y.mvt (бинарь, заголовки)
src/modules/map/common/map.openapi.ts          — задокументировать бинарный тайл-роут
src/modules/me/...                             — GET /v1/me/saved/ids (feature-state)
src/config/routes.ts                           — AppRoute.MapTile, MeSavedIds
src/lib/cache/                                 — ключ tile:v{N}:{z}/{x}/{y}
docs/FRONTEND_MAP_VECTOR_TILES.md              — фронт-handoff (см. отдельный файл)
```

## Back-compat / миграция

- `/v1/map/places` (bbox) **не ломаем**, живёт во время перехода фронта.
- Шипим `/v1/map/tiles` + `/v1/me/saved/ids` → фронт переключается на Mapbox
  vector source → депрекейтим bbox-эндпоинт.

## Test Plan

- **MVT-корректность**: `map_tile(z,x,y)` отдаёт непустой bytea для известного
  тайла с местами; пустой — для пустого региона. Декодировать MVT в тесте
  (mapbox-vector-tile / vt-pbf) → проверить слой `places`, наличие `id` как
  feature id, лёгкие атрибуты, отсутствие тяжёлых.
- **Генерализация**: на z=10 в тайле только score≥92; на z=17 — порог 56.
- **Стабильность/кэш**: повторный запрос тайла → Redis hit (cache-метрика); бамп
  `tile_version` → miss → пересчёт; заголовки `immutable`+ETag присутствуют.
- **Бинарь**: ответ `application/vnd.mapbox-vector-tile`, тело — валидный protobuf
  (не base64-строка) — проверка прямого PG-пути.
- **isSaved**: `/v1/me/saved/ids` отдаёт ids; тайл их НЕ содержит (общий).
- **Контракт**: новый бинарный роут в OpenAPI; существующие id целы.
- Гейт: `pnpm typecheck && pnpm test && pnpm lint && pnpm build`.
- **Нагрузка/CDN**: после прогрева cache/CDN hit-rate на тайлах резко вырастает,
  БД-RPS на карте падает почти до нуля (главный приз).

## Риски

- Тяжёлый `ALTER places ADD geom_3857` (rewrite) — гнать в окно, CAPS-варнинг выше.
- `bytea` через supabase-js — мимо; обязателен прямой пулед-PG (заложено).
- Числовые кластеры не из коробки — осознанно вне MVP.
- Версионирование тайлов должно быть связано с пайплайном импорта, иначе CDN
  отдаст устаревшие тайлы.

## Assumptions

- Клиент на Mapbox (vector-рендерер уже есть) — клиентский стек не меняется,
  меняется источник данных (см. `docs/FRONTEND_MAP_VECTOR_TILES.md`).
- PostGIS 3.x на Supabase (есть `ST_TileEnvelope`/`ST_AsMVT`).
- Богатые детали места остаются за `/v1/places/:id`.
```
