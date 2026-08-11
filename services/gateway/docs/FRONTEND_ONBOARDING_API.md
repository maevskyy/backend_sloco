# Frontend Onboarding API

The write half of the onboarding state (iOS ask `ONBOARDING_STATUS_WRITE`). One call at
the end of the onboarding flow; the state is then readable on any device via `GET /v1/me`.

Swagger/OpenAPI remains the source of truth:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

## Endpoint

```http
POST /v1/onboarding/complete
Authorization: Bearer <supabase_access_token>   (required — 401 without it)
Content-Type: application/json
```

Body:

```json
{
  "pickedPlaceIds": [123, 456, 789],
  "status": "completed"
}
```

| Field | Type | Rules |
| --- | --- | --- |
| `pickedPlaceIds` | `number[]` | The places the user liked during the onboarding deck. May be empty. Max 100. Duplicates are deduped. |
| `status` | `"completed"` \| `"skipped"` | Anything else → `400`. |

Response `200`:

```json
{
  "onboardingStatus": "completed",
  "savedCount": 3
}
```

`savedCount` = picks actually saved. An unknown/stale place id is **skipped, not an
error** — the call still succeeds and the status is still written.

## What it does

1. Each pick becomes a plain saved place (`saved_places`) — **which the feed counts as a
   favourite signal**, so finishing onboarding with ≥1 pick flips the user onto the
   personalized feed path on their next `GET /v1/feed/places`. No extra call needed.
2. Writes `profiles.onboarding_status` — readable back as
   `GET /v1/me → profile.onboardingStatus`.

The call is **idempotent**: repeating it re-saves the same places (no duplicates) and
re-writes the same status. If it fails mid-way, just retry the whole call.

## `GET /v1/me` vocabulary (now documented)

`profile.onboardingStatus` is an enum in the OpenAPI spec:

```text
not_started   the row default — the user never finished onboarding
completed     finished the flow
skipped       explicitly skipped it
```

Branch on `onboardingStatus == "completed"` (or `!= "not_started"` if a skip should also
bypass the flow — product call).

## Client work this unblocks

- Delete the `AuthService.isNewlyCreatedAccount()` stopgap (Supabase
  `createdAt`/`lastSignInAt` heuristic) — branch on `/v1/me` instead. Fixes both known
  holes: quitting onboarding halfway no longer hides the rest of the flow forever, and a
  returning user on a new device is recognized.
- Delete the device-local `OnboardingProgress` UserDefaults flag — the backend is now the
  source of truth.
- Send the deck's liked place ids in `pickedPlaceIds` at the end of the personalization
  flow (the reactions the deck already writes stay as they are — the two signals are
  complementary; duplicates are harmless).

## Example

```bash
curl -X POST "https://sloco.pp.ua/v1/onboarding/complete" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pickedPlaceIds": [123, 456], "status": "completed"}'

curl -s "https://sloco.pp.ua/v1/me" -H "Authorization: Bearer $TOKEN" \
  | jq .profile.onboardingStatus
# "completed"
```
