# Frontend Events API (telemetry intake)

Contract for the iOS event log. The WHAT and WHEN of each event type is defined by
the product spec (`sloco_event_log_frontend_spec.md` in the x-algorithm handoff —
that file is the source of truth for the dictionary and trigger rules); this doc is
the wire contract of the backend half.

## `POST /v1/events`

Send batches, not single events. Auth header is OPTIONAL:

- With a valid bearer token, the server takes `user_id` from the TOKEN — the
  `user_id` field inside events is ignored (nobody can write history for someone
  else).
- Without a token the batch is anonymous: identity is `anon_id` only.
- A present but INVALID token → `401` (fix the session, do not drop the events).

### Request

```json
{
  "batch_id": "uuid-v4",
  "device": { "app_version": "1.4.0", "os": "iOS 27", "locale": "en", "network": "wifi" },
  "events": [
    {
      "event_id": "uuid-v4",
      "event_type": "card_open",
      "client_ts": "2026-08-16T14:03:21+03:00",
      "seq": 1042,
      "anon_id": "device-install-uuid",
      "session_id": "session-uuid",
      "surface": "feed",
      "context": {
        "request_id": "from feed.requestId",
        "position": 5,
        "profile_id": 2,
        "mode": "match_vibe",
        "city": "bucharest"
      },
      "place_id": "1234567890",
      "payload": { "source": "feed_card" }
    }
  ]
}
```

Only three fields decide whether an event is accepted: `event_id` (uuid),
`event_type` (non-empty string) and a parseable `client_ts`. Everything else is
stored as sent. `context.request_id` / `context.position` are extracted for the
training join — put them on every event that originates from a feed card.

### Response — `202` always (when the batch itself is well-formed)

```json
{
  "accepted": 48,
  "duplicates": 2,
  "rejected": [{ "event_id": "…", "reason": "bad_client_ts" }]
}
```

- `duplicates` — events whose `event_id` the server has already stored. **Resending
  a batch after a timeout is the EXPECTED protocol** — keep the same `event_id`s,
  never regenerate them; the resend is free.
- `rejected` — per-event failures (`bad_event_id`, `bad_event_type`,
  `bad_client_ts`, `not_an_object`). Neighbours in the batch are still accepted.
  Do not retry rejected events — they will be rejected again.
- Unknown `event_type` values are ACCEPTED (stored with a flag) — ship new event
  types without waiting for a backend release.

### Limits and retries

- Max **500 events** per batch, max **1 MiB** body → over either limit the reply is
  `429`: split the batch, do not retry it as is.
- `202` → delete the sent events from the local queue. Network error / `5xx` →
  retry the SAME batch with exponential backoff. Other `4xx` → drop the batch
  (it will never succeed).

## Where `request_id` and `position` come from

`GET /v1/feed/places` now returns:

- `feed.requestId` (uuid | null) — the serving id. One id per recommendation
  snapshot: pages (`offset=`), re-sorts (`sort=`) and category cuts of the same
  snapshot share it, and it changes when the snapshot refreshes (~10 min cache or
  changed reaction signals). Dedupe impressions per `(requestId, placeId)`.
- `places[].position` (int | null) — the card's 0-based position INSIDE that
  snapshot. Stable under `sort=distance` and `category=` (unlike `rank`, which is
  positional per page). Echo BOTH into `context` of every event born from that
  card — including `card_open` from a card the user reached via paging.
- Both are `null` on fallback feeds (anonymous / no signals / rec-service down):
  send events with `context.request_id: null` then — that is expected.

## Event dictionary accepted as `known_type=true` today

`impression, map_viewport, card_open, card_dwell, photo_swipe, similar_open,
save_favourite, save_want_to_go, unsave_favourite, unsave_want_to_go, like,
dislike, hide, share, route_click, external_click, search_query,
search_result_click, filter_apply, onboarding_card_like, onboarding_complete,
onboarding_skip, app_open, app_background`

(`like` was added 2026-08-16 after the app started sending it; rows ingested
before that keep `known_type=false` — the flag records what the dictionary knew
at INGEST time and is never backfilled. Treat the dictionary, not the stored
flag, as the source of truth when reading.)

New types are still accepted (see above) — tell the backend so the dictionary and
the action-weights config catch up.

## Privacy rule (from the spec)

Never put precise GPS coordinates into events. `map_viewport` carries a coarse
geohash of the map center — nothing else location-shaped.
