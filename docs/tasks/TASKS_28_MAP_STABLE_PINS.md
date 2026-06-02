# TASKS 28: Стабильность map-пинов через порог видимости (A-lite)

Status: Done.

## Summary

Чиним «дёрготню» (pin churn) на `/v1/map/places`: при пане карты пин, который
остаётся в зоне видимости, может пропасть и снова появиться.

`TASKS_27` дал пространственное покрытие (grid round-robin) и `meta`, но финальный
отбор остался **count-based**: из кандидатов выбираем `totalLimit` штук. Сколько
именно показать — считается **относительно соседей в текущем bbox**. При пане
набор соседей меняется → граница отсечки едет → точка A, оставаясь в кадре, то
проходит отсечку, то нет.

Фикс: членство пина делаем **интринсивным** — точка видна, если её собственный
`map_visibility_score >= threshold(zoom)`, а не «попала в топ-N кадра». Тогда при
пане на одном зуме набор видимых пинов инвариантен by construction: видимость A
зависит только от самой A и зума, а не от того, что ещё попало в запрос.

Это backend-only, stateless, фронт-контракт почти не меняется. `map_visibility_score`
уже заполнен и используется. Позже порог бесшовно меняется на
`map_min_zoom_global <= zoom`, а сверху доезжает кластеризация/тайлы.

## Где это в общей картине

```text
TASKS_24  -> lightweight map pins
TASKS_27  -> spatial coverage (grid) + meta        # покрытие
TASKS_28  -> threshold membership                   # стабильность (эта задача)
later     -> map_min_zoom_global + clustering/tiles # продакшн-уровень
```

Grid решает **coverage**, threshold решает **continuity** — это разные задачи, и
threshold не отменяет grid (грид остаётся для порядка и для честного отбора, если
сработает safety-cap).

## Current Behavior

`services/map.service.ts`: `getEffectiveDisplayLimits(zoom)` даёт `totalLimit`,
overfetch кандидатов, `rankSpatiallyBalancedMapPlaces(...)` (грид round-robin) и
срез до `totalLimit`. RPC `map_places_in_bbox` (`010_*`):

```sql
where p.geom && st_makeenvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
order by p.map_visibility_score desc, ...
limit least(greatest(coalesce(result_limit, 100), 1), 1000)
```

То есть и в SQL (`limit`), и в сервисе (`totalLimit`) отсечка по **количеству**.

## Problem

Любая отсечка «топ-N в кадре» нестабильна, даже если score глобальный: сам срез
N-го элемента считается относительно набора в bbox. Пан меняет набор → меняется,
кто N-ный → пин у границы флипает.

Стабильно только когда членство **абсолютное**: точка проходит порог по своему
свойству, независимо от соседей.

Симптом для фронта: точка с фиксированным `map_visibility_score` то есть, то нет
при пане на одном и том же зуме, хотя географически остаётся в кадре.

## Goals

- Пин, оставшийся в кадре при пане на одном зуме, не пропадает.
- Stateless backend (никаких `stickyIds`, без памяти о прошлом экране).
- Фронт-контракт по сути не меняется (только аддитивно в `meta`).
- Сохранить покрытие из `TASKS_27` и lightweight payload.
- Не возвращать unbounded точки в плотных районах (safety-cap).
- Сделать порог/cap наблюдаемыми (`meta` + лог) для калибровки.

## Non-Goals

- Не делать `map_min_zoom_global`-версию сейчас (это follow-up, когда DA заполнит
  и откалибрует колонку).
- Не делать кластеризацию/тайлы в этой задаче.
- Не двигать состояние на фронт (вариант `keepIds` отклонён: он делает фронт
  умным и не кэшируем).
- Не менять place details / lightweight модель.

## Key Changes

### 1. Селектор членства: count → абсолютный порог

В `common/map.ranking.ts` ввести пороги вместо count-бюджета как **решателя
членства**:

```ts
getMapVisibilityThresholds(zoom): { minScore: number; featuredMinScore: number }
```

- Те же зум-бакеты, что и сейчас (`<=10/<=12/<=14/<=16/else`), но значения —
  пороги `map_visibility_score`, не количества.
