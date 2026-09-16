# Аудит dev → main · 1.2 Миграции и живая БД

Дата: 2026-09-15. Ветка-источник: `origin/dev` @ `dbb1565` (21 коммит впереди `main` @ `fc348fe`, все — Kirill, 2026-08-11 → 08-30).
Прод: **dev**. Последние деплои (GitHub Actions, `ref: dev`): gateway `dbb1565` и recommender `0f52e65` — оба 2026-08-30. Все деплои с 2026-08-16 — с `dev`.

Метод: вычитка `supabase/migrations/017–023`, `supabase/01_*`/`02_*`, `supabase/rollback/*` на dev + сверка с живой Supabase через `psql` (pooler `aws-0-eu-west-1:6543`, PG 17.6) + `pg_stat_statements` (сброшен 2026-08-30 21:18 UTC, т.е. окно = две недели прода на dev) + `EXPLAIN (ANALYZE, BUFFERS)` на трёх горячих RPC.

---

## 1. Статус миграций

Таблицы учёта миграций в `public` нет (есть только `auth`/`storage`/`realtime`). Всё накатывалось руками через `psql` — записи «что и когда» не существует. Статус ниже — по маркерам в живых объектах.

| # | Что делает | Деструктивно | Накачена | Rollback | Замечания |
|---|---|---|---|---|---|
| 017 | `map_tile()`: per-tile top-N вместо глобального score floor | нет (CREATE OR REPLACE) | ✅ (маркер `when z <= 12 then 6`) | не нужен | Связана с `MAP_TILE_VERSION` 1→2 (в CI сейчас 3) |
| 018 | `places` +5 колонок `*_norm` + backfill; `search_places` 6 арг → DROP, новая 8 арг; `places_name_trgm` → DROP, новый `places_name_norm_trgm` | **да**: DROP function, DROP index, UPDATE всех строк | ✅ колонки есть, `name_norm is null` = 0 из 58 427 | `2026-08-11_018_019` | Новый предикат `search_keywords %> q` — корень лага поиска, см. §4.1 |
| 019 | `feed_fallback_places` 5 арг → DROP, новая 6 арг (`category_keywords`) | **да**: DROP function | ✅ | `2026-08-11_018_019` | Bucket-фильтр `like '% kw %'` по трём колонкам — индексом не покрывается |
| 020 | bucket только по `primary_type_norm`/`category_norm`; browse-режим с KNN; +`places_geog_gist`, +`places_primary_type_norm_trgm` | нет | ✅ (маркер `<->` KNN) | покрыт 018/019 | `places_primary_type_norm_trgm` за 2 недели: **idx_scan = 0** |
| 021 | `feed_fallback_places`: `user_city` из +20 буста → hard WHERE | нет | ✅ (маркер `btrim(user_city)`) | `2026-08-13_021` | WHERE по `lower(f_unaccent(city))` — индекса нет → seq scan 58k, см. §4.2 |
| 022 | 4 таблицы event log + индексы, RLS on без политик | нет (аддитивно) | ✅ events_raw 597 · identity_links 4 · rec_served 41 · rec_served_items 8 200 | не нужен | 200 строк jsonb на одну выдачу фида = **191 KB/выдачу**, см. §3 |
| 023 | `saved_collections.slug` + partial unique idx; перепись строк (default → `saved`, +2 системных списка на юзера) | пишет в существующие строки | ✅ 7 юзеров × {saved, favorites, been}; default ровно 1 у всех | `2026-08-28_023` | **Rollback нарушает свой же README** (`delete from …`, `drop column slug`); в разделе «Current» README не упомянут |
| 01/02 | staging-импорт каталога all_themes: `\copy` 6 чанков → `merge_places_staged()` батчами по 1000 | UPSERT **перезаписывает все колонки** существующих 12.6k строк | ✅ 58 427 строк; `places_staging` и процедура удалены | нет | Перезаписаны в т.ч. `price_level`, `map_visibility_score`, `ai_*` — TASKS_44-бэкфилл цен мог быть затёрт (Bucharest: `price_level` есть у 1 222 из 12 084) — **проверить** |

Дрифт вне миграций: в `pg_stat_statements` есть `call public.backfill_primary_photo($1)` (1 вызов, 234 с) — процедуры нет ни в репо, ни в базе сейчас. Ad-hoc скрипт, следа не осталось.

Совместимость `main`-кода с живой схемой (если переносить по кускам): `search_places(6 арг)` и `feed_fallback_places(5 арг)` работают через DEFAULT'ы новых параметров; `saved-places` на main не знает `slug`, но `is_default` теперь стоит на списке «Saved», а не «Favorites» — семантика «куда падает быстрый сейв» поменялась под main-кодом.

## 2. Каталог и инстанс

| | Было (по комментам Kirill) | Сейчас |
|---|---|---|
| Строк в `places` | 12 578 (Bucharest + Tbilisi) | **58 427**: Berlin 37 813 · Bucharest 12 084 · Tbilisi 8 530 |
| `places` total | — | **525 MB** = heap 174 + TOAST 223 + индексы 128; строка ≈ 3.1 KB inline, `attributes` jsonb ≈ 3.6 KB в TOAST |
| `place_photos` total | — | 297 MB (448 696 строк) |
| Фото: доля с `primary_photo_path` | — | Berlin 97 % · Bucharest 72 % · Tbilisi 59 % |

