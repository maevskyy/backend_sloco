# TASKS 40: Onboarding — `POST /v1/onboarding/similar`

**Status: Planned (awaiting approval).**

Part **3 of 5** of the onboarding feature. Live "similar places" for the two
**off-artifact** cases: (a) the user searched a place (existing
`GET /v1/search/places`) and taps it as a root; (b) a leaf/exhausted node gets
tapped. Everything on the baked tree stays instant (`TASKS_39`); this fires only
off-artifact.

**Depends on `TASKS_5`** (rec-service `POST /v1/recommendations/similar`) and reuses
the `onboarding` module + the id-resolution / photo-rewrite helpers from
`TASKS_39`.

## Context

The gateway is a thin adapter over the rec-service similar endpoint: numeric ids in,
node-shaped cards out. It reuses what `TASKS_39` builds — `feedPlacesBySourceIds`
for source_id↔id + card hydration, and the `photos_cid/`→R2 photo rewrite. `POST`
because the exclude list grows with the session.

## Decisions

- **Thin proxy, no engine logic in the gateway.** All ranking is in the rec-service
  (`TASKS_5`); the gateway maps ids and enriches for rendering.
- **Reuse `TASKS_39` helpers** for id resolution and photo URLs — no duplication.
- **Auth `optionalUser`** (parity with `tree`; anonymous onboarding is allowed).
- **No caching** — off-artifact calls are session/seed specific.

## Changes (`services/gateway`)

1. **`src/lib/recommendation-client.ts`** — add `similarPlaces(request)` (POST
   `/v1/recommendations/similar`), reusing the shared request helper +
   `measureDependencyMetric`.
2. **`onboarding` module** — `POST /v1/onboarding/similar` (`optionalUser`):
   - request `{ "seedPlaceId": number, "city": string, "k": number,
     "excludePlaceIds": number[] }`;
   - resolve `seedPlaceId`/`excludePlaceIds` (numeric) → `source_id`
     (a small id→source_id lookup; the reverse of `feedPlacesBySourceIds`);
   - call `client.similarPlaces({ seed_place_id, city, k, exclude_place_ids })`;
   - map returned `source_id`s back → numeric `placeId`, hydrate the node payload
     and `photoUrls` (reuse `TASKS_39`), drop excludes, take top-k;
   - response `{ "items": [ { sourceId, placeId, name, placeType, rating,
     ratingCount, summary, photoUrls } ] }` — same node shape as the tree, **no
     `children`**.
3. **Docs** — `docs/FRONTEND_ONBOARDING_API.md`: the similar endpoint + when the
   client calls it (search-root / exhausted-leaf), session dedup via
   `excludePlaceIds`.

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- `similar` for a seed → items in node shape, `children` absent, `photoUrls`
  absolute (mocked rec-client + RPC).
- `excludePlaceIds` are not returned.
- **Parity (integration, DoD):** for a seed that is a root inside the artifact with
  empty `excludePlaceIds`, the returned places have **high overlap** with that
  node's baked `children` (not exact — prod scores against extra candidates; near-zero
  overlap ⇒ the rec-service direct-image store didn't load, see `TASKS_5`).
- OpenAPI shows the endpoint.

## Dependencies

- **Upstream:** `TASKS_5` (rec `POST /v1/recommendations/similar`) and `TASKS_39`
  (shared helpers). `TASKS_5` must have the direct-image store loaded, or similars
  diverge from the baked tree.
- **Downstream:** none — completes the gateway onboarding surface.

## Out Of Scope

The baked tree (`TASKS_39`); `complete` (`TASKS_38`); any rec-service change
(`TASKS_5`); a new search endpoint (reuse `GET /v1/search/places`); caching.