- Монотонно: выше зум → ниже `minScore` → больше пинов.
- `featuredMinScore` — порог для тира `featured` (тоже интринсивный).
- `getMapDisplayLimits`/`getEffectiveDisplayLimits` как решатель членства больше
  не используются; overfetch `getCandidateLimit` не нужен.

### 2. RPC: фильтр по `min_score`

Миграция `supabase/migrations/013_map_places_in_bbox_threshold.sql`:

```text
ВАЖНО: ЭТА МИГРАЦИЯ DESTRUCTIVE. ОНА ДЕЛАЕТ DROP FUNCTION public.map_places_in_bbox
(старая сигнатура с 5 аргументами) И ПЕРЕСОЗДАЁТ ЕЁ С НОВОЙ СИГНАТУРОЙ.
Данные в таблицах не трогаются; пересоздаётся только функция. Короткое окно между
DROP и CREATE — деплой согласовать.
```

- `drop function if exists public.map_places_in_bbox(double precision, double precision, double precision, double precision, integer);`
  (нельзя просто добавить параметр — Postgres создаст второй overload).
- `create function public.map_places_in_bbox(sw_lat, sw_lng, ne_lat, ne_lng, min_score numeric default 0, result_limit integer default 250)`:
  тот же `returns table (...)` (колонки не меняем),
  `where p.geom && st_makeenvelope(...) and coalesce(p.map_visibility_score, 0) >= min_score`,
  `order by p.map_visibility_score desc, rating_score_0_100 desc nulls last, ..., p.id asc`,
  `limit least(greatest(coalesce(result_limit, 250), 1), 1000)`.
- `default min_score = 0` → обратная совместимость.
- Отдельный `GRANT execute` не добавляем: у текущей публичной RPC нет явного
  grant в миграциях, Postgres default EXECUTE для `public` сохраняет поведение.

`result_limit` теперь — **safety-cap по payload**, а не целевая плотность.

### 3. Сервис: порог + грид для порядка/cap

`services/map.service.ts`:

- `const { minScore, featuredMinScore } = getMapVisibilityThresholds(effectiveZoom)`;
- `rows = await store.placesInBbox(query, minScore, effectiveCap)`;
- порядок и честный отбор при срабатывании cap — оставить
  `rankSpatiallyBalancedMapPlaces` (грид из `TASKS_27`), но **только** среди
  прошедших порог; без count-среза по `totalLimit`;
- тир интринсивный: `displayKind = row.map_visibility_score >= featuredMinScore ? "featured" : "dot"`
  (не по позиции в списке — иначе тир тоже флипает);
- `displayPriority` — по позиции после ранжирования (косметика, на членство не влияет).

`stores/map.store.ts`: RPC-вызов передаёт `min_score` + `result_limit`
(обёртка `measureDependencyMetric` сохраняется). `MapStoreContract.placesInBbox`
получает `minScore` и `resultLimit`.

### 4. `limit` переосмыслить как верхний кап

```ts
effectiveCap = min(query.limit ?? SAFETY_CAP, SAFETY_CAP)   // SAFETY_CAP напр. 400
```

`limit` больше не «дай ~N», а «не больше N».

ВАЖНО (записать в `docs/FRONTEND_MAP_API.md`): **в нормальном использовании карты
фронт `limit` не шлёт.** Маленький `limit` возвращает count-отсечку «top-N в
кадре» → churn возвращается для этого клиента. `limit` — это debug/safety-бэкстоп,
а не инструмент плотности. Стабильный путь — порог решает, сколько вернуть.

### 5. `meta`: расширить под threshold-модель

`meta` уже есть (`TASKS_27`). Добавить поля диагностики:

```ts
meta: {
  effectiveZoom: number;        // какой зум backend реально применил
  minScore: number;             // порог членства
  featuredMinScore: number;     // порог тира featured
  safetyCap: number;            // = effectiveCap
  capHit: boolean;              // rows.length >= safetyCap (членство снова не гарантировано)
  returnedCount: number;
  requestedLimit: number | null;
  queryBounds: { swLat; swLng; neLat; neLng };
}
```

`capHit` — ключевой сигнал: если он частит, порог для этого зума занижен (рекалибровать)
либо район дорос до кластеризации (уровень 2). Логировать `map_pins_cap_hit` метрикой.