Инстанс Supabase: `shared_buffers` **224 MB**, `effective_cache_size` 384 MB, `work_mem` **2.1 MB**, `max_connections` 60, `statement_timeout` 120 s. Это младший compute-тир. Рабочее множество (≈ 820 MB, из них ~220 MB — TOAST `places`, который горячим путям не нужен) в кеш не влезает: heap hit ratio `places` 86 %, `place_photos` 84.8 % — каждый 7-й блок с диска. Сорты по 8 MB уходят на диск (`external merge`).

Индексы на `places` без единого использования за 2 недели (`idx_scan = 0`): `places_attributes_gin` 28 MB, `places_primary_type_norm_trgm` 10 MB, `places_ai_tags_gin` 9.7 MB, `places_map_visibility_idx`, `places_rating_idx`, `places_primary_type_idx`, `places_category_idx` — ≈ 55 MB мёртвого веса в 224 MB кеша.

## 3. Baseline latency — `pg_stat_statements`, 2026-08-30 → 09-15

| Запрос | Вызовов | mean, ms | max, ms | Откуда |
|---|---|---|---|---|
| `search_places(...)` | 22 | **14 505** | **78 498** | pg pool, `GET /v1/search` |
| `feed_fallback_places` (PostgREST) | 6 | **4 111** | 6 027 | anon / cold-start фид |
| `feed_places_by_source_ids` (PostgREST) | 66 | **1 149** | 4 468 | гидрация выдачи рекомендера |
| `select city, country, count(*) … group by` | 37 | 1 209 | 21 047 | `GET /v1/cities`, на каждый запрос |
| `map_tile($1,$2,$3)` | 459 | 195 | 4 618 | тайлы (Redis-кеш 7 дней) |
| `insert into events_raw` | 164 | 12 | 254 | `POST /v1/events` |
| `insert into rec_served …` | 20 | 64 | 210 | receipt после выдачи |

Трафик мизерный (22 поиска за две недели) — проблема не в нагрузке, а в стоимости одного запроса.

## 4. Разбор трёх горячих RPC

### 4.1 `search_places('cafe', Bucharest)` — 11–18 с

План (тело 020, inline с литералами):

- `BitmapOr` по трём предикатам: `name_norm %> 'cafe'` → 5 096 строк, `name_norm like 'cafe%'` → 2 873, **`search_keywords %> 'cafe'` → 31 971 строк (55 % каталога)**. `search_keywords` — мешок слов из категории/типов/тегов, `word_similarity_threshold = 0.3` — любое частотное слово матчит половину базы.
- Кандидатов 31 780; на каждого — 7 × `word_similarity` (в т.ч. по длинному `search_keywords`), 2 × `f_unaccent`, `st_distance`, и `p.*` (TOAST: `ai_*`-тексты, `attributes`, `ai_tags_json`). 82 800 буферов = **647 MB чтения** на 174 MB heap.
- Сорт 8 MB → `external merge` на диск (`work_mem` 2.1 MB).
- Город — только +12 к рангу: Berlin (37k) сканируется при поиске по Бухаресту.

Эксперимент (тот же запрос, cold / warm):

| Вариант | Кандидатов | Cold | Warm |
|---|---|---|---|
| Как на проде | 31 780 | 14.7 с | 10.6 с |
| + hard `p.city = 'Bucharest'` | 8 170 | 3.6 с | 0.7 с |
| − предикат `search_keywords %> q` | 5 032 | 1.0 с | 0.26 с |
| Оба | 1 210 | **0.11 с** | **0.12 с** |

`'pizza'` (реже): 3 330 кандидатов, 1.9 с — та же структура, просто меньше строк.

Направления фикса (только тело функции, `CREATE OR REPLACE`, без схемы): hard-cut по городу/радиусу до скоринга; убрать `search_keywords %> q` из отбора кандидатов (оставить в скоринге или заменить на tsvector); выбирать нужные колонки вместо `p.*`; отсечь top-K до полного скоринга.

**Дополнение при фиксе (SLO-4, миграция 027, 2026-09-16).** Два факта, которых в разборе выше не было:

- **Направление оператора.** `column %> const` на этом инстансе в ~50 раз медленнее, чем `const <% column` или `word_similarity(const, column) >= 0.3` при той же семантике (2.8 с против 60 мс на 12 084 строках Бухареста; `search_keywords %> 'cafe'` — 3.0 с против 0.42 с). pg_trgm кэширует триграммы **левого** аргумента между вызовами; константа справа — кэш промахивается на каждой строке. Berlin `'cafe'` warm 8.4 с при **0 чтений с диска** — это CPU на операторе, не I/O.
- **Чужие города в выдаче.** Точное имя даёт +30, свой город +12: поиск «cafe» из Бухареста возвращал места с именем «Cafe» из Тбилиси и Берлина (1 500 км). Hard-cut по городу лечит и скорость, и это.

