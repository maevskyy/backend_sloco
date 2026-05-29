# AGENTS.md

## Product Context

This project is an MVP for a personalized city discovery app.

The product is not a Google Maps competitor. It is a taste-based city discovery assistant.

Core promise:

> Help users discover places in a new city that actually fit their taste, lifestyle, energy level, and favorite-place patterns.

The main product feeling to preserve:

> "This app understands what kind of places I would like."

## MVP Scope

Core MVP features:

- City map with recommended places.
- Taste onboarding.
- Favorite places input from user's home city.
- Personalized recommendation layer.
- Save / wishlist system.
- Basic place pages.
- "Why recommended for you" explanation.

Explicitly out of MVP:

- Social network.
- Comments and reviews.
- AI chat.
- Followers.
- Creator economy.
- Events platform.
- Advanced route optimization.
- Real-time ML personalization.
- Worldwide coverage.
- Large auto-generated city graph.

## MVP Geography

Start with 3-4 cities and only central / nomad / social districts.

Candidate cities:

- Berlin
- Tbilisi
- Bucharest
- Kyiv

For initial development, one seeded city is enough.

## Team Responsibilities

- Backend: Node.js service owned by Dimitriy.
- iOS frontend: Swift app owned by frontend teammate.
- Analytics / recommendation model: Python service owned by analytics teammate.

Backend should expose a clean API and keep product logic centralized.

## Preferred Backend Stack

Use:

- Node.js
- TypeScript
- Fastify
- Supabase PostgreSQL
- PostGIS for geo queries
- Zod for validation

Likely ORM/query options:

- Prisma for familiar DX.
- Drizzle if we want SQL-first control.

Do not add Redis/cache/Kafka/microservices for MVP unless there is a concrete bottleneck.

## Architecture Direction

Recommended shape:

```text
iOS app
  -> Node/Fastify backend
    -> Supabase Postgres
    -> Python scoring service later
```

Supabase is infrastructure: database, auth, storage, PostGIS.

Node backend owns:

- API contracts.
- Taste profile writes.
- Map places query.
- Recommendation v0.
- Save/unsave.
- "Why recommended" generation.
- Future integration with Python scoring service.

Avoid putting core business logic directly in the iOS app or spreading it across Supabase Edge Functions unless needed.

## Main Backend Query

The heart of the product is the map refresh endpoint.

Example:

```http
GET /map/places?cityId=bucharest&swLat=44.403&swLng=26.049&neLat=44.468&neLng=26.150&zoom=13&category=coffee
```

Meaning:

> Give me personalized place pins for the current visible map rectangle.

Frontend owns:

- Map SDK.
- Gestures.
- Camera / viewport.
- Zoom.
- Pin rendering.
- Debounced requests when the map region changes.

Backend owns:

- Which places exist.
- Which places are inside the viewport.
- Which places fit the user.
- Saved state.
- Match score.
- Why recommended.

Map endpoint response must be lightweight. Do not return full place detail payloads from the map endpoint.

## Core API Endpoints

Initial endpoints:

```http
GET /health
GET /cities
GET /map/places
GET /places/:id
POST /onboarding/taste-profile
POST /onboarding/favorite-places
POST /places/:id/save
DELETE /places/:id/save
GET /me/saved-places
POST /places/:id/feedback
```

For the very first implementation, start with:

```http
GET /health
GET /cities
GET /map/places
```

Mock data is acceptable before Supabase is wired.

## Map Places Response Shape

Use a compact pin response:

```json
{
  "places": [
    {
      "id": "place_123",
      "name": "Quiet Coffee",
      "latitude": 44.433,
      "longitude": 26.096,
      "primaryCategory": "coffee",
      "vibeTags": ["calm", "specialty_coffee", "work_friendly"],
      "matchScore": 92,
      "matchLabel": "Strong match",
      "whyRecommended": "Because you like calm specialty coffee and work-friendly places.",
      "isSaved": false,
      "thumbnailUrl": null
    }
  ]
}
```

Place details should be fetched separately:

```http
GET /places/:id
```

## Database Design Direction

Use Supabase Postgres with PostGIS.

Important design choice:

- Store place coordinates as `location geography(Point, 4326)`.
- Add a GiST index on `places.location`.

Core MVP tables:

- `profiles`
- `cities`
- `places`
- `place_photos`
- `categories`
- `vibe_tags`
- `place_categories`
- `place_vibe_tags`
- `taste_profiles`
- `taste_profile_categories`
- `taste_profile_vibe_tags`
- `favorite_place_inputs`
- `saved_places`
- `place_feedback`

Important indexes:

```sql
create index places_location_idx on places using gist (location);
create index places_city_id_idx on places (city_id);
create index places_primary_category_id_idx on places (primary_category_id);
create unique index saved_places_user_place_idx on saved_places (user_id, place_id);
```

For very early MVP, latitude/longitude columns are acceptable, but prefer PostGIS from the start because the product is map-first.

## Recommendation V0

Do not build AI magic first.

Use simple scoring:

- Category match.
- Vibe tag match.
- Lifestyle match.
- Favorite-place-derived hints.
- Manual curated score.
- Penalties for mismatch: too noisy, alcohol-heavy, touristy, wrong budget, wrong energy.

Main output:

- `matchScore`
- `matchLabel`
- `whyRecommended`

The explanation is part of the product magic. Treat it as core, not decoration.

## Frontend Core Screens

iOS MVP should prioritize:

- Taste onboarding.
- Favorite places input.
- Map home.
- Place preview bottom sheet.
- Place details.
- Saved places.
- Taste profile edit.

Important buttons:

- Continue
- Add favorite place
- Finish
- Save / Saved
- Not for me
- Filters
- Current location
- Open in Maps
- Edit taste

## Development Order

Recommended first backend slice:

1. Scaffold Fastify + TypeScript.
2. Add `GET /health`.
3. Add mock `GET /cities`.
4. Add mock `GET /map/places`.
5. Validate query params with Zod.
6. Add docs for API contract.
7. Add Supabase schema.
8. Replace mock places with database query.
9. Add onboarding endpoints.
10. Add save/unsave endpoints.

## Engineering Principles

- Keep MVP narrow and vertical.
- Prefer product flow over abstract architecture.
- Keep map payloads small.
- Keep place details separate from map pins.
- Use seeded curated data early.
- Optimize for recommendation relevance, not raw place count.
- Add caching only after a real bottleneck appears.
- Keep Python scoring integration behind an adapter so local scoring can be swapped later.