Это аддитивное изменение контракта: фронт `meta` может игнорить; в OpenAPI
`MapPlacesMeta` прирастает полями (id компонентов не меняются).

### 6. Калибровка порогов (make-or-break — это не код, а данные)

Код тут на полдня; качество фичи определяет калибровка.

- Цель: `minScore(zoom)` такой, чтобы типичный кадр на этом зуме давал ~прежнюю
  плотность (старые `totalLimit`: 80/120/180/220/250), а `capHit` почти не случался.
- Метод: вручную прогнать SQL в Supabase SQL Editor для нескольких
  репрезентативных bbox на каждый зум-бакет, найти значение
  `map_visibility_score` на ранге = целевая плотность (перцентиль) → засеять
  константы → подправить по проду.
- То же для `featuredMinScore` из старого `featuredLimit`.
- Зафиксировать в `map.ranking.ts` как константы с комментом «откалибровано по
  данным на <дата>, рекалибровать при росте плотности/городов».

## Files

```text
supabase/migrations/013_map_places_in_bbox_threshold.sql   (новый, DESTRUCTIVE: DROP FUNCTION)
src/modules/map/common/map.ranking.ts        # getMapVisibilityThresholds + SAFETY_CAP
src/modules/map/common/map.types.ts          # MapStoreContract.placesInBbox(minScore, resultLimit)
src/modules/map/common/map.schemas.ts        # meta: + effectiveZoom/minScore/featuredMinScore/safetyCap/capHit
src/modules/map/common/map.openapi.ts        # описание route (limit = верхний кап)
src/modules/map/stores/map.store.ts          # передать min_score + result_limit
src/modules/map/services/map.service.ts      # порог + грид для порядка, тир интринсивный
src/modules/map/tests/map.ranking.test.ts
src/modules/map/tests/map.service.test.ts
src/modules/map/tests/map.routes.test.ts
docs/FRONTEND_MAP_API.md                      # семантика limit + meta
```

## Test Plan

### Unit (`map.ranking`)
- `getMapVisibilityThresholds(zoom)` — ожидаемые пороги по бакетам, монотонность
  (выше зум → ниже `minScore`).

### Unit (`map.service`)
- членство по порогу: всё с `map_visibility_score >= minScore` доезжает; ничего не
  режется по count, кроме `safetyCap`;
- тир по `featuredMinScore`, не по позиции;
- **тест на стабильность (ключевой)**: один и тот же набор «в двух перекрывающихся
  кадрах» (разное число прочих рядов) → пин с фиксированным score присутствует/
  отсутствует одинаково в обоих, не флипает;
- `capHit=true`, когда `rows.length >= safetyCap`.

### Route (`map.routes.test`)
- контракт прежний: 200, `isSaved`-enrich, 401 на invalid-токен, 400 на битый bbox;
- `meta` содержит новые поля; `meta.returnedCount === places.length`.

### SQL вручную
- `select count(*) from map_places_in_bbox(sw,sw,ne,ne, <minScore>, 400)` на двух
  перекрывающихся bbox с одной точкой A на одном зуме → A в обоих (или ни в одном),
  не флипает.

### Контракт
- diff `/v1/swagger/openapi.json`: id и paths не меняются; `MapPlacesMeta` аддитивно
  прирастает полями.

### Гейт
- `pnpm typecheck && pnpm test && pnpm lint && pnpm build`.

## Open Questions

- Точные пороги `minScore(zoom)` — определяются калибровкой по проду, не угадываются.
- `SAFETY_CAP`: 400 как старт; уточнить по `capHit`-метрике.
- Когда переходить с `map_visibility_score`-порога на `map_min_zoom_global`
  (зависит от готовности оффлайн-пайплайна DA).

## Assumptions

- `map_visibility_score` заполнен для serving-набора (используется уже сейчас).
- Покрытие из `TASKS_27` (grid) остаётся; threshold добавляет continuity сверху.
- Зум легитимно меняет набор (это намеренный переход, не churn) — чиним только пан
  на одном зуме.
- Фронт в норме не шлёт `limit`; шлёт — осознанно опт-инит обратно в count-отсечку.
