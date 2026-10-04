# Changelog

All notable changes of the Walk Planner package and service. Versions follow `walk_planner/version.py`:

- MAJOR breaks the API contract;
- MINOR changes plans on purpose, and the golden outputs are regenerated in the same release;
- PATCH changes no golden output.

Dates are release dates. How releases are made, and the template of an entry:
[docs/RELEASE.md](docs/RELEASE.md).

## 1.0.0 — 2026-10-03

Versions: algorithm 1.0.0 · API v1 · bundle schema 1 · interest walk_interest_v1

Accepted on: `bucharest-20261002-d68a311e` (new) — golden 26/26 in-process and over HTTP against the
image, mini 8/8 · image built from this code. The acceptance runs used local image tags (`fix2`, and `docscheck`
for the documentation check on 2026-10-03); the release image is tagged `sloco-walk-planner:1.0.0` and
`sha-<git12>` once the release is cut ([docs/HANDOFF.md §1.1](docs/HANDOFF.md#11-release-identity)).

The first release as a separate package and microservice. Until now the Walk Planner existed only in the
research dashboard: the core module `recommendation_system/ai_location_recommender/walk_planner.py` plus
the Walk Planner page of `dashboard_app.py`. The algorithm is the dashboard's as of research-repo commit
`f1b2196`. The only changes are the fixes and behaviour changes below. Against the pre-refactor dashboard
(`golden/baseline_v0`, checked with `golden/tools/parity_check.py`), 22 of 24 scenarios are identical, and
S05 and S19 differ exactly by bugs 1 and 2. Of the 32 edit chains, 31 are identical and 1 differs only by
bug 1. All 48 candidate lists are identical.

### Contents

- **Package `walk_planner`.** The former core moved here as `core.py`. The dashboard's page pipeline was
  extracted, without Streamlit, into `slots`, `catalog`, `candidates`, `pipeline`, `present` and
  `messages`. New modules: `interest` and `interest_build` (personalization), `routing` (street routing
  chain), `bundle` (data bundles) and `cli` (developer CLI). The dashboard page is now UI over this
  package. The old module and its test file moved into the package (`core.py`, `tests/test_core.py`).
- **Service `walk-planner`** (FastAPI, `walk_planner/service/`). Endpoints:
  - `GET /v1/health/live`, `GET /v1/health/ready`, `GET /v1/meta`;
  - `GET /v1/walks/config`, `POST /v1/walks/plan`, `POST /v1/walks/schedule`, `POST /v1/walks/insert`;
  - `GET /v1/walks/places/search`, `GET /v1/walks/places/{place_id}`.

  It also has a per-worker load guard (`WALK_MAX_CONCURRENT_PLANS`, 503 `busy`), JSON logs with
  privacy-safe access lines, `X-Request-Id` / `X-Process-Time-Ms` headers, and start-up checks of the
  bundles (sha256, layout, smoke plan). Generated `docs/openapi.json` and `docs/messages.json`
  (15 messages, 20 errors).
- **Messages** are codes with params plus text rendered in `ru` or `en`. The Russian texts of existing
  messages reproduce the dashboard's strings exactly. The English texts and the texts of new codes are
  drafts.
- **Data bundle format v1:**
  - `manifest.json` with the sha256 of every file;
  - `walk_catalog.parquet`;
  - `interest/` (taste artifacts).

  Commands: `python -m walk_planner bundle build | validate | info`. The Bucharest bundle
  `bucharest-20261002-d68a311e` has 12,961 places and takes 60.0 MB.
- **Golden outputs:** `golden/scenarios.json` (S01–S24, plus P01 and P02 with favourites) and
  `golden/expected/bucharest-20261002-d68a311e/`. `python -m walk_planner golden run` checks the package;
  `golden run --url` checks a running service, which is the deployment acceptance test. A synthetic mini
  city with its own bundle and golden set lives in `tests/fixtures/mini/`.
- **Deploy:**
  - `deploy/Dockerfile`: two stages, hash-pinned locks, `python:3.12.15-slim-trixie`, uid 10001, entrypoint
    exit 78 on a missing or corrupt bundle;
  - `deploy/docker-compose.yml`: walk-planner, osrm-foot, optional Redis;
  - `deploy/env.example`;
  - `deploy/osrm/prepare_osrm.sh`: OSRM dataset build, canary, switch, rollback;
  - `deploy/sync_bundle.sh`: verified, atomic bundle and photo sync;
  - `deploy/photos.nginx.conf.example`.

  CI workflow `.github/workflows/walk-planner-ci.yml` in the research repo.
- **Tests:** 663 (offline; real-data tests skip without the data).

### Fixed

- **Bug 1: segment indices.** For a route without a start anchor (shape `free`, or no start),
  `Segment.from_order` / `to_order` were shifted by one stop: `(-1, 0), (0, 1)` instead of `(0, 1), (1, 2)`.
  They now come from a stop counter. Golden S05 changes in exactly 40 values, all of them these indices.
  User test: WP-50.
- **Bug 2: closed places.** The business status was checked only for slot and on-the-way candidates.
  Must-visit places and places added in the editor could be closed without any warning. Now:
  - **`closed_forever`** is never routed. A must-visit closed forever is left out, and the request message
    `must_visit_closed_forever` names it. `/insert` answers 422 `place_closed_forever`, and so does
    `/schedule` for a sequence that contains one.
  - **`temporarily_closed`** is allowed but flagged: the stop message `place_temporarily_closed` and
    `business_status` on the stop and card. `/insert` refuses it with 422 `place_temporarily_closed` unless
    `allow_temporarily_closed: true` is sent (the app confirms with the user).

  Golden S19 shows the change (La Mama left out, Ryan's Pub pinned and flagged). User test: WP-04.
- **ORS host and fallback storm:**
  - The default ORS host is now `https://api.heigit.org/openrouteservice`; `api.openrouteservice.org` is
    deprecated and being shut down.
  - Before, any ORS error made the old provider call ORS once per leg (6 calls for a 5-leg route), and
    the plan silently fell back to straight lines.
  - Now one routing request covers a route, through the chain leg cache → OSRM → ORS (at most 50
    waypoints per request, local rate limiter, quota-aware breaker) → straight-line estimate. It has a
    6 s routing budget per request and a failed router is skipped for the rest of the request.
  - Every segment reports `quality` / `provider`, every variant `summary.routing`, and estimated legs add
    the message `routing_estimate`.

  User test: WP-01.
- **Phantom empty-plan link.** A route emptied in the editor produced a start→start segment, a router call
  and a start→start Google Maps link. An empty route now has no segments, no router call and no navigation
  links; it carries the message `route_empty`.

### Changed (behaviour differs from the dashboard)

- **Personalization:**
  - Favourites now go through the in-package taste model: a numpy port of the v4 feed engine's 6-channel
    blend (text 0.26, photo 0.50, tags 0.08, vibe axes 0.06, quality 0.06, price 0.04) with taste
    profiles. It is rank-blended with the cold-start popularity per catalog theme, with strength 0.5 by
    default.
  - This replaces the dashboard's old personalised path (`_compute_recommendation_df`) in both the
    dashboard and the service.
  - Plans with favourites differ from the old dashboard's. Plans without favourites are unchanged: the
    cold start is byte-identical.
  - A personalised interest map takes 21–26 ms for 3–6 favourites, against about 15 s before.
  - New request fields: `want_to_go_place_ids` and `personalization_strength`. New response block:
    `personalization`.
- **Closed-place policy:** as described under bug 2.
- **Radius:** at most 50 km (the dashboard had no maximum) and at least 0.3 km.
- **Strict formats** (HTTP API):
  - `date` is `YYYY-MM-DD`; times are `HH:MM` (no `9:00`, no seconds).
  - Place ids are JSON strings of 1–20 ASCII digits; numbers are refused.
  - Styles and shapes must be codes; Russian labels are refused (the CLI still accepts them). Activities
    should be codes too. For now the service still maps the dashboard's Russian activity labels to codes,
    and the `request` echo always carries codes.
  - `start` is `"city_center"`, `{lat, lon}` or `{place_id}`.
  - NaN and Infinity are refused.
  - Wrong types are 422 `validation_error`.
- **Limits** (all 422):
  - at most 8 slots, each activity at most once (`duplicate_activity`);
  - at most 10 must-visits (`too_many_must_visits`; the dashboard accepted any number);
  - at most 500 favourite and want-to-go ids;
  - window 15–1440 min, 1–5 variants;
  - visit minutes 5–480;
  - at most 150 stops in an edited sequence.
- **Start:**
  - `loop` and `one_way` require a start (`start_required`).
  - `free` ignores any start. The dashboard never sent one, and the core mis-times such a start.
  - `start.place_id` must be a place of the city (404 `unknown_place` otherwise).
- **Unknown must-visit ids** are reported in the request message `unknown_place_ids`.
- **Stop interest:** `stops[].interest` reports the place's own interest. Variants 2 and later used to show
  the value lowered for variety.
- **Navigation:** the start label of the navigation legs is now a parameter of `core.navigation_links`
  (default «Старт», which the dashboard keeps). The API's navigation links carry no labels, so `lang` does not
  change them.

### Data

- The Bucharest bundle is built with CSV floats parsed exactly (`float_precision="round_trip"`), so a
  bundle built on Linux and one built on macOS get the same id. This rebuild replaced the pre-release build
  `bucharest-20261002-49fc956c`.
- `golden/tools/compare_expected_sets.py` compared 99,642 values between the two expected sets:
  - no substantive differences;
  - 7,077 float differences of at most 3.4e-11;
  - 258 navigation-link coordinates one unit apart in the 6th decimal;
  - 228 identity fields;
  - 366 values in golden S19 variants 2 and 3, where the solver met an exact tie and keeps the same loop in
    the opposite direction, with the same time.

  Details: [golden/README.md](golden/README.md).

### Rollback

First release: there is no earlier service version. The research dashboard keeps working on the same
package.

### Known limitations

v1 keeps the dashboard's planning behaviour on purpose. [docs/SPEC.md](docs/SPEC.md) §13 lists the top 10
limitations from the user test and the code review, with links. The roadmap is in §14. Known defects of the
package's own files, which change no plan (gaps of the generated `openapi.json`, misleading comments and
messages in the deploy files, the release not yet tagged), are listed with their workarounds in
[docs/HANDOFF.md §4.2](docs/HANDOFF.md#42-known-issues-in-the-packages-own-files); they are candidates for 1.0.x.
