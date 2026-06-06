# TASKS 27: Пространственное покрытие карты и meta для обрезанных результатов

Status: Done.

## Summary

Исправляем проблему `/v1/map/places`, где в плотных районах backend возвращает
фиксированный top-N список, который кучкуется в одной части запрошенного bbox и
оставляет резкие прямоугольные пустые зоны на карте.

Текущий endpoint специально ограничивает количество точек по `zoom`, но этот
лимит применяется к глобально отранжированному списку. Это дает хорошие места по
score, но не гарантирует пространственное покрытие. Когда клиент пэнит карту или
запрашивает bbox с буфером, лучшие места могут оказаться в одной плотной
под-области, а остальная видимая карта выглядит пустой, хотя места там есть.

Эта задача сохраняет lightweight map pin модель из `TASKS_24`, но меняет
стратегию выбора точек так, чтобы даже обрезанный результат покрывал
запрошенный bbox.

## Current Behavior

Запрос:

```http
GET /v1/map/places?swLat=44.40&swLng=26.06&neLat=44.46&neLng=26.14&zoom=14&limit=200
```

Текущий budget по zoom:

```text
zoom <= 10 -> totalLimit 80,  featuredLimit 8
zoom <= 12 -> totalLimit 120, featuredLimit 12
zoom <= 14 -> totalLimit 180, featuredLimit 20
zoom <= 16 -> totalLimit 220, featuredLimit 30
zoom > 16  -> totalLimit 250, featuredLimit 40
```

Важная деталь:

```text
client limit может только сузить backend zoom cap, но не расширить его
```

То есть `zoom=14&limit=200` все равно вернет максимум `180`.

База фильтрует по bbox:

```sql
where p.geom && st_makeenvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
```

Потом кандидаты сортируются по глобальным visibility/rating/popularity signals,
и backend еще раз ранжирует их глобальным score. Сейчас нет квоты на tile/cell и
нет гарантии пространственного распределения.

## Problem

Сейчас endpoint отвечает на вопрос:

```text
"дай мне лучшие 180 мест в этом bbox"
```

Но для map UX нужен другой контракт:

```text
"дай мне до 180 полезных мест, которые еще и пространственно покрывают bbox"
```

Это разные задачи.

Симптомы от frontend:

- резкие прямоугольные пустые зоны при pan;
- чем меньше `limit`, тем меньше spatial extent у returned points;
- bbox в 4 раза больше может вернуть примерно то же количество точек, снижая
  плотность в видимой области;
- `limit > 200` возвращает 400 в текущем deployed API;
- returned count управляется `zoom`, а не площадью bbox.

## Goals

- Сохранить ranked/high-quality markers.
- Убрать резкие пустые под-прямоугольники, если в bbox есть места.
- Сообщать клиенту, когда ответ был обрезан.
- Сохранить lightweight map payload.
- Не возвращать unlimited points в очень плотных районах.

## Non-Goals

- Не редизайнить place details.
- Не возвращать full place payload из map endpoint.
- Не добавлять raw provider/debug blobs в map pins.
- Не делать полноценный tile API в этой задаче, если grid-подход окажется
  достаточным после тестирования.

## Key Changes

### 1. Добавить response metadata

Расширить ответ `GET /v1/map/places`:

```json
{
  "places": [],
  "meta": {
    "returnedCount": 180,
    "limit": 180,
    "requestedLimit": 200,
    "candidateLimit": 720,
    "capped": true,
    "queryBounds": {
      "swLat": 44.4,
      "swLng": 26.06,
      "neLat": 44.46,
      "neLng": 26.14
    }
  }
}
```

`totalBeforeLimit` полезен, но для первой реализации опционален, потому что
требует либо второго count query, либо более тяжелого RPC. Начинаем без него,
если не сможем добавить дешево.

Рекомендуемая первая форма `meta`:

```ts
meta: {
  returnedCount: number;
  limit: number;
  requestedLimit: number | null;
  candidateLimit: number;
  capped: boolean;
  queryBounds: {
    swLat: number;
    swLng: number;
    neLat: number;
    neLng: number;
  };
}
```

`capped` на первом этапе может означать:

```text
rows fetched from DB >= candidateLimit OR returnedCount >= limit
```

Это не идеальный total-count signal, но этого достаточно, чтобы frontend понимал:
backend, скорее всего, обрезал результат.

### 2. Spatially fair selection

Заменить финальный global top-N selection на grid-aware selection.

MVP algorithm:

1. Забираем candidates из `map_places_in_bbox` через текущий overfetch.
2. Считаем score каждого candidate через существующий `scoreMapPlace`.
3. Делим query bbox на небольшую grid по zoom и total limit.
4. Кладем каждое место в grid cell по latitude/longitude.
5. Сортируем места внутри каждой cell по score.
6. Round-robin выбираем из непустых cells, пока не достигнем `totalLimit`.
7. Сортируем selected places для display priority:
   - либо сохраняем pick order для spatial balance;
   - либо назначаем `featured` по highest score среди selected, затем stable order.

