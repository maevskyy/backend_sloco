# TASKS 16: Supabase Auth Foundation

## Goal

Add backend authentication foundation for future saves, taste profile,
onboarding, and personalized ranking.

MVP auth decision:

- iOS performs sign up / sign in with Supabase Auth SDK.
- Backend does **not** implement `/auth/register`, `/auth/login`, or
  `/auth/refresh` in this task.
- Backend verifies Supabase access tokens and serves product APIs.

## Backend Contract

Add protected endpoint:

```http
GET /v1/me
Authorization: Bearer <supabase_access_token>
```

Success response:

```json
{
  "user": {
    "id": "uuid",
    "email": "user@example.com"
  },
  "profile": {
    "userId": "uuid",
    "displayName": null,
    "onboardingStatus": "not_started"
  }
}
```

Auth error response:

```json
{
  "status": "error",
  "message": "Unauthorized"
}
```

Existing public endpoints stay public:

- `GET /v1/health`
- `GET /v1/health/supabase`
- `GET /v1/map/places`
- `GET /v1/swagger/docs`
- `GET /v1/swagger/openapi.json`

## Backend Changes

- Add auth helper/middleware:
  - read `Authorization` header;
  - require `Bearer <token>` format;
  - verify token with `supabase.auth.getUser(token)`;
  - attach authenticated user context for protected routes;
  - return `401` for missing, malformed, expired, or invalid tokens.

- Add `profiles` migration:

  ```sql
  create table public.profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    display_name text,
    onboarding_status text not null default 'not_started',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
  );
  ```

- Enable RLS on `public.profiles`.
- Do not add frontend DB policies yet. Product data access goes through backend
  service role.
- On first valid `GET /v1/me`, create/upsert the profile if it does not exist.
- Add OpenAPI `bearerAuth` security scheme.
- Document `/v1/me` and `401` response in Swagger.

## Frontend Boundary

Backend expectation:

```http
Authorization: Bearer <session.access_token>
```

Frontend must not call Supabase database tables directly. Supabase client usage
on iOS is auth/session only.

## Test Plan

- `GET /v1/me` without token returns `401`.
- Malformed `Authorization` header returns `401`.
- Invalid Supabase token returns `401`.
- Valid token returns user + profile.
- Missing profile is created/upserted on first valid request.
- Existing profile is returned.
- Public routes still work without auth.
- `/v1/swagger/openapi.json` includes `bearerAuth`.
- `/v1/swagger/openapi.json` documents `/v1/me`.

Run:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual checks:

```bash
curl http://65.108.142.55/v1/me
curl -H "Authorization: Bearer <token>" http://65.108.142.55/v1/me
```

## Assumptions

- Supabase Auth is enabled in the existing Supabase project.
- MVP auth starts with email/password only.
- OAuth, Apple Sign In, Google Sign In, password reset, email verification UX,
  and account deletion are later tasks.
- Backend keeps using `SUPABASE_SERVICE_ROLE_KEY`.
- Frontend uses only Supabase URL + publishable/anon key.
- Saves, place details, taste profile, and personalized ranking come after this
  auth foundation.
