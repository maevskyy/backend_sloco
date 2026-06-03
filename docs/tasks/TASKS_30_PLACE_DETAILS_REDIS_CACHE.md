# TASKS 30: Redis-кэш для `GET /v1/places/:id` (Stage 1)

Status: Done.

## Context

Фронт ходит в Supabase на каждый запрос. Вводим кэш-слой: горячие чтения из
кэша, БД дёргается редко. Решения:

- **Store:** Redis (`ioredis`), уже заскаффолен в `backend/docker-compose.yml`
  (профиль `cache`).
- **Scope Stage 1:** только `GET /v1/places/:id` — статичные данные, простой
  ключ, высокий hit-rate. Карта (произвольный bbox, нужен tile-snapping) и
  search — отдельные этапы.
- **Консистентность (MVP):** TTL + ручной flush после офлайн-импорта. Без
  write-through и invalidate-on-write (у мест нет юзерского write-пути).

### Подтверждено по коду (ключевой инвариант)

- `places.service.ts` → возвращает **чистый public-payload** (`mapPlaceDetailRow`),
  без юзерских полей.
- `places.controller.ts` → `enrichSavedState()` накладывает
  `isSaved`/`savedCollectionIds` **после** сервиса.
- ⇒ **Кэшируем результат сервиса (public), НЕ output контроллера.** Так юзерские
  поля физически не попадают в кэш. Это инвариант задачи.

---

## Архитектура

```text
controller (auth optional, enrich saved state)
   └─ service.getPlaceDetails(id)        ← cache-aside ВОТ ЗДЕСЬ (public payload)
         ├─ cache hit  → public details
         └─ miss → store.placeDetailsById → set(public) → details
```

Кэш — инфраструктура → `src/lib/cache/`. Store остаётся чистым DB-access,
controller — HTTP/auth/enrichment, service оркестрирует cache-aside.

---

## Файлы

Создаются:

- `src/lib/cache/cache-store.ts`

  ```ts
  export interface CacheStore {
    get<T>(key: string): Promise<T | null>;
    set<T>(key: string, value: T, ttlSeconds: number): Promise<void>;
    del(key: string): Promise<void>;
    delByPrefix(prefix: string): Promise<void>;
  }
  ```

- `src/lib/cache/redis-cache-store.ts` — `RedisCacheStore` на `ioredis`
  (singleton по образцу `src/lib/supabase.ts`, `lazyConnect`).
  - `get`: `JSON.parse` в **try/catch** → невалидный JSON = miss (не throw).
  - **Graceful fallback:** любая ошибка Redis → лог + ведём себя как miss,
    запрос НЕ падает.
  - `delByPrefix` через **`SCAN`**, не `KEYS`:

    ```ts
    async delByPrefix(prefix: string) {
      let cursor = "0";
      do {
        const [next, keys] = await redis.scan(cursor, "MATCH", `${prefix}*`, "COUNT", 100);
        cursor = next;
        if (keys.length) await redis.del(...keys);
      } while (cursor !== "0");
    }
    ```

- `src/lib/cache/noop-cache-store.ts` — `NoopCacheStore` (всегда miss). Дефолт
  без `REDIS_URL` и при `TTL=0`.
- `src/lib/cache/index.ts` — `getCacheStore()`:
  - `REDIS_URL` отсутствует → `NoopCacheStore`;
  - `PLACE_CACHE_TTL_SECONDS=0` → bypass (Noop-поведение);
  - иначе → `RedisCacheStore`.

Правятся:

- `src/config/env.ts` — `REDIS_URL?` (optional url), `PLACE_CACHE_TTL_SECONDS`
  (coerce number, дефолт `3600`, `0` разрешён = bypass).
- `.env.example` — `REDIS_URL=redis://redis:6379/0`, `PLACE_CACHE_TTL_SECONDS=3600`.
- `src/modules/places/services/places.service.ts` — cache-aside:
  ключ `place:v1:${id}`, miss → store → `set` (только non-null). Инъекция
  `CacheStore` в фабрику (дефолт `getCacheStore()`).
- `src/modules/places/places.module.ts` — прокинуть `CacheStore`.
- `package.json` — `ioredis`.
- `backend/docker-compose.yml` — Redis остаётся за профилем `cache`; прокинуть
  `REDIS_URL` в сервис `backend`.

---

## Поведение

- **Ключ:** `place:v1:${id}` (версия в префиксе → смена формата = смена префикса).
- **TTL:** `PLACE_CACHE_TTL_SECONDS`, дефолт `3600`. `0` → bypass.
- **Negative caching:** `null` (не найдено) НЕ кэшируем.
- **Flush на импорт:** `delByPrefix("place:v1:")` — звать из ETL-скрипта вручную.
- **Метрики — только structured log** (без нового слоя):

  ```json
  { "eventType": "metric", "metricType": "cache", "cacheName": "place_details",
    "cacheStatus": "hit", "keyPrefix": "place:v1", "durationMs": 2 }
  ```

  `cacheStatus`: `hit | miss | error | bypass`.

---

## Деплой / окружения

- **local/e2e:** `docker compose --profile cache up` + `REDIS_URL` задан.
- **prod (Stage 1):** `REDIS_URL` пока можно НЕ задавать → backend на
  `NoopCacheStore`, поведение == текущему. Включаем Redis на проде после
  локальной проверки.

---

## Тест-план (`src/modules/places/tests/`, `src/lib/cache/`)

- first request → `miss` (store зовётся); second → `hit` (store НЕ зовётся);
- TTL: после истечения (fake timers) снова `miss`;
- graceful fallback: cache get/set кидает → запрос отдаёт данные из БД
  (`error`-статус, не 500);
- `NoopCacheStore` / `TTL=0`: всегда miss, поведение == текущему;
- `null` не кэшируется (повторный 404 снова идёт в store);
- сериализация round-trip `PlaceDetails`;
- **инвариант:** в кэш кладётся public payload без `isSaved`/`savedCollectionIds`;
- существующие `places.routes.test.ts` не ломаются.

Запуск: `pnpm build && pnpm test && pnpm typecheck && pnpm lint`.

---

## Проверка (e2e, вручную)

```bash
cd backend && docker compose --profile cache up -d
curl -s localhost:3000/v1/places/1   # miss (лог), идёт в БД
curl -s localhost:3000/v1/places/1   # hit (лог), не идёт в БД
docker compose exec redis redis-cli --scan --pattern 'place:v1:*'
docker compose exec redis redis-cli TTL place:v1:1
# authenticated: isSaved/savedCollectionIds корректны и НЕ из кэша
```

---

## Следующие этапы (не сейчас)

- Stage 2: `search` (норм. ключ, короткий TTL); `map` через tile-snapping.
- Stage 3: `Cache-Control`/`ETag` (CDN/304); invalidate-on-write для юзерских
  кэшей; single-flight против stampede.

## Out of scope (Stage 1)

кэш map/search/saved; write-through; распределённые инвалидации; stampede-защита.

## Assumptions

- Одна инстанция gateway на Stage 1 (in-process кэш был бы достаточен, но Redis
  выбран как переиспользуемый слой на вырост).
- `places` меняется только офлайн-импортом → TTL + ручной flush достаточно.
- `place_details_by_id` остаётся единственным DB-путём для деталей места.
