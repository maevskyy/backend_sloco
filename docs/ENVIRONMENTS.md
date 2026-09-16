# Backend Environments

Last updated: 2026-09-16.

One table for the whole system: what runs where, per environment, and what
differs between environments. This is the inventory the dev environment is
built from — dev is not "a second server", it is the right-hand column of the
component matrix below.

## Environments

| Environment | Exists | Purpose | Who deploys, from what |
| --- | --- | --- | --- |
| `prod` | yes | real users | backend owner only, from `main` or a tag (rule; the workflow does not enforce it yet — any `ref` is accepted) |
| `dev` | **planned** — Linear `SLO-20` | anything unverified: branches, migrations, load tests, tools for people | anyone on the team, any ref |

There is no staging/test tier and none is planned. Two environments are enough.

## Component Matrix

Snapshot values are dated; everything else is a rule or a target.

| Component | `prod` (today) | `dev` (target) |
| --- | --- | --- |
| Host | Hetzner dedicated (Ryzen 5 3600, 12 threads, 62 GB RAM, 2×NVMe software RAID1 — snapshot 2026-09-16), `/opt/backend_sloco` | Hetzner Cloud VPS (€5–15/mo), same directory layout |
| Compose | root `docker-compose.yml`; default profile + `observability` | same file, different `.env`; adds a `tools` profile |
| Public edge | host Nginx + Certbot; `sloco.pp.ua/v1/*` → gateway | host Nginx + Certbot; `dev-api.<domain>/v1/*`; public, holds no valuable data |
| Gateway image | GHCR, built by the deploy workflow | GHCR, any ref |
| Recommender image | GHCR, built by the deploy workflow | GHCR, any ref |
| Database / Auth | one Supabase project: managed Postgres + PostGIS, Auth, PostgREST, Supavisor pooler | a **separate** Supabase project — see "Supabase For Dev" |
| Data | real users + full catalog | catalog copied from prod (`places`, `place_photos`, precomputed/onboarding tables — confirm the list when grooming); user-owned tables (`profiles`, `saved_*`, `place_reactions`, `events_raw`, `rec_served_items`) and `auth.users` stay **empty**; test users are created by hand |
| Migrations | applied by hand with `psql` (`SLO-15`) | applied **first** — dev is where a migration is proven before prod |
| Redis | container `redis`, cache only, `appendonly` | container, cache only |
| Photo objects | object storage; the app reads URLs from `place_photos.public_url` | same objects, read-only — nothing to copy |
| Recommender artifacts (`.npy`, `.csv`) | delivered to the host by hand (`SLO-19`) | same artifacts, same delivery |
| Secrets | GitHub repository secrets, one contour (see `DEPLOYMENT.md`) | GitHub Environments `production` / `dev` with per-environment secrets; one workflow with an `environment` input |
| Observability | Grafana + Loki + Prometheus + exporters on the prod host (`observability` profile) | none at first; later the stack moves to the dev/tools host and scrapes prod over the Hetzner private network |
| Tools for people | `sloco-dashboard` (data team UI) runs outside compose — `SLO-29` | their home: dashboard, admin UI (`SLO-30`), analytics — behind one SSO door (`SLO-34`) |
| iOS app | prod config | dev config: dev Supabase URL + anon key, dev API URL — a change in `frontend_sloco` |

## Rules

- `prod` is deployed only by the backend owner, only from `main` or a tag.
  Everything else goes to `dev` first.
- Migrations run `dev` → `prod`, never `prod` first.
- User data never leaves `prod`. The catalog is public data and may be copied.
- If a container is not declared in compose in git, it does not exist.
  (`sloco-dashboard` is the current violation — `SLO-29`.)
- Tools for people live on `dev`, behind one SSO door. The prod host runs only
  what users need.

## Supabase For Dev

Decision pending (`SLO-20` grooming). The options:

| Option | Shape | Verdict |
| --- | --- | --- |
| **A. Second Supabase project** | Same shape as prod: Auth, PostgREST, pooler, Studio. Catalog seeded by script. | **Recommended.** Free tier is 500 MB — the full catalog (≈525 MB `places` + ≈297 MB `place_photos`, snapshot 2026-09-16) does not fit; either Pro ($25/mo) with the full catalog, or Free with a one-city slice (Bucharest). |
| B. Supabase Branching | Preview branch per git branch, seeded from `seed.sql`. | Later, if ever. Requires migrations in the CLI format `<timestamp>_name.sql` and `supabase db push`; ours are `NNN_name.sql` applied by hand. Bundle with `SLO-15`. |
| C. Postgres + PostGIS container in compose | Cheapest, fully local. | No. No Auth, no PostgREST, no pooler — dev would have a different shape from prod and catch the wrong bugs. |

### Seeding dev from prod (sketch)

Schema comes from migrations, data comes from a catalog-only dump:

```bash
# 1. empty dev project: apply services/gateway/supabase/migrations/ in order
# 2. catalog tables only — never profiles / saved_* / place_reactions /
#    events_raw / rec_served_items / auth.*
pg_dump "$PROD_DB_URL" --data-only -t public.places -t public.place_photos \
  | psql "$DEV_DB_URL"
```

Connection strings come from the Supabase dashboard of each project; do not
commit them.

## Order Of Work

1. Supabase dev project → apply migrations → seed script.
2. Dev host: Docker, Nginx, Certbot, DNS record.
3. Deploy workflow: `environment` input, GitHub Environments with per-env
   secrets, protection rule on `production`.
4. iOS dev config.
5. Move tools for people (`sloco-dashboard`) to dev.
6. Optional, later: observability moves to the dev/tools host.

## Related

- Linear: `SLO-20` dev/prod, `SLO-15` migration tracking, `SLO-19` artifact
  delivery, `SLO-29` dashboard outside compose, `SLO-30` admin, `SLO-33` compose
  hygiene, `SLO-34` SSO door, `SLO-35` alerts.
- `ARCHITECTURE.md` — runtime shape and network boundary.
- `DEPLOYMENT.md` — the prod deploy flow and secret list this matrix extends.
- `tasks/TBD_PLATFORM_HARDENING.md` — concern 3 (no staging).