Стартовые размеры grid:

```text
zoom <= 10 -> 4x4
zoom <= 12 -> 4x4
zoom <= 14 -> 5x5
zoom <= 16 -> 5x5
zoom > 16  -> 6x6
```

Сначала держим эти constants в `map.ranking.ts`. Не создаем отдельную
configuration system сейчас.

### 3. Сохранить качество featured markers

`displayKind` остается:

```text
featured | dot
```

Но первые `featuredLimit` мест не должны все приходить из одной cell, если это
создает очевидный дисбаланс на карте.

Предпочтительный MVP:

- сначала выбрать spatially balanced result set;
- потом выбрать `featured` из selected set по score;
- сохранить `displayPriority` stable и deterministic.

Если визуально это будет странно, follow-up может разделить priority:

```text
displayPriority: spatial/render priority
scoreRank: pure quality rank
```

Не добавлять это, пока frontend явно не попросит.

### 4. Аккуратно работать с limits

Не решать проблему простым поднятием `limit`.

Пока оставляем public query cap консервативным:

```text
limit max: 250
```

После того как spatial selection заработает, можно вернуться к:

```text
limit max 300-500
```

Но поднятие cap без spatial fairness только утяжелит endpoint и не исправит
корневую проблему.

### 5. Задокументировать bbox contract

Уточнить в OpenAPI/docs:

- `swLat/swLng/neLat/neLng` это bbox, не center/radius;
- bbox через antimeridian пока не поддержан;
- `zoom` определяет default density budget;
- `limit` может сузить density budget;
- ответ может быть capped и отдает `meta.capped`;
- map results это spatially balanced best-effort, не exhaustive list.

## Files

Вероятные backend files:

```text
src/modules/map/common/map.schemas.ts
src/modules/map/common/map.types.ts
src/modules/map/common/map.openapi.ts
src/modules/map/common/map.ranking.ts
src/modules/map/common/map.spatial-ranking.ts
src/modules/map/services/map.service.ts
src/modules/map/tests/map.ranking.test.ts
src/modules/map/tests/map.service.test.ts
src/modules/map/tests/map.routes.test.ts
```

Для первой реализации database migration не нужна.

Возможное database improvement позже:

```text
use SQL-level grid bucketing/window functions if backend candidate processing is too slow
```

Но начинаем в TypeScript, потому что candidate set capped, а поведение проще
тестировать.

## Test Plan

### Unit Tests

Добавить ranking tests:

- места, сконцентрированные в одной cell, не вытесняют полностью другие cells;
- все selected places остаются внутри requested bbox;
- result length никогда не превышает `totalLimit`;
- output deterministic для одинакового input;
- featured count уважает `featuredLimit`;
- empty cells пропускаются и не мешают добирать useful output из populated cells.

### Route/Service Tests

Добавить/обновить tests:

- response включает `meta`;
- `meta.returnedCount === places.length`;
- `meta.requestedLimit` равен `null`, если limit не передали;
- `meta.limit` отражает effective backend cap;
- `meta.capped` равен `true`, когда candidates показывают clipping;
- `limit > schema max` все еще возвращает 400.

### Manual QA

Использовать frontend-provided checks:

```bash
curl -s 'https://sloco.pp.ua/v1/map/places?swLat=44.40&swLng=26.06&neLat=44.46&neLng=26.14&zoom=14&limit=25' \
  | jq '.places | length'

curl -s 'https://sloco.pp.ua/v1/map/places?swLat=44.40&swLng=26.06&neLat=44.46&neLng=26.14&zoom=14&limit=200' \
  | jq '[.places[].latitude] | (max - min)'

curl -s 'https://sloco.pp.ua/v1/map/places?swLat=44.37&swLng=26.02&neLat=44.49&neLng=26.18&zoom=14&limit=200' \
  | jq '.meta, (.places | length)'
```

Также проверить визуально на iOS:

- центр Бухареста на zoom 14;
- медленный pan с включенным frontend debug HUD;
- убедиться, что нет резкой прямоугольной пустой половины экрана, если места там
  существуют.

## Open Questions

- Должен ли `limit` оставаться "absolute max returned places" или стать
  "preferred max returned places", где backend может вернуть меньше в low-density
  areas?
- Нужен ли frontend `totalBeforeLimit`, или `capped` достаточно для MVP?
- Должен ли frontend запрашивать только visible bbox с минимальным buffer, или
  позже хотим backend density, зависящий от площади bbox?
- Если grid selection сработает, нужен ли нам все еще tile endpoint
  (`/v1/map/tiles/:z/:x/:y`)?

## Assumptions

- `TASKS_24` lightweight map pins остаются serving model.
- `displayKind` и `displayPriority` остаются runtime fields.
- Текущий bbox filter корректен для Бухареста и обычных bbox без antimeridian.
- Spatial fairness для map coverage важнее, чем pure global top-N list.
