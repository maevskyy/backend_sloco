# Walk Planner: integration guide (gateway and app)

This guide is for the backend developer who wires the `walk-planner` service into `backend_sloco`, and for
the app developer who builds the designed Walk Planner screens. It covers v1 of the service (algorithm
`1.0.0`, API prefix `/v1`).

- The ordered handoff checklist: [`HANDOFF.md`](HANDOFF.md).
- Field-by-field reference: [`API.md`](API.md); the generated schema: [`openapi.json`](openapi.json). The schema
  is exact on field names and JSON types. Where it is looser or stricter than the service (for example an
  edit's `dwell_min`, which the service requires), [API.md §4.0](API.md#40-schema-names-and-the-gaps-of-openapijson)
  lists the difference and the service's behaviour wins.
- Message and error catalogue: [`messages.md`](messages.md) to read, [`messages.json`](messages.json) for
  code.
- Running the service: [`DEPLOY.md`](DEPLOY.md). Versions and upgrades: [`RELEASE.md`](RELEASE.md).

## 0. The rules that matter most

1. **The gateway is the only client.** `walk-planner` is internal and has no authentication. Never publish
   it. The app talks to the gateway; the gateway talks to `http://walk-planner:8000`.
2. **Place ids are strings.** A place id is the Google CID in decimal, for example `"10915586233752676659"`.
   It exceeds 2^53, so a JavaScript number would silently change it. The service refuses JSON numbers. A
   walk place id should equal Supabase `places.source_id` (§3.2 says what is verified and what is not). In the
   gateway's JSON it is called `sourceId`, next to the numeric `placeId` (`number | null`).
3. **Editing is stateless.** `POST /plan` returns, for every variant, an editable `sequence`, plus the
   normalised `request` echo. The client sends them back to `/schedule` or `/insert`. Nothing is stored
   on the server. "Reset" happens on the client.
4. **Favourites come from the server side.** The gateway fills them from the user's saved places (§3.3). The
   app never sends them.
5. **Never cache a plan for another user.** Plans depend on favourites and on the start location.
6. **A valid request that finds nothing is still HTTP 200.** `status` is then `no_candidates` or
   `no_route`, with no variants; the request `messages` say why. Errors are 4xx/5xx with the envelope
   `{"error": {"code", "message", "params"}}`.
7. **Routing quality is always explicit.** Every segment is `streets` or `estimate` (a straight line).
   Show estimates as estimates. The router never changes which places are chosen or their order; it only
   changes the shown times and lines.
8. **Times are city-local.** `TimePoint.local` is the wall clock of `request.timezone`
   (`Europe/Bucharest`). Do not convert it to the phone's time zone.

## 1. Architecture

```text
 app ──HTTPS──▶ gateway (backend_sloco, Fastify, /v1/walks/*)
                 optionalUser · favourites (getSavedSignals) · sourceId ↔ placeId (Supabase)
                 rate limits · timeouts/retries · X-Request-Id · camelCase
                   │  HTTP on the Docker network, no auth
                   ▼
               walk-planner :8000 (FastAPI, uvicorn workers; stateless)
                 ├─ data bundle per city (read-only mount; catalog + taste model)
                 └─ routing chain: osrm-foot :5000 (self-hosted) → ORS cloud → straight-line estimate

 app ──HTTPS──▶ photos: nginx /walk-media/photos_cid/<cid>/<NN>_<label>.jpg   (PHOTO_BASE_URL)
 app ──────────▶ Google Maps / Apple Maps (navigation links from the plan)
```

The service holds no user data and no session. Each request carries everything it needs. Any worker or
replica can serve any request.

## 2. Gateway endpoints

They map 1:1 onto the service. Paths and query parameters are camelCase; the service is snake_case.

| Gateway (app-facing) | Auth | Service call | Timeout | Retries | Cache |
|---|---|---|---|---|---|
| `GET /v1/walks/config?city=&lang=` | optionalUser | `GET /v1/walks/config` | 5 s | 1 on a network error | yes, across users (§3.7) |
| `POST /v1/walks/plan` | optionalUser | `POST /v1/walks/plan?lang=&geometry=` | **20 s** (at least 15 s) | only for `503 busy` / `not_ready` / connection refused (§3.5) | never across users |
| `POST /v1/walks/schedule` | optionalUser | `POST /v1/walks/schedule?lang=&geometry=` | 10 s | 1 (idempotent) | no |
| `POST /v1/walks/insert` | optionalUser | `POST /v1/walks/insert?lang=&geometry=` | 10 s | 1 (idempotent) | no |
| `GET /v1/walks/places/search?q=&city=&lat=&lon=&limit=&includeClosed=&lang=` | optionalUser | `GET /v1/walks/places/search` | 5 s | 1 | optional, only without `lat`/`lon` |
| `GET /v1/walks/places/:sourceId?city=&lang=` | optionalUser | `GET /v1/walks/places/{place_id}` | 5 s | 1 | yes, across users |

Not exposed to the app: `GET /v1/health/live`, `GET /v1/health/ready` and `GET /v1/meta`. They are for
operations ([`DEPLOY.md`](DEPLOY.md) §10). The gateway may poll `/v1/meta` to learn the current bundle id
(§3.7).

Document all six routes in Swagger (`docsRoute`). Put the route constants in `src/config/routes.ts` and use
the module pattern `controllers/services/stores/common`. Write a `src/lib/walk-planner-client.ts` that
mirrors `recommendation-client.ts`, with `WALK_PLANNER_URL=http://walk-planner:8000` and the per-call
timeouts above.

### 2.1 Key conversion between gateway and service

Use ONE generic, recursive pair of converters for every body, `toCamel` on the way out and `toSnake` on the
way in. Do not use a field whitelist: a later v1 release may add fields, and an edit must send them back
intact. Apply these exceptions:

| Rule | Service (snake_case) | Gateway (camelCase) |
|---|---|---|
| A place id is a `sourceId` | `place_id` | `sourceId` |
| Lists of place ids | `must_visit_place_ids`, `favourite_place_ids`, `want_to_go_place_ids` | `mustVisitSourceIds`, `favouriteSourceIds`, `wantToGoSourceIds` |
| The gateway adds `placeId` (`number \| null`) next to `sourceId` | stops, `places{}` cards, search results, place detail | `placeId` |
| Data keys stay as they are | the keys of `places` (they are ids) | unchanged |
| Message and error `params` stay verbatim, **including the objects nested in them** | `params.search_radius_km`, `params.place_ids`; the TimePoint `params.finish_at` (`offset_min`, `local`) and the HoursDay `params.hours_day` (`open_24h`, `closed_all_day`, `intervals[].close_day_offset`, `carryover_until`) | unchanged: snake_case inside `params` (they match [`messages.json`](messages.json)) |
| GeoJSON stays verbatim | `geometry.type`, `geometry.coordinates` | unchanged |
| Only keys are converted, never values | enum values such as `on_the_way`, `open_after_wait`, `one_way`, `city_center`, `closed_forever`, `temporarily_closed` | unchanged |

Do not add `placeId` to the `request` echo or to `sequence` items. These two objects make a round trip, and
the app reads `placeId` from `stops[]` or `places{}` instead.

**`lang` and `geometry`.** The gateway may send them as query parameters (as in §2.2) or leave them in the body,
but not both with different values: the service answers that with 422 `validation_error` (`params.field` names
the option, for example `"lang"`). When you move them to the query, delete them from the body.

**Round trips.** Send every key of an echo or a sequence item back, even the ones the app does not use. A missing
key is not an error, it is a different request: an echo without `slots` plans the default slots, and a sequence
item without `kind` becomes a `pinned` stop ([API.md §3.6](API.md#36-post-v1walksschedule)).

Acceptance check for the converters: for every `request` and every `sequence` in
`golden/expected/<bundle_id>/*.json` (a few hundred real objects), `toSnake(toCamel(x))` must equal `x`.
Today no round-tripped key contains `_<digit>`. Output-only keys such as `open_24h` and
`geometry_polyline6` become `open24h` and `geometryPolyline6`.

### 2.2 `POST /v1/walks/plan`

App → gateway (camelCase). Every field is optional, with the defaults of `/config`, except `date`, and
`start` for the shapes `loop` and `one_way`:

```json
{
  "city": "Bucharest",
  "date": "2026-10-03",
  "startTime": "10:00",
  "endTime": "14:00",
  "endDayOffset": 0,
  "shape": "loop",
  "start": {"lat": 44.4355, "lon": 26.1025},
  "style": "max",
  "slots": [{"activity": "sight"}, {"activity": "coffee", "dwellMin": 15}, {"activity": "park"}, {"activity": "food", "dwellMin": 120}],
  "mustVisitSourceIds": ["10915586233752676659"],
  "variants": 3,
  "radiusKm": 2.5,
  "fillWindow": true,
  "knownHoursOnly": false,
  "lang": "ru",
  "geometry": "polyline6"
}
```

- `start` is `{"lat", "lon"}`, `{"sourceId"}` (a catalog place) or `"city_center"`. Send nothing for the
  shape `free`; the service ignores a start there. `loop` and `one_way` without a start get 422
  `start_required`.
- `slots` is ordered (the order of the walk), at most 8, and each activity at most once.
- `date`, `startTime` and `endTime` must be exactly `YYYY-MM-DD` and `HH:MM`, in the city's time.
- `endDayOffset: 1` means the window ends the next day. Without it the service infers it (end ≤ start
  means the next day).

What the gateway does, in order:

1. **Auth** with `optionalUser`. The user may be anonymous.
2. **Clean the body.** Drop `favouriteSourceIds`, `wantToGoSourceIds`, `topK` and `debug` if the app sent
   them: favourites come from the server, and the other two are bench-only. Refuse a body over 1 MiB.
3. **Favourites.** Logged in: §3.3. Anonymous: none.
4. **Convert** to snake_case (§2.1). Send `lang` and `geometry` as query parameters, `X-Request-Id`, and
   `Content-Type: application/json`.
5. **On 200:** collect the place ids of `places{}` (every stop of every variant is there). Resolve them in
   one batch to `places.id` (§3.2; the `feed_places_by_source_ids` RPC pattern, if it does what we were told). Add
   `placeId` (`number | null`) to each card and each stop. Rewrite photo URLs if you chose to (§3.4). Convert to
   camelCase. Return 200.
6. **On an error:** pass the envelope and the status through (§3.8). `Retry-After` handling is in §3.5.

Gateway → app (abridged; `placeId: 812` is illustrative):

```json
{
  "planId": "…40 hex…",
  "versions": {"api": "v1", "algorithm": "1.0.0", "catalog": "bucharest-20261002-d68a311e", "interest": "walk_interest_v1", "routing": "romania-clip-261001"},
  "status": "ok",
  "request": {"city": "Bucharest", "date": "2026-10-03", "startTime": "10:00", "endTime": "14:00", "endDayOffset": 0,
              "shape": "loop", "start": {"lat": 44.4355, "lon": 26.1025, "sourceId": null}, "style": "max",
              "slots": [{"activity": "sight", "dwellMin": null}], "mustVisitSourceIds": [], "favouriteSourceIds": ["…"],
              "wantToGoSourceIds": [], "personalizationStrength": 0.5, "variants": 3, "radiusKm": 2.5, "fillWindow": true,
              "knownHoursOnly": false, "topK": 8, "timezone": "Europe/Bucharest", "weekday": 5, "windowMin": 240,
              "windowStart": "2026-10-03T10:00", "windowEnd": "2026-10-03T14:00", "searchRadiusKm": 1.39,
              "catalogVersion": "bucharest-20261002-d68a311e"},
  "personalization": {"mode": "favourites", "strength": 0.5, "favouritesUsed": ["…"], "favouritesIgnored": [], "profiles": 1},
  "messages": [{"code": "radius_shrunk", "severity": "info", "scope": "request", "stopIndex": null,
                "params": {"radius_km": 2.5, "search_radius_km": 1.39, "reason": "reach"}, "text": "…"}],
  "variants": [{
    "index": 0, "edited": false,
    "summary": {"stopsTotal": 7, "slotsRequested": 4, "slotsFilled": 4, "onTheWay": 3, "pinned": 0, "droppedSlotIndices": [],
                "finishAt": {"offsetMin": 224.7, "local": "2026-10-03T13:45"}, "finishKind": "return", "windowMin": 240,
                "totalMin": 224.7, "walkMin": 19.7, "dwellMin": 205, "waitMin": 0, "distanceKm": 1.48, "slackMin": 15.3,
                "overBudget": false, "routing": "streets"},
    "messages": [{"code": "extras_added", "severity": "info", "scope": "variant", "stopIndex": null, "params": {"count": 3}, "text": "…"}],
    "stops": [{"index": 0, "number": 1, "sourceId": "3676382838556882236", "placeId": 812, "name": "\"Theodor Aman\" Museum",
               "lat": 44.44025, "lon": 26.09813, "kind": "slot", "slotIndex": 0, "activity": "sight",
               "arrival": {"offsetMin": 0.6, "local": "2026-10-03T10:01"}, "visitStart": {"offsetMin": 0.6, "local": "2026-10-03T10:01"},
               "departure": {"offsetMin": 60.6, "local": "2026-10-03T11:01"}, "dwellMin": 60, "dwellFixed": false, "waitMin": 0,
               "hours": {"status": "open", "day": {"weekday": 5, "open24h": false, "closedAllDay": false,
                         "intervals": [{"open": "10:00", "close": "18:00", "closeDayOffset": 0}], "carryoverUntil": null}},
               "businessStatus": "operational", "interest": 0.61}],
    "segments": [{"index": 0, "from": {"kind": "start", "stopIndex": null}, "to": {"kind": "stop", "stopIndex": 0},
                  "walkMin": 0.6, "distanceM": 45, "depart": {"offsetMin": 0, "local": "2026-10-03T10:00"},
                  "arrive": {"offsetMin": 0.6, "local": "2026-10-03T10:01"}, "quality": "streets", "provider": "osrm",
                  "geometryPolyline6": "…"}],
    "navigation": {"googleParts": ["https://www.google.com/maps/dir/?api=1&travelmode=walking&…"],
                   "legs": [{"segmentIndex": 0, "google": "https://…", "apple": "https://maps.apple.com/?…"}]},
    "bbox": [26.0959, 44.4367, 26.0995, 44.4403],
    "sequence": [{"sourceId": "3676382838556882236", "kind": "slot", "slotIndex": 0, "activity": "sight", "dwellMin": 60, "dwellFixed": false}]
  }],
  "places": {"3676382838556882236": {"sourceId": "3676382838556882236", "placeId": 812, "name": "\"Theodor Aman\" Museum",
             "typeLabel": "historic house museum and art museum", "rating": 4.7, "ratingCount": 710, "summary": "…", "summaryLang": "en",
             "photos": [{"key": "photos_cid/3676382838556882236/00_all.jpg", "url": "https://<host>/walk-media/photos_cid/3676382838556882236/00_all.jpg"}],
             "googleMapsUrl": "https://www.google.com/maps?cid=3676382838556882236", "address": "…", "businessStatus": "operational",
             "lat": 44.44025, "lon": 26.09813, "priceLevel": null}}
}
```

### 2.3 `POST /v1/walks/schedule` and `POST /v1/walks/insert`

```json
// POST /v1/walks/schedule: re-time the stops EXACTLY in this order
{"request": { …the request echo, unchanged… },
 "sequence": [ …sequence items, reordered / removed / restored / with new dwellMin… ],
 "variantIndex": 0, "lang": "ru", "geometry": "polyline6"}

// POST /v1/walks/insert: add one place where it costs the least time, then re-time
{"request": {…}, "sequence": […], "variantIndex": 0,
 "sourceId": "10915586233752676659", "allowTemporarilyClosed": false, "dwellMin": null}
```

The response is `{versions, request, variant, places, messages}`, plus `insertedIndex` from `/insert`. The
gateway passes `request` and `sequence` through with only the key conversion. It does not touch the
favourites in the echo, and it adds `placeId` to `variant.stops` and `places{}` exactly as for a plan.

Insert outcomes the app must handle:

| Answer | Meaning | UX |
|---|---|---|
| 200 + `insertedIndex` | Inserted at its best position | Highlight the new stop. Times may move; check `overBudget`. |
| 409 `place_already_in_route` | Already in the sequence | "Already in your route" |
| 422 `place_closed_forever` | Google marks it permanently closed | Disable "Add" (the search result already says `closed_forever`) |
| 422 `place_temporarily_closed` | Temporarily closed and `allowTemporarilyClosed` was false | Ask "Temporarily closed — add anyway?", then resend with `true`. The stop then carries a badge and a stop message. |
| 404 `unknown_place` | Not in the walk catalog | "This place isn't available for walks" |
| 409 `catalog_changed` | The data bundle changed since the plan, and a stop of the sequence no longer exists | "Place data was updated — rebuild the route": call `/plan` again with the same form |

### 2.4 `GET /v1/walks/config`

This returns everything the form needs for one city: the activities with codes, `label`, `label_ru`,
`label_en`, `group` and `base_dwell_min`; the styles and shapes; the `dwell_choices`
(`[10, 15, 20, 30, 45, 60, 75, 90, 120, 150, 180, 240]`); `defaults`; `limits`; `center`; `bbox`;
`timezone`; `versions`; and `cities`. Build every picker from it, never from hard-coded labels.
`limits.max_must_visits` is 10, `limits.window_min` is `[15, 1440]`, `limits.max_edit_stops` is 150, and
so on. Show the shapes in the design's order (`loop`, `one_way`, `free`), not in the order the config lists
them.

### 2.5 Search and place screen

- **Search:** `GET /v1/walks/places/search?q=…&lat=&lon=&limit=20`. It returns
  `{city, query, count, results[], catalog_version}`. Each result has the id, name, `type_label`, rating,
  `rating_count`, `address`, `business_status`, `lat`, `lon`, `google_maps_url`, one `photo`, `match`
  (`exact | prefix | substring | all_words | address`) and `distance_m` (only with `lat`/`lon`). Matching
  ignores diacritics and case. Names come first, then popularity, then distance. Places Google marks
  `closed_forever` appear only with `includeClosed=true`. `temporarily_closed` places are included and
  labelled. `q` needs at least 1 character: there is no "browse nearby" without a query. Results carry no
  opening hours.
- **Place screen:** `GET /v1/walks/places/:sourceId`. It returns the card with up to 10 photos, plus
  `sections[] {key, title_ru, title_en, text}` (text in English), `tags`, `opening_hours_week` (Monday to
  Sunday, `null` when unknown), `opening_hours_known` and `timezone`. Without `city` every served city is
  searched.

## 3. Gateway duties

### 3.1 Auth

Use `optionalUser` for all six routes, as for onboarding: they compute and store nothing. Anonymous users
get the same plans without personalisation (`personalization.mode = "popularity"`). Whether anonymous users
may send onboarding picks as favourites is a product decision. v1 says no.

### 3.2 Id mapping (`sourceId` ↔ `placeId`)

**What we know, and what we assume.** Our onboarding handoff of 2026-07-16 (research repo,
`docs/handoffs/2026-07-16-onboarding-backend-endpoints.md`, not in this package) told us three things about
`backend_sloco`. We have not seen your code or database, so check them first
([DATA.md §6.2](DATA.md#62-what-we-could-not-verify)):

- the recommender's `place_id` (the Google CID) is stored as `places.source_id`, a string;
- an existing RPC pattern, `feed_places_by_source_ids`, resolves source ids to `places.id` in one batch;
- `getSavedSignals()` returns a user's saved places: plain saved places count as favourites, the default
  "want to go" collection as want-to-go.

Whatever the real names are, the gateway needs two lookups:

| Lookup | Input | Output | Rule |
|---|---|---|---|
| CID → app id | a list of CID strings (up to a few hundred per response) | `places.id` per CID, or nothing | match on `(source, source_id)` with the source value of the CID rows, not on `source_id` alone: rows of other sources (older notes show `source: "tripadvisor"`) may reuse the column. Several rows for one CID make the mapping ambiguous; the SQL in [DATA.md §6.3](DATA.md#63-supabase-coverage-gap) lists them |
| saved places → CIDs | a user id | two lists of CID strings, most recent first: plain saved places, and the "want to go" collection | places without a CID (other sources) are dropped |

- **Responses.** Batch-resolve every `sourceId` once per response (`places{}` keys, search results, the place
  screen). Missing rows give `placeId: null`. This is expected: the walk catalog has 12,961 Bucharest places,
  many of them (sights, shopping) probably not in Supabase `places` yet. Walk cards render from the walk payload
  alone, so `null` only disables Supabase features such as saving the place. Cache `sourceId → placeId` per id;
  it changes only when rows are added.
- **Requests.** The walk routes take `sourceId`s only. The app always has them, from walk responses, walk search
  and saved places. Do not let `GET …/places/:sourceId` also accept a numeric `placeId`: both ids are strings of
  digits, so the route cannot tell them apart. If the app ever needs a lookup by `placeId`, give it its own
  field or route (for example a `placeId` body field), resolve it to `source_id`, and answer 404 `unknown_place`
  when there is none.
- **Before launch,** run the batch lookup over all ids of the bundle and record the coverage
  ([DATA.md §6.3](DATA.md#63-supabase-coverage-gap) exports the ids). Product decides whether to import the
  missing places into `places` or live with `placeId: null` ([HANDOFF.md §4](HANDOFF.md#4-open-questions-for-the-product-owner-and-the-backend),
  question 1).

### 3.3 Favourites

Personalisation comes from the user's saved places (the second lookup of §3.2; `getSavedSignals(userId)` if it
works as described to us):

| Service field | Source (the user's saved places) | Weight in the taste model |
|---|---|---|
| `favourite_place_ids` | plain saved places (not in a collection) | 1.0 |
| `want_to_go_place_ids` | the default "want to go" collection | 0.55 (a place in both lists counts as a favourite) |

- Map them to `source_id` strings. Send each list most recent first, and dedupe.
- **Cap:** at most **500 ids in total** (favourites first). Above that the service answers 422
  `validation_error`.
- The model uses at most 200 valid seeds (favourites first). Ids it cannot use are listed in
  `personalization.favourites_ignored`: unknown ids, places of another city, places without a text
  embedding. Pre-filter to the plan's city when Supabase knows the city, so that the cap keeps useful ids.
- **Anonymous** users and users without saved places: send nothing. The plan is ranked by popularity.
- Personalisation changes which places are chosen, not how a route is timed. A plan with favourites takes
  about 50–70 ms more than without, and an edit whose echo carries favourites 30–55 ms instead of 3–4 ms,
  because the taste model runs again ([SPEC.md §10](SPEC.md#10-performance-and-capacity)).
- `personalization_strength` (0..1, default 0.5) is the share of taste in the ranking: 0 is pure
  popularity (then `favourites_used` is empty, see [API.md §4.6](API.md#46-personalization)). The designed
  screens have no control for it; leave it unset.
- The favourites travel inside the `request` echo to the same user and back with edits. That is intended:
  an edit reports the stops' interest with the same taste. Do not refill or strip them on edits.

### 3.4 Photos

Every card carries `photos: [{key, url}]`. The key is opaque, `photos_cid/<cid>/<NN>_<label>.jpg`; never
build or parse keys. Two ways to serve the files:

- **Pass-through (recommended for v1).** Run the service with `PHOTO_BASE_URL=https://<host>/walk-media`.
  Each `url` is then `PHOTO_BASE_URL + "/" + key`, absolute and ready to render, and the gateway passes it
  through. The nginx `location` is in [`../deploy/photos.nginx.conf.example`](../deploy/photos.nginx.conf.example)
  and the setup in [`DEPLOY.md`](DEPLOY.md) §9.
- **Rewrite.** Leave `PHOTO_BASE_URL` unset (`url: null`) and build URLs at the gateway from `key`. Use this
  when photos must go through a resizing CDN or signed URLs.

Either way: about 3.5 % of places have no photo (12,502 of 12,961 have one), so the app needs a
placeholder. Originals average about 0.4 MB, so plan resized variants before launch.

### 3.5 Timeouts and retries

| Call | Typical (measured, [SPEC.md §10](SPEC.md#10-performance-and-capacity)) | Gateway timeout | Retry |
|---|---|---|---|
| plan, 4-hour walk | 0.3–0.45 s (0.35–0.5 s with favourites) | 20 s (at least 15 s) | No retry after the request was sent: a CPU-bound plan keeps running on the server. Retry only on `503 busy` (after `Retry-After: 2`, at most 2 times, with jitter, within the 20 s), `503 not_ready` (after 5 s, once) or a refused connection (once). |
| plan, 24-hour walk | 3–4 s (55 stops; about 6.5 s when two plans share a worker) | the same | the same |
| schedule / insert | 3–4 ms; 30–55 ms when the echo carries favourites | 10 s | Once on a refused or reset connection, or 503 with `Retry-After`. Both are pure functions. |
| search | 8–15 ms | 5 s | once |
| place / config | ~1 ms | 5 s | once |

Add up to `WALK_ROUTING_DEADLINE_S` (6 s) of street routing per plan **and per edit**, used only when the
routers are slow or failing. That is why the plan timeout is above 15 s and the edit timeout is 10 s: with OSRM
hanging an edit took 4.2 s, with OSRM and ORS both hanging 6.0 s ([`ROUTING.md`](ROUTING.md) §8). Never retry
a 4xx.

Use a 1 s connect timeout and keep-alive. The service keeps idle connections for 65 s, longer than Node's
pool, so reused sockets are not reset. During a restart or a bundle switch the container refuses
connections for about 5–10 s. Retry a refused connection once after 2–5 s, then answer 503
`walk_planner_unavailable`.

### 3.6 Rate limits and concurrency

Suggested starting values, per user id, or per IP for anonymous users:

| Route | Limit | Why |
|---|---|---|
| plan | 6/min and 60/h | CPU-heavy: 0.4–5 s of a core each |
| schedule + insert | 60/min | Every drag or stepper change is a call; the app debounces |
| search | 60/min | The app debounces at about 300 ms and needs 2 characters |
| place | 120/min | |
| config | none | served from the cache |

Answer 429 `rate_limited` with `Retry-After`, in the same error envelope.

The service runs at most `WEB_CONCURRENCY × WALK_MAX_CONCURRENT_PLANS` heavy requests at once (2 × 2 = 4 by
default). It refuses the next one at once with 503 `busy` instead of queueing. A gateway-side semaphore of
the same size, with a short wait queue (at most about 5 s), avoids most `busy` round trips. With clients
that retry, a burst of 30 large plans was fully served by the default guard ([`DEPLOY.md`](DEPLOY.md) §12).

### 3.7 Caching

| What | Across users? | Key / TTL |
|---|---|---|
| config | yes | `walks:config:<city>:<lang>:<versions.algorithm>:<versions.catalog>`; TTL 10 min, or poll `/v1/meta` every minute and drop the entry when `bundles[].bundle_id` changes |
| place screen | yes | `<sourceId>:<lang>:<catalog version>`; 1 h |
| `sourceId → placeId` | yes | per id; refresh daily |
| search | only without `lat`/`lon` (coordinates are personal) | `<city>:<lang>:<q>`; 10 min; low value, optional |
| plan, schedule, insert | **never** | They depend on favourites and on the start location. `plan_id` is deterministic (sha1 of the normalised request and the versions), but caching one user's plans is not worth it in v1. |

### 3.8 Errors

Pass the service's error envelope and HTTP status through unchanged:
`{"error": {"code", "message", "params"}}`. `message` is in the request's `lang`; `params` are verbatim (§2.1).
The full catalogue with params, examples and statuses is [`messages.md`](messages.md) (and
[`messages.json`](messages.json)). Add the gateway's own failures in the same envelope:

| Gateway code | Status | When |
|---|---|---|
| `walk_planner_unavailable` | 503 + `Retry-After: 5` | connection refused or reset after the retry |
| `walk_planner_timeout` | 504 | the gateway timeout (§3.5) |
| `rate_limited` | 429 + `Retry-After` | §3.6 |
| `busy` (passed through) | 503 + `Retry-After: 2` | still busy after the retries |

The app handles `code`. Codes are append-only: treat an unknown error code by its HTTP status, and an
unknown message code as information (§4.6).

### 3.9 Request ids

Send the gateway's request id as `X-Request-Id` on every call. The service echoes it in its response
header and in every log line of that request when it matches `^[A-Za-z0-9._:\-]{1,128}$`; otherwise it
makes its own and returns it. Log the id at the gateway and return it to the app, so a support ticket can
be traced through both logs. The service also returns `X-Process-Time-Ms`.

### 3.10 Logging and privacy

- **Do not log walk request or response bodies at the gateway.** They hold the user's location, their
  favourites and routes that reveal where they will be.
- **Log:** route, status, duration, service request id, `status` of a plan, error code, user id (hashed
  if your policy asks for it).
- **Never send user ids or e-mails to the service.** The service logs favourites only as a count plus a
  salted digest, coordinates rounded to 2 decimals (about 1 km), and the length of search queries
  ([`DEPLOY.md`](DEPLOY.md) §11).

### 3.11 Payload sizes and geometry

These were measured on Bucharest with GeoJSON and straight-line geometry, compact JSON before gzip, without
`PHOTO_BASE_URL` (photo URLs add about 10 %: S01 63 KB, S08 272 KB):

| Response | Size |
|---|---|
| plan, 3 variants of a 4-hour walk (S01) | ~57 KB (~66 KB with 3 favourites, P01) |
| plan, 24-hour walk (55 stops) | ~245 KB |
| schedule / insert | ~22 / ~24 KB |
| search (3 results) | ~2 KB |
| place screen | ~5 KB (~6 KB with photo URLs) |
| config | ~2.6 KB (`en`), ~2.8 KB (`ru`) |

Street geometry has 2–63 points per segment (median 19). Ask for `geometry=polyline6` for the app: an
encoded polyline with precision 1e-6, standard `(lat, lon)` order inside the string, which Google and
Mapbox decoders read with precision 6. GeoJSON coordinates are `[lon, lat]`. Enable gzip at the gateway.

## 4. Contract details the app must get right

### 4.1 Time

- `TimePoint = {offset_min, local}`. `offset_min` is minutes after the departure (a float). `local` is
  `YYYY-MM-DDTHH:MM`, the city's wall clock, rounded to the minute. Show `local` as it is.
- **+1 day.** When the date part of `local` is after `request.date`, show `(+1)`. The window label is
  `request.windowStart`–`request.windowEnd`.
- **Today** is computed in the city's time zone (`config.timezone`), not the phone's.
- **Dates.** The service plans any valid date, past dates too, on the same weekly opening hours; it has no
  calendar of holidays. Offer today and later dates only. Whether to limit how far ahead is open
  ([HANDOFF.md §4](HANDOFF.md#4-open-questions-for-the-product-owner-and-the-backend), question 12).
- **"Start now"** means: the current city time rounded up to 5 or 15 minutes as `startTime`; `endTime` is
  start plus the duration; `endDayOffset: 1` when the end passes midnight.
- The window is 15 min to 24 h. A start equal to the end with `endDayOffset: 1` is 24 h. Below 15 min the
  service answers 422 `invalid_window`.
- Show durations (`walkMin`, `dwellMin`, `waitMin`) rounded to whole minutes, and distances from
  `distanceM` and `distanceKm`.

### 4.2 Stops

| Field | Values | Show |
|---|---|---|
| `kind` | `slot` | the activity label of slot `slotIndex` |
| | `on_the_way` | "on the way, optional": a short stop of 10 minutes or less that the walker may skip; `slotIndex` is null |
| | `pinned` | "Your place": a must-visit or an added place; `activity` may be null |
| `businessStatus` | `operational` | nothing |
| | `temporarily_closed` | a red "temporarily closed (Google)" badge; the stop also has a `place_temporarily_closed` message |
| `waitMin ≥ 1` (`hours.status: open_after_wait`) | | "Arrive {arrival.local}, wait {waitMin} min for opening"; the visit is `visitStart.local`–`departure.local`. A wait under 1 min is shown as none (status `open`), as in the dashboard |
| `dwellFixed` | true | the user set this visit length |
| `interest` | 0..1 | internal ranking value; do not show it |

A `closed_forever` place never appears in a route: the service leaves it out with a request message.

### 4.3 Opening hours

`hours.status` is the check at the time of the visit:

| Status | Meaning | Show |
|---|---|---|
| `open` | open for the whole visit | the day's hours |
| `open_after_wait` | opens after the arrival; the schedule includes the wait (`waitMin`) | wait line (§4.2) |
| `unknown` | no hours in the data (about 36 % of places); treated as open | "opening hours unknown" |
| `closes_during_visit` | closes before the visit ends | ⚠ warning plus the stop message |
| `closed_at_arrival` | closed when the walker arrives | ⚠ warning plus the stop message |
| `not_checked` | the catalog has no hours data at all | nothing |

`hours.day` holds the hours of the visit's weekday (`weekday`, 0 = Monday):
- `intervals[] {open, close, closeDayOffset}`; `closeDayOffset: 1` means "until 02:00" past midnight;
- `open24h`;
- `closedAllDay`;
- `carryoverUntil` (last night's opening still running, for example "until 03:00 (from Friday)").

Format these on the client. The planner never drops or swaps a stop because of hours in manual edits; it
flags it.

### 4.4 Routing quality

| Where | Values | Show |
|---|---|---|
| `segments[].quality` | `streets` (`provider` osrm or ors: the street path and its duration) or `estimate` (`provider` haversine: a 2-point straight line, × 1.35 detour at 4.5 km/h) | estimate: a dashed straight line and "≈" before the minutes |
| `summary.routing` | `streets`, `estimate`, `mixed` or `none` (no segments) | "by streets" / "≈ estimate" next to the walking KPI |
| variant message `routing_estimate` `{segments_estimated, segments_total}` | info | one line, for example "Walking times are straight-line estimates — the street route may be longer" |

Plans are always optimised with the estimate. Street routing changes the shown times and lines only: an
arrival may move by a few minutes, a stop may become flagged, and `overBudget` may change. Places and
order never change. If the routers fail, the service still answers and marks every affected segment
`estimate`. It never falls back silently.

More rules for the map ([`ROUTING.md`](ROUTING.md) §4.2 has the full table):

- For `mixed`, draw street segments solid and estimated ones dashed. Put "≈" on the estimated legs and on
  every time after the first of them.
- An estimated `distanceM` is the straight line × 1.35, not the straight line itself.
- Draw stop pins at `stops[].lat`/`lon`, not at the ends of a street line, which are snapped to the
  nearest walkable way.
- To upgrade a stored estimate plan once routing works again, send the variant's unchanged sequence to
  `/schedule`. Same stops, router times; the result is `edited: true`.
- **Attribution.** When any segment's `provider` is `osrm` or `ors`, show "© OpenStreetMap contributors".
  When any is `ors`, show "© openrouteservice.org by HeiGIT | Map data © OpenStreetMap contributors". The
  API has no attribution field.

### 4.5 Summary KPIs and the variant state

| Design KPI | Fields |
|---|---|
| Stops "4 of 4 + 1 (+📍1)" | n of `slotsRequested` + `onTheWay` (+📍 `pinned`), where n = `stopsTotal − onTheWay − pinned` (the `slot` stops), so the three counts add up to `stopsTotal`. This is the dashboard's formula ([API.md §6.1](API.md#61-stop-kinds)). Do not use `slotsFilled` for n: it also counts pinned stops that fill a slot, which are already in `pinned` |
| "Return 13:43" / "Finish 13:43" | `finishAt.local`; `finishKind`: `return` = back at the start (loop), `finish` otherwise |
| "+17 min spare" / "−18 min, over" | `slackMin` (= `windowMin − totalMin`); `overBudget: true` when negative, plus the `over_budget` message `{total_min, window_min, finish_label, window_end_label}` |
| "On foot 0.7 km · 8 min · by streets" | `distanceKm`, `walkMin`, `routing` |
| "Window 10:00–14:00" | `request.windowStart` / `request.windowEnd` |
| Dropped slots | `droppedSlotIndices` (requested slots that did not fit) plus the `slots_dropped` message `{activities, slot_indices, window_end_label}`; fix: a later `endTime` and a new plan |
| "edited" tag | `variant.edited` (true after `/schedule` or `/insert`) |

`status` of the plan: `ok` (at least one variant), `no_candidates` or `no_route` (zero variants and an
error-level request message). Fewer variants than requested come with the `fewer_variants` message.

### 4.6 Messages and localisation

- A message is `{code, severity, scope, params, stopIndex, text}`.
- **Scope.** `request` messages are in `plan.messages`; show them above the variants. `variant` and `stop`
  messages are in `variant.messages`; stop messages carry `stopIndex` and belong on that stop's card.
- **Severity.** `info` is a neutral note; `warning` is a yellow banner or badge; `error` appears only at
  request level, as `no_candidates` or `no_route`, which are empty states.
- **Codes.** Request scope: `radius_shrunk`, `slots_no_candidates`, `no_candidates`, `no_route`,
  `fewer_variants`, `must_visit_closed_forever`, `unknown_place_ids`, `personalization_unavailable`.
  Variant scope: `extras_added`, `slots_dropped`, `over_budget`, `routing_estimate`, `route_empty`. Stop
  scope: `stop_hours_conflict`, `place_temporarily_closed`. Codes are append-only: show the `text` of an
  unknown code as `info`.
- **Language.** `text` and error `message` are rendered in `lang` (`ru` or `en`; default `ru`). The Russian
  texts of the 13 codes the research dashboard already had are its exact wording; the other 22 Russian texts and
  all English texts are drafts ([`messages.md`](messages.md) §1.4). Picker labels come from `/config` (`label`
  in the requested language, plus `label_ru` and `label_en`).
- **Own copy.** For your own wording, for buttons inside messages, or for other languages, render from
  `code` + `params`. [`messages.md`](messages.md) lists every code with its params and example texts in RU
  and EN; [`messages.json`](messages.json) is the same catalogue for code. The design's copy differs in
  places (for example "Unhurried" against the service's "Relaxed" for `chill`); key your strings by code.
- A 422 `validation_error` means the app built a bad request. Its `message` may contain English parser
  wording even in `ru`. Show a generic error and log the request id.
- Place names stay as in Google (Romanian, with diacritics). Summaries, sections and `typeLabel` are in
  English (`summaryLang: "en"`); translation is a roadmap item.

### 4.7 Errors and empty states

| Status | Code | When | UX |
|---|---|---|---|
| 200 | `status: no_candidates` | nothing matches in the (possibly shrunk) radius and window | WPErrNoCandidates: "Widen radius" / "Change the window", then a new plan |
| 200 | `status: no_route` | candidates exist but are closed or don't fit by the time you reach them | WPErrCantAssemble: "Shift start" / "Change slots", then a new plan. The API does not suggest the new time. |
| 422 | `no_slots_or_must_visits` | no slots and no must-visits | WPErrEmptyForm (also check this on the client) |
| 422 | `invalid_window` | window < 15 min or > 24 h | inline on the time pickers |
| 422 | `start_required` | `loop` / `one_way` without a start | ask for a start |
| 422 | `duplicate_activity`, `unknown_activity` | an activity twice / an unknown code | prevent it in the UI (config codes, each once) |
| 422 | `too_many_must_visits` | more than 10 | cap the picker at `limits.max_must_visits` |
| 422 | `unknown_city` | a city not served | only Bucharest today: hide the picker |
| 404 | `unknown_place` | unknown `start.sourceId` (plan), insert id, place screen | "This place isn't available" |
| 409 | `place_already_in_route`, `catalog_changed` | §2.3 | §2.3 |
| 422 | `place_closed_forever`, `place_temporarily_closed` | insert; also `/schedule` when a stop of a saved sequence became permanently closed after a data update | §2.3; on `/schedule`, remove the stop named in `params.place_id` and resend |
| 422 | `validation_error` | malformed request | generic error (§4.6) |
| 413 / 400 | `payload_too_large` / `bad_request` | body over 1 MiB / unreadable JSON | generic error (an app or gateway bug) |
| 503 | `busy`, `not_ready`, `walk_planner_unavailable` | load or restart | "The service is busy — try again" with a retry button |
| 504 / 500 | `walk_planner_timeout` / `internal_error` | timeout / bug | ErrGenerationFailed with the request id |

## 5. Screen-by-screen mapping

The design canvas "SLOCO · Route Planner" (a claude.ai design artifact, not part of this package: ask the research
side for access, [HANDOFF.md §1.2](HANDOFF.md#12-files-that-live-outside-this-package)) has two flows:

- **Section 9, "Walk Planner (from UX brief · Bucharest data)":** artboards `WP*`. This is the brief's §10
  and maps 1:1 onto the v1 API. **Build this flow for v1.**
- **Sections 1–7, the broader "Route Planner" concept:** an 8-step wizard, active-walk mode, saved and
  shared routes. Most of it needs API features that v1 does not have (§7).

| Screen (artboards) | API | Fields → UI | Notes |
|---|---|---|---|
| **How it works** (`WPOnb1`–`WPOnb5`) | none | static | |
| **New walk** (`WPNew`) | `GET /config` once per city and language; `POST /plan` on "Build route" | Day → `date`. "Leave at" → `startTime` (a 15-min step is fine; any `HH:MM` is accepted). "Finish by" → `endTime` + `endDayOffset`. Presets "2 hours / 4 hours / Until evening" → computed `endTime` ("Until evening" has no defined end yet: [HANDOFF.md §4](HANDOFF.md#4-open-questions-for-the-product-owner-and-the-backend), question 11). Shape segmented control → `shape` (`loop` / `one_way` / `free` = "Around area"). "What I want · in this order" → `slots[]` in chip order. Style cards → `style` with `config.styles` labels. | Disable chips already chosen: each activity at most once, 8 slots at most. "From where" → §5.1. "Around area" sends no start. |
| **More options** (`WPMore`) | `GET /places/search` for must-visits | Must visit → `mustVisitSourceIds` (at most 10). Time at each type → `slots[i].dwellMin` (`auto` = null; choices from `config.dwell_choices`; the API allows 5–480). Search radius → `radiusKm` (0.3–50; the design's slider goes to 20). "Add places on the way" → `fillWindow`. "Only places with known hours" → `knownHoursOnly`. Route variants → `variants` (1–5). | A must-visit Google marks closed forever is left out with `must_visit_closed_forever`; a temporarily closed one is kept and flagged; unknown ids give `unknown_place_ids`. |
| **Validation** (`WPErrEmptyForm`) | none (client), or 422 `no_slots_or_must_visits` | | |
| **Building** (`WPLoading`) | the `POST /plan` in flight | an indeterminate progress | The API returns all variants together: no per-variant progress ("Variant 1 ready … Variant 2 routing …" is not possible). "Cancel" aborts the HTTP call; the server finishes the computation anyway. |
| **Variants** (`WPVariants`) | the plan response | One card per `variants[]`: "N stops · X km · back by HH:MM" = `stopsTotal`, `distanceKm`, `finishAt.local` (`finishKind`); spare = `slackMin`; 3 photos = the first stops' `places[sid].photos[0].url`; badges: §5.2. Header = the request summary. | "Edit request" goes back to the form, which the app keeps itself. Variant labels and a "Recommended" badge (WP-32) are not provided. |
| **Route timeline + map** (`WPRoute`) | the selected variant | Map: one line per `segments[]` (`geometryPolyline6` or GeoJSON `[lon, lat]`), numbered markers from `stops[].number`, `lat`, `lon`, the start marker from `request.start` (none for `free`), fit to `bbox` (`[minLon, minLat, maxLon, maxLat]`). KPIs: §4.5. Timeline: start row (the app's own label, e.g. "My location", plus `windowStart`); for each stop: photo, `name`, kind or activity label, `visitStart.local`–`departure.local` and `dwellMin`, the day's hours (§4.3), wait (§4.2), stop messages; between stops: "Walk {walkMin} min · {distanceM} m, leave at {depart.local}" from the segment whose `from.stopIndex` is the previous stop (WP-45); return row for `loop` = the last segment plus `finishAt`. | Variant switcher = `variants[].index`. After every new plan select variant 1 again (WP-33). |
| **Route warnings** (`WPRouteWarnings`) | the same | Request messages above the summary; variant messages under it; stop messages on the stops. "Widen" on `slots_dropped` → a later `endTime`, then a new plan. "Rebuild" → a new plan. "Keep & navigate" → navigation. | The design's **"Replace"** on a closing stop is not supported in v1: offer "Remove" or "Move" in the editor. |
| **Night window** (`WPNight`) | the same | `endDayOffset: 1`; "(+1)" from the `local` dates; night hours from `closeDayOffset` and `carryoverUntil` | |
| **No candidates / can't assemble** (`WPErrNoCandidates`, `WPErrCantAssemble`) | `status` `no_candidates` / `no_route` | The request messages' `text` explains why; `radius_shrunk.params.search_radius_km` is the radius actually searched. | The fix buttons build a new request on the client ("Widen radius to 2.5 km", "Shift start to 11:00"); the API does not compute these suggestions. |
| **Edit route** (`WPEdit`) | `POST /schedule`, `POST /insert` | §6 | "Reset" works on the client without a call. "Not visiting" is the client's bin. |
| **Editor empty** (`WPErrEditorEmpty`) | `/schedule` with `sequence: []` → variant with no stops, no segments and a `route_empty` message (or render it on the client) | | |
| **Parameters changed** (`WPErrParamsChanged`) | none | Compare the current form with the form that built the shown plan; show the banner and keep the old route until "Build". | |
| **Place** (`WPPlace`) | `GET /places/:sourceId` | Gallery = `photos[].url`; `name`, `typeLabel`, `rating` (`ratingCount`), `priceLevel`; `summary` and `sections[]` (`title_ru` or `title_en`); week hours = `openingHoursWeek` (Monday first); `address`; "Open in Google Maps" = `googleMapsUrl`. "In your route: stop 3 · 10:58–11:28 · 30 min" comes from the variant's stop. | "Open now" is computed by the app from `openingHoursWeek` and the current time in `timezone` (§5.3). "How long to stay" and "Remove" are editor actions (§6). "Save" needs a non-null `placeId`. "Share" is an app feature. |
| **Search** (`WPSearch`) | `GET /places/search` | Rows: `photo.url`, `name`, `typeLabel`, `rating` (`ratingCount`), `address`, `distanceM`, a badge for `temporarily_closed`. "Add to route" → `/insert` from the editor, or a must-visit chip from the form. | Search results have **no opening hours** (the design shows "Sat 10:00–22:00"): show them on the place screen. No Russian aliases yet (WP-09): "Атенеум" does not find "Ateneul Român". |
| **Navigation** (`WPNavigate`) | the variant's `navigation` | "Whole route in Google Maps" = `googleParts[]`: one URL, or "Part i/n" when there are more than 9 intermediate points. Each part starts where the previous one ends; part k covers points 10k…10k+10 of [start] + stops + [start for a loop]. "By leg" = `legs[]` with `google` and `apple` URLs; their minutes and metres come from `segments[segmentIndex]`, their names from the stops. | Open the URLs with the OS: the Google Maps app, or Apple Maps. |

### 5.1 Start point ("From where", and the Recent / All / Map picker)

| Option | Request |
|---|---|
| My location (default) | the device position → `start: {lat, lon}` |
| **All** (search) | `/places/search` → `start: {sourceId}` |
| **Map** (pick a point / long-press) | `start: {lat, lon}` |
| **Recent** | No server-side history in v1. Keep a short local list of recent starts on the device (a `sourceId` or `lat`/`lon` with a label), or hide the tab. |
| Enter an address | No geocoder in the API (WP-31). Use the map SDK's geocoder and send `{lat, lon}`, or leave it out. |
| City centre | `start: "city_center"` (dashboard-only; not in the design) |

Check that the point is inside `config.bbox` before planning. A start outside the city returns 200
`no_candidates`, with a misleading "radius narrowed" note; show `ErrOutsideCity` instead.

### 5.2 Badges on the variant cards (proposal)

The design shows badges on each variant card but does not say which data makes them. Until the designer decides
([HANDOFF.md §4](HANDOFF.md#4-open-questions-for-the-product-owner-and-the-backend), question 13), this mapping
uses only fields the API returns. Show at most two or three, in this order:

| Badge | When |
|---|---|
| "Over the window by N min" | `summary.overBudget` (the `over_budget` message) |
| "Dropped: Coffee" | `summary.droppedSlotIndices` not empty (the `slots_dropped` message) |
| "May be closed" | a stop with `hours.status` `closed_at_arrival` or `closes_during_visit` (a `stop_hours_conflict` message) |
| "Temporarily closed" | a stop with `businessStatus: "temporarily_closed"` |
| "N stops with unknown hours" | stops with `hours.status: "unknown"`; neutral, not a warning |
| "≈ times" | `summary.routing` is `estimate` or `mixed` |

Request-level messages (`radius_shrunk`, `slots_no_candidates`, `fewer_variants`, `must_visit_closed_forever`,
`unknown_place_ids`) apply to every variant: show them once above the cards, not as badges.

### 5.3 "Open now" on the place screen

The API gives the week's hours, not an "open now" flag, so the app computes it. `openingHoursWeek[i]` describes
weekday `i` (0 = Monday) as seen at 00:00 of that day: `carryoverUntil` is the opening of the night before that is
still running at midnight, and `intervals` are the openings that start that day, possibly ending after midnight
(`closeDayOffset: 1`). With the current time in `timezone`:

```ts
// week = PlaceDetail.openingHoursWeek (null = unknown hours: show "hours unknown", not open or closed)
function isOpenNow(week: HoursDay[], weekday: number /* 0 = Monday */, hhmm: string /* city time */): boolean {
  const min = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));
  const t = min(hhmm);
  const day = week[weekday];
  if (day.open24h) return true;
  if (day.carryoverUntil !== null && t < min(day.carryoverUntil)) return true;   // last night's opening
  return day.intervals.some(iv => {
    const close = min(iv.close) + 1440 * iv.closeDayOffset;                      // "00:00" + 1 day = midnight
    return min(iv.open) <= t && t < close;
  });
}
```

Take `weekday` and `hhmm` from the current time in the city's zone, not the phone's. JavaScript's `getDay()`
counts from Sunday = 0; convert it with `(getDay() + 6) % 7`.

Real example (Hanu' lui Manuc, `15439727830362881762`): Friday 10:00–02:00 next day, so Saturday has
`carryoverUntil: "02:00"`. Saturday at 01:30 is open (the carry-over), at 09:00 closed, at 23:00 open. Places
open around the clock have `open24h: true` and no intervals.

## 6. Client state for editing

Keep this per plan. `RequestEcho`, `Variant`, `PlaceCard` and `EditStop` are the API objects of
[API.md §4](API.md#4-objects) after the key conversion of §2.1 (`EditStop` is a `variant.sequence` item).
`PlanForm` is the app's own form state, not an API object: the values the user picked, before the service
applies defaults (for example "4 hours" rather than an `endTime`), so the form can be shown again as it was.

```ts
type WalkSession = {
  form: PlanForm;                    // what the user chose: "Edit request", "parameters changed"
  places: Record<string, PlaceCard>; // merged from every response (inserts add cards)
  variants: Array<{
    request: RequestEcho;            // the plan's `request`; after each successful edit of this variant,
                                     // the edit response's `request`. Send it unchanged with every edit.
    planned: Variant;                // as /plan returned it: "Reset", without a call
    current: Variant;                // the last /plan, /schedule or /insert result
    bin: EditStop[];                 // "Not visiting": removed items, restorable as they were
  }>;
  selected: number;                  // 0 after every new /plan
  editSeq: number;                   // increases with each edit call; ignore stale responses
};
```

The `request` is kept per variant because an edit made after a data update returns an echo with the new
`catalog_version`. The other variants keep their plan's version, so a stop of theirs that disappeared is
still reported as 409 `catalog_changed`, not 404. The field-by-field version of this model is in
[`API.md`](API.md) §5.

| User action | Client change | Call |
|---|---|---|
| Reorder (drag) | move the item in `current.sequence` | `/schedule {request, sequence, variantIndex}` |
| Remove (swipe, drag to bin) | take the item out and push it to `bin` | `/schedule` |
| Restore from the bin | take it out of `bin` and put it where it was dropped | `/schedule` |
| Stay stepper | `dwellMin` = the new value (5–480), `dwellFixed: true` | `/schedule`, debounced about 400 ms |
| Add a place | if the place is in `bin`, take it out | `/insert {request, sequence, sourceId, variantIndex}` → highlight `insertedIndex` |
| Add at a chosen position (the design's AddPosition) | after the insert, move it | `/insert`, then `/schedule` with the insert's `sequence`, the new item moved. If the `/schedule` fails, keep the `/insert` result (the place at the planner's position), show the error and offer to move it again: nothing is stored on the server, so there is nothing to roll back |
| Reset ("Reset to planner's route") | `current = planned`, `bin = []` | none |
| Switch variant | `selected = i` | none |
| Change the form | show "parameters changed" | none until "Build" |
| Build again | replace the whole session | `/plan` |

- Send sequence items exactly as received: keep `sourceId`, `kind`, `slotIndex`, `activity`, `dwellMin`
  and `dwellFixed`. Only reorder, remove, re-add or change `dwellMin`/`dwellFixed`. Never send a place
  twice. At most 150 stops per sequence. `dwellMin` is required (5–480), whatever `openapi.json` says, and an
  item without `kind` turns into a `pinned` stop.
- After a successful edit, replace `current` with `response.variant` and that variant's `request` with
  `response.request`, and merge `response.places` into `places`. Every edit result is re-timed from
  scratch, so always replace the whole displayed variant.
- On a 4xx the edit is not applied: keep the previous state and show the error.
- "Reset" needs no call. Re-scheduling the original order would give the same times, but with
  `edited: true` and without the planner's `extras_added` / `slots_dropped` messages.
- In manual edits the planner never drops, swaps or replaces a stop. A stop closed at its new time is kept
  and flagged; a route over the window gets `overBudget` and the `over_budget` message.
- Run edit calls one at a time. When the user edits again while a call is in flight, send the newest
  state next and drop responses older than `editSeq`.
- A session saved on the device can be edited later. After a data update the service answers 409
  `catalog_changed` when a stop no longer exists, or 422 `place_closed_forever` when it became permanently
  closed. Rebuild in the first case; remove that stop in the second.

## 7. Design elements the API does not support yet

| Design element (artboards) | v1 status | What to do |
|---|---|---|
| **8-step wizard** City → Area → Start/End → Time → Interests → Vibe → Preferences → Summary (`City`…`Summary`) | The API takes one plan request. Several wizard steps have no API equivalent (rows below). | Build the one-screen flow (`WPNew` + `WPMore`) for v1. A wizard is possible later if it collects the same fields and calls `/plan` once. |
| City choice (`City`, `CitySearch`, `ErrCityNotFound`, `ErrCityFewPlaces`) | `config.cities` lists the served cities (Bucharest only) | Hide the picker; send `city` anyway. |
| Area: district, draw on map (`Area`, `AreaDraw`) | Only `radiusKm` around the start; `free` searches around the city centre (WP-28) | `AreaRadius` maps to `radiusKm`; the rest is roadmap. |
| End point, "finish at B", "be at the theatre by 19:00" (`EndPoint`, `ErrEndpointNoFit`, the time step) | Not supported (WP-27): `loop` returns to the start, `one_way` ends at the last stop | Roadmap. |
| Interests and Vibe chips (`Interests`, `InterestsFood`, `Vibe`) | The API has 8 **ordered** activity codes and 3 styles | Food → `food`, Drinks → `coffee`/`bar`, Entertainment → `entertainment`, Nature → `park`, Shopping → `shopping`/`market`, Sightseeing and Art & Culture → `sight`. Hidden gems and vibes: no equivalent. |
| Two slots of one type ("Lunch + Dinner", "Bar ×3") | Each activity at most once (WP-13) | Roadmap. |
| Preferences: budget, cycling/transit, max walking distance, avoid crowds, skip visited, accessibility (`Prefs`, `PrefsAdvanced`, `ErrNoAccessibleRoute`) | Walking only; no budget, crowd, history or accessibility filters; no daily distance cap (WP-29) | Roadmap. "Include my saved places" is automatic through favourites (§3.3). |
| Summary "Good fit: 186 matching places, 5–6 stops expected" (`Summary`) | No preview endpoint | Roadmap; or show the request summary only. |
| Pre-checks at input time ("30 min fits one stop", `ErrTooLittleTime`) | Only 422 `invalid_window` (< 15 min) | Estimate on the client from `config.activities[].base_dwell_min` against the window. |
| Per-variant progress (`WPLoading`, `Generating`) | One response with all variants | Indeterminate progress. |
| Variant character labels, "Recommended" (WP-32) | Not provided | Derive simple labels from summaries (shortest walk, most stops), or leave them out. |
| **Replace** a stop with alternatives (`Replace`, `ErrPlaceClosed` "auto-replaced", the "Replace" button in `WPRouteWarnings`) | No alternatives endpoint (WP-35); the planner never auto-replaces | Remove, then add a place through search. |
| Add a place: **On map**, **Saved**, **Nearby** tabs; "Fits · +12 min" per candidate (`AddPlace`, `AddTooFar`) | Only search by name. Saved places work when they are in the walk catalog (else 404). No viewport or nearby browsing. The time cost per candidate would need one `/insert` per candidate. | Search only; "Fits +N min" is roadmap (a batch insertion-cost endpoint). An insert always succeeds and shows `overBudget` instead of "doesn't fit". |
| Customize: "Fewer stops / More stops", "Change end", one "Apply changes" (`Customize`, `CustomizeWarn`, `ErrTooManyStops`) | Reorder, remove, stay and add are supported; there is no stop-count control | "Fewer/More" → toggle `fillWindow` and plan again. Batching locally and calling `/schedule` once on "Apply" works. Limits: 150 stops per edit. |
| Search rows with today's hours (`WPSearch`, `AddPlace`) | Search results carry no hours | Show hours on the place screen. |
| Start picker **Recent** tab, address entry | No history, no geocoder | §5.1. |
| Saved, planned, in-progress, completed and shared routes; freshness checks (`MyRoutes`, `MyRoutesPlanned`, `RouteDetail`, `PlannedRoute`, `SharedRoute`, `ErrSaveFailed`, `ErrLoadRouteFailed`, the home screens' route lists) | Not in v1 (brief §12) | Roadmap. A saved walk = the gateway stores `{request echo, sequence}`. Reopening it = `/schedule`, which re-times the stops on the current data for the saved date, and answers 409/422 if a place is gone or closed. A different date needs a new `/plan`. |
| **Active walk / live mode** (`Active*`, `ErrOffRoute`, `ErrSkippedStop`, `ErrAheadOfTime`, `ErrOfflineWalk`, `ErrRebuildFailed`, `ErrNextClosed`, `ErrSegmentBlocked`, `ErrLocLostWalk`, `ErrRestoreWalk`) | Not in v1: no "re-plan from here at this time"; turn-by-turn navigation is handed to Google or Apple Maps | Roadmap. |
| Completion, ratings, "heart to favourite" (`Completed`, `CompletedEarly`, `RateSheet`) | Not walk-planner features | A heart = a saved place, which becomes a favourite for later plans through `getSavedSignals()`. |
| Home "Recommended routes", "For you" (`Home`, `HomeReturning`) | Not from walk-planner | Roadmap / feed service. |
| Location, offline and map failures (`ErrLocFailed`, `ErrLocInaccurate`, `ErrOutsideCity`, `ErrNoInternet`, `ErrMapFailed`, `ErrRestoreDraft`, `ErrSettingsConflict`) | Client-side states | `ErrOutsideCity`: check `config.bbox`. |
| `ErrClosesBeforeArrival`, `ErrTooFarApart`, `ErrNoResults` | The first is supported as a warning (`stop_hours_conflict`); long legs are only visible as `walkMin`; there are no category or budget filters | |
| Russian or translated place texts | English summaries and sections (WP-41) | Roadmap. |

## 8. Definition of Done

**Gateway**

- [ ] The six routes of §2 exist with `optionalUser`, camelCase, and Swagger docs. Health and meta are not
      exposed.
- [ ] The key converters pass the round-trip check over every `request` and `sequence` of
      `golden/expected/<bundle_id>/` (§2.1). `sourceId` is always a string; `placeId` is `number | null` on
      stops, cards, search results and the place screen.
- [ ] Favourites: a logged-in user with saved places gets `personalization.mode = "favourites"`; an
      anonymous user gets `"popularity"`; a user with more than 500 saved places is capped (no 422).
- [ ] The `placeId` coverage of the bundle's ids is measured and shared with product (§3.2).
- [ ] Photo URLs open (200 `image/jpeg`) for a sample of places; a place without photos renders a
      placeholder in the app.
- [ ] Timeouts and retries follow §3.5. Tested: with `WALK_MAX_CONCURRENT_PLANS=1` and parallel plans,
      `busy` is retried and then returned as 503; with the service stopped, the app gets 503
      `walk_planner_unavailable`; a plan is never re-sent after a timeout.
- [ ] Errors pass through with their status and envelope; the gateway's own codes are documented in
      Swagger.
- [ ] `X-Request-Id` from the gateway appears in the service's access log line.
- [ ] Rate limits are active (§3.6). Walk bodies are not in the gateway logs.
- [ ] The config cache refreshes after a bundle switch.
- [ ] End to end through the gateway:
  - the S01 request (`golden/scenarios.json`, `S01_default`) returns 3 variants;
  - remove → `/schedule`, add (Stavropoleos, `10915586233752676659`) → `/insert`, and reset work;
  - inserting a temporarily closed place (Ryan's Pub, `10366085341954844986`) gives 422, then 200 with
    `allowTemporarilyClosed: true`;
  - a simulated bundle switch gives 409 `catalog_changed`: an edit whose echo has another
    `catalogVersion` and whose sequence holds an id that is not in the catalog.

**App**

- [ ] The form is built from `/config`: labels in the UI language, limits enforced, each activity at most
      once, at most 10 must-visits.
- [ ] Variants, timeline and map follow §4: estimate rendering, OSM/ORS attribution, hours statuses,
      waits, on-the-way and pinned stops, dropped slots, over budget, the night window with "(+1)", all
      times city-local.
- [ ] The editor keeps state as in §6: client-side reset and bin, debounce, stale responses dropped, 409
      and 422 handled.
- [ ] Place screen, search and navigation work, including "Part i/n" for long routes.
- [ ] Every code of §4.7 has a UX; unknown codes fall back by severity or HTTP status.
- [ ] Tested both with the service in estimate mode (routing disabled) and with street routing.

## 9. References

- [`API.md`](API.md): every endpoint and field; §5 is the editing flow.
- [`openapi.json`](openapi.json): the generated schema (`tools/export_openapi.py`); its gaps are listed in
  [API.md §4.0](API.md#40-schema-names-and-the-gaps-of-openapijson).
- [`messages.md`](messages.md) and [`messages.json`](messages.json): every message and error code with
  params, examples and RU/EN texts (generated by `tools/export_messages.py`).
- [`ROUTING.md`](ROUTING.md): routing quality, attribution and the fallback chain.
- [`../golden/expected/`](../golden/expected/): real request/response pairs for 26 scenarios, including
  edit chains. These make good contract-test fixtures.
- `recommendation_system/ai_location_recommender/WALK_PLANNER_UX_BRIEF.md` (research repo, not in this
  package): product behaviour, §10 screens, §12 out of scope.
- `recommendation_system/ai_location_recommender/WALK_PLANNER_UX_TEST_REPORT.md` (research repo, not in this
  package): the WP-xx issues referenced above. Most are planned for v1.1+. Their English titles:
  [SPEC.md](SPEC.md#index-of-user-test-ids). How to get both files: [HANDOFF.md §1.2](HANDOFF.md#12-files-that-live-outside-this-package).