Итог 027: кандидаты — один index-only scan по срезу города (`places_city_candidates_idx`, ключ `lower(f_unaccent(city))`, INCLUDE `name_norm`, `primary_type_norm`, `geom`, скоринговые колонки); `cafe`@Bucharest 10.0 с / 7.6 с → 553 мс / 87 мс, `cafe`@Berlin 10.3 с / 8.4 с → 1.08 с / 255 мс. Стоимость ограничена размером города, а не запросом.

### 4.2 `feed_fallback_places(Bucharest, limit 200)` — 26.4 с cold

- `Seq Scan on places` с фильтром `lower(f_unaccent(city)) = 'bucharest'` — выражение без индекса, 58 427 строк.
- Планировщик оценил 292 строки, реально 12 084 → выбрал `Nested Loop` и **джойнит `place_photos` для всех 12 084 строк до сортировки и LIMIT** (47k буферов на фото).
- Это anonymous / cold-start фид — то, что видит новый пользователь сразу после онбординга.

Тот же RPC под `GET /v1/feed/places?limit=20&category=cafe&city=Berlin` (anon; `category_keywords` = 9 слов бакета `cafe`): **21.6 с cold / 9.8 с warm**. Seq scan 58 427 → 5 879 берлинских кафе → фото джойнятся для всех 5 879 до сортировки (оценка планировщика 146).

Фикс: индекс по выражению (или хранить `city_norm`, как остальные `*_norm`), джойн фото после LIMIT 200, тогда ~200 lookup'ов вместо 12k.

Смежное, из `feed.service.ts` (dev): персональный путь **не передаёт рекомендеру ни город, ни категорию** — рекомендер отдаёт 200 лучших по всему каталогу (58k, три города), gateway гидрирует их (§4.3) и только потом режет по `city`/`category`. После среза часто остаётся мало или ноль → `empty_recommendation_fallback` → этот же медленный RPC. Так выглядит нажатие pill в TikTok-ленте: рекомендер + гидрация 1.15 с + fallback 10–26 с.

### 4.3 `feed_places_by_source_ids(200 ids)` — 2.95 с cold / 0.7 с warm

- Тело (миграция 012/016, **это main-код, не Kirill**) джойнит `p.source_id = r.source_id` без `p.source`. Единственный подходящий индекс — `(source, source_id)`; без ведущей колонки Postgres сканирует его целиком на каждый из 200 id: **113 000 буферов**.
- С `and p.source = 'sloco_ai'`: 1 500 буферов, **12 мс**.
- Баг существовал и до dev; каталог вырос ×4.6 — индекс тоже, поэтому вылезло сейчас. Это горячий путь персонального фида (66 вызовов, mean 1.15 с).

## 5. Прочее, что всплыло

- RLS: `places`, `place_photos` — RLS **off** (читаются anon-ключом через PostgREST). Остальные таблицы — RLS on, 0 политик (gateway ходит service_role / прямым пулом). Дизайн с main, не dev, но владельцу безопасности — решить осознанно.
- `rec_served_items`: 200 строк × `score_components jsonb` на каждую выдачу = 191 KB. Retention (TASKS_52) чистит `events_raw` по экспорту; рост `rec_served_items` — проверить, что тоже под ретеншеном.
- `GET /v1/cities` — агрегат по всей таблице на каждый запрос (417 мс cold), три города. Кандидат на кеш/константу.
- README rollback-скриптов декларирует «не дропать колонки и данные», а `2026-08-28_023` делает и то, и другое.

## 6. Выводы для переноса dev → main

1. Схема на проде = 023. Любой перенос кода в main должен работать против неё (см. совместимость в §1). Откатывать схему ради «чистого» main смысла нет — данные (Berlin, event log, системные списки) уже живые.
2. Три перф-фикса из §4 — **только тела SQL-функций**, независимы от решения по merge и от кода gateway (сигнатуры не меняются). Можно шипить первыми, до разбора остального dev.
3. Инстанс маленький; после фиксов §4 нагрузка на кеш упадёт в разы, но `work_mem` 2 MB и 224 MB буферов останутся потолком — апгрейд compute-тира держать как отдельный рычаг, не как первый шаг.

## 7. Заведено в Linear (2026-09-16)

Проект **«Perf: лента, поиск, фото»**: SLO-3 перф-стенд (блокирует фиксы) · SLO-4 `search_places` (§4.1) · SLO-5 `feed_fallback_places` (§4.2) · SLO-6 pills → fallback (§4.2) · SLO-7 `feed_places_by_source_ids` (§4.3) · SLO-8 `/v1/cities` · SLO-9 прогрев рекомендера · SLO-10 доставка фото · SLO-11 Supabase compute · SLO-12 неиспользуемые индексы (§2).

Плоские в `sloco`: SLO-13 бэкфилл `price_level` после импорта (§1) · SLO-14 `rec_served_items` retention (§5) · SLO-15 учёт миграций (§1) · SLO-16 rollback 023 vs README (§5) · SLO-17 RLS на `places`/`place_photos` (§5).
