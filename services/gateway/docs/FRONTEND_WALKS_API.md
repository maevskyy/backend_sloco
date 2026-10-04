# Frontend Walks API

The walk planner: "on Saturday 10:00–14:00 I want a sight, a coffee, a park and lunch"
→ 1–5 timed walking routes through real places, with opening hours checked on arrival,
then edits (reorder, remove, change minutes, add a place) re-timed by the server.

The gateway proxies the private `walk-planner` service (`services/walk-planner/`). Its
documents are the field-by-field reference; this page lists only what the gateway changes:

```text
services/walk-planner/docs/API.md           every field, enum and error
services/walk-planner/docs/INTEGRATION.md   §4–§5: time, editing, screen-by-screen mapping
services/walk-planner/docs/messages.md      message and error codes, params, RU/EN texts
```

Swagger/OpenAPI lists the routes (tag `Walks`):

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

> Status: implemented and tested locally (SLO-67). Until the service is deployed
> (SLO-68) production answers every `/v1/walks/*` call with 503 `walk_planner_unavailable`.

## Endpoints

| Route | Use |
| --- | --- |
| `GET /v1/walks/config?city=&lang=` | Everything the form needs: activities, styles, shapes, `dwellChoices`, `defaults`, `limits`. Build every picker from it. |
| `POST /v1/walks/plan?lang=&geometry=` | Plan. |
| `POST /v1/walks/schedule?lang=&geometry=` | Re-time an edited route in exactly the sent order. |
| `POST /v1/walks/insert?lang=&geometry=` | Add one place where it costs the least time. |
| `GET /v1/walks/places/search?q=&city=&lat=&lon=&limit=&includeClosed=&lang=` | Pickers: must-visit, add a place, start at a place. |
| `GET /v1/walks/places/:sourceId?city=&lang=` | Walk place screen: up to 10 photos, text sections, week hours. |

`lang` is `ru` (default) or `en` — it changes message texts and labels only. Ask for
`geometry=polyline6`: compact segment lines (precision 6, `[lat, lon]` after decoding).

All routes take an optional `Authorization: Bearer <supabase_access_token>`; an invalid
token is 401. A signed-in user's plans are personalised by their saved places — the
gateway fills them in. Anonymous users get the same plans ranked by popularity.

## What the gateway changes

1. **camelCase.** Every key of the service is converted (`start_time` → `startTime`,
   `geometry_polyline6` → `geometryPolyline6`, `open_24h` → `open24h`). Values are never
   converted: enum codes stay `on_the_way`, `one_way`, `city_center`, `closed_forever`.
   Two exceptions stay as the service sends them: `params` of messages and errors
   (snake_case, they match `messages.md`) and GeoJSON `geometry`.
2. **Two place ids.** A walk place is identified by its Google CID, called **`sourceId`**
   — a decimal **string** (it exceeds 2^53; never parse it into a number). Stops
   (`variants[].stops[]`, `variant.stops[]`), cards (`places{}` values), search results and
   the place screen also carry **`placeId`** = the app's `places.id` (`number | null`).
   `null` (≈7 % of Bucharest walk places) means the place is not in the app catalog: show
   it from the walk payload, only "save" is unavailable. Walk routes take `sourceId`s only.
3. **Favourites are server-side.** `favouriteSourceIds`, `wantToGoSourceIds`, `topK` and
   `debug` sent to `/plan` are dropped.

## Plan → edit loop

```json
// POST /v1/walks/plan?geometry=polyline6
{
  "city": "Bucharest",
  "date": "2026-10-03",
  "startTime": "10:00",
  "endTime": "14:00",
  "shape": "loop",
  "start": "city_center",
  "slots": [{"activity": "sight"}, {"activity": "coffee"}, {"activity": "park"}, {"activity": "food", "dwellMin": 120}],
  "mustVisitSourceIds": ["10915586233752676659"]
}
```

`start` is `{"lat", "lon"}`, `{"sourceId"}` or `"city_center"` (none for `shape: "free"`).
A valid request that finds nothing is still **200** with `status: "no_candidates"` or
`"no_route"` and the reason in `messages`.

The response keeps no state on the server. For edits, keep the response's **`request`**
(the normalised echo) and the chosen variant's **`sequence`**, and send them back —
unchanged except for your edit:

```json
// POST /v1/walks/schedule — the stops re-timed in exactly this order
{"request": {"...": "the plan's request echo, unchanged"},
 "sequence": [{"sourceId": "6975675940809821901", "kind": "slot", "slotIndex": 0, "activity": "sight", "dwellMin": 45, "dwellFixed": false}],
 "variantIndex": 0}

// POST /v1/walks/insert — same body plus the place to add
{"request": {"...": "..."}, "sequence": ["..."], "variantIndex": 0,
 "sourceId": "10915586233752676659", "allowTemporarilyClosed": false}
```

Send every key of the echo and of each sequence item back, even ones you do not use: a
missing key is a different request (an item without `kind` becomes a pinned stop). The
answer is `{versions, request, variant, places, messages}`, plus `insertedIndex` from
`/insert`. "Reset" is client-side: keep the original sequence.

## Errors

Planner errors pass through with their status:

```json
{"error": {"code": "place_already_in_route", "message": "Это место уже в маршруте.", "params": {"place_id": "10915586233752676659"}}}
```

Branch on `code`; treat an unknown code by its HTTP status. The ones the screens handle:

| Status | `code` | UX |
| --- | --- | --- |
| 422 | `start_required`, `invalid_window`, `unknown_activity`, `duplicate_activity`, `too_many_must_visits`, `validation_error` | Form problem; `params.field` names the field |
| 409 | `place_already_in_route` | "Already in your route" |
| 422 | `place_temporarily_closed` | Ask "Temporarily closed — add anyway?", resend with `allowTemporarilyClosed: true` |
| 422 | `place_closed_forever` | Disable "Add" |
| 404 | `unknown_place` | "This place isn't available for walks" |
| 409 | `catalog_changed` | Place data updated — plan again with the same form |
| 503 | `busy` (`Retry-After: 2`) | The gateway already retried; offer "try again" |
| 503 | `walk_planner_unavailable` (`Retry-After: 5`) | Gateway: the planner is down or restarting |
| 504 | `walk_planner_timeout` | Gateway: no answer in time (plan 20 s, edits 10 s, others 5 s) |

Gateway-level checks keep the gateway's own shapes: 401 `{"status": "error", "message":
"Unauthorized"}`, and 400 `{"status": "error", "message", "issues"}` for a non-object body,
an unknown `lang`/`geometry`, non-numeric `lat`/`lon`/`limit` or a `sourceId` that is not
1–20 digits. Database overload is 503 `{"status": "error", ...}` with `Retry-After: 1`.

## Times

`TimePoint = {offsetMin, local}`: `local` is the **city's** wall clock
(`request.timezone`, `Europe/Bucharest`) — show it as is, never convert to the phone's zone.
A `local` date after `request.date` means the next day: show `(+1)`. Offer today and later
dates only (the service accepts past dates too). Details: INTEGRATION.md §4.1.

## Caching

Never cache a plan, schedule or insert response across users: they depend on the user's
saved places and start location. Config changes only with a service redeploy
(`versions.catalog`).
