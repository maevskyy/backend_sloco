# Walk Planner: versions and releases

How the Walk Planner is versioned, what a release contains, how the research side cuts one, how the
backend developer upgrades and rolls back, and what stays compatible. The deploy commands are in
[`DEPLOY.md`](DEPLOY.md); the gateway and app contract is in [`INTEGRATION.md`](INTEGRATION.md); the
release history is [`../CHANGELOG.md`](../CHANGELOG.md).

## 1. Version identifiers

| Identifier | Where it shows | v1.0.0 | Changes when |
|---|---|---|---|
| `ALGORITHM_VERSION` (semver) | `walk_planner/version.py`; `walk_planner.__version__`; the package version; `/v1/meta` `version`; `versions.algorithm` in every response; the image tag; the git tag `walk-planner-vX.Y.Z` | `1.0.0` | every release (§2) |
| `API_VERSION` | the URL prefix `/v1`; `versions.api` | `v1` | only on a MAJOR release: a new prefix `/v2`, served alongside `/v1` |
| `BUNDLE_SCHEMA_VERSION` | `manifest.json` `schema_version`; `/v1/meta` `versions.bundle_schema` | `1` | The bundle's file layout or format changes. The service refuses another schema version (the entrypoint exits 78). |
| `bundle_id` | the bundle directory name; `versions.catalog`; `request.catalog_version`; `golden/expected/<bundle_id>/` | `bucharest-20261002-d68a311e` | Every build with different content. Format `<city_slug>-<YYYYMMDD>-<first 8 hex of the content sha256>`. The hash covers the catalog rows, the taste fingerprint, the city, the time zone and the schema, so rebuilding the same data gives the same hash. |
| `INTEREST_VERSION` | the taste model; `versions.interest`; manifest `interest.version` | `walk_interest_v1` | The taste-model maths or artifact format changes. Bundles must then be rebuilt; at least a MINOR release. |
| OSRM `data_version` | `versions.routing` (null while only the estimate is used); `/v1/meta` `routing.dataset` | e.g. `romania-clip-261001` | Each map refresh (`<name>-<YYMMDD of the extract>`). Independent of releases. |
| `plan_id` | plan responses | sha1 | Derived from the normalised request and the versions; it identifies a computation and is not a version. |
| File formats | `docs/messages.json` `format`; golden files `format` | `walk-messages/v1`, `walk-golden/v1` | incompatible change of those files |

## 2. Semantic versioning of `ALGORITHM_VERSION`

The rule is decided by the **golden outputs**: the full API JSON of 26 real scenarios and their edit chains
(`golden/expected/<bundle_id>/`), plus the synthetic mini set (`tests/fixtures/mini/`).

| Bump | When | Golden |
|---|---|---|
| **PATCH** `1.0.x` | Nothing in any existing golden output changes: a fix that does not touch outputs, performance, deploy, docs, a dependency or base-image bump that keeps the golden identical. A **data-only release** (a new bundle with its new expected set, code unchanged) is a PATCH too. | `golden run` passes unchanged on every shipped expected set |
| **MINOR** `1.x.0` | An intended change of plans (places, order, times, texts), or an **additive** API change: a new response field, a new optional request field, a new message or error code, a new enum value, a new endpoint. | regenerated in the same release, with the differences reviewed and summarised in the CHANGELOG |
| **MAJOR** `x.0.0` | An API contract break: a field removed, renamed or retyped, a changed meaning or unit, changed error semantics. | regenerated; the new contract is served under `/v2` |

A change of any expected output is therefore never a PATCH. `walk_planner/version.py` and
`golden/README.md` say the same.

## 3. What a release consists of

1. A **git tag** `walk-planner-vX.Y.Z` in the research repo, on a commit that holds the code,
   `golden/expected/`, the regenerated `docs/openapi.json` and `docs/messages.json`, and the `CHANGELOG.md`
   entry.
2. The **source tarball** `walk-planner-X.Y.Z.tar.gz`: `services/walk_planner` at the tag, about 1.4 MB.
   It holds the package, tests, mini fixture, `deploy/`, `docs/` and `golden/`, so the backend can build and
   run the acceptance. It comes with `walk-planner-X.Y.Z.tar.gz.sha256`.
3. The **data bundle(s)** the release is accepted on, when they are new: `<bundle_id>.tar.gz` (about 56 MB
   for Bucharest; 60 MB unpacked) and its `.sha256`. Or ship them straight to the server with
   `deploy/sync_bundle.sh`. Their expected sets are inside the source tarball.
4. Optionally an **image** `ghcr.io/<org>/sloco-walk-planner:X.Y.Z` (and `:sha-<git12>`).
5. **Release notes:** the CHANGELOG section (§6), sent to the backend developer, with the tag, its 12-hex commit
   (the image's `GIT_SHA`) and the sha256 of every archive. For 1.0.0 these values go into
   [HANDOFF.md §1.1](HANDOFF.md#11-release-identity).
6. **What lives outside the package**, sent along or shared when it changed: the design canvas, the UX brief and
   test report, the CI workflow ([HANDOFF.md §1.2](HANDOFF.md#12-files-that-live-outside-this-package)).

## 4. Release procedure (research repo)

Run everything from `services/walk_planner` unless noted, with `PY=/path/to/venv/bin/python`: the
research venv, or any Python ≥ 3.10 with the package's dependencies. scikit-learn is needed for P02.

1. **Change** code or data on a branch. Planning logic lives only in the package, never in
   `dashboard_app.py` (§9).
2. **Tests:**

   ```bash
   PYTHONDONTWRITEBYTECODE=1 $PY -m pytest -q                                   # package suite
   cd ../.. && PYTHONDONTWRITEBYTECODE=1 $PY -m pytest test -q -p no:cacheprovider && cd -   # research suite (dashboard glue)
   ```

3. **Parity with the pre-refactor algorithm** (the v1.0 gate):

   ```bash
   PYTHONDONTWRITEBYTECODE=1 $PY golden/tools/parity_check.py --source csv
   PYTHONDONTWRITEBYTECODE=1 $PY golden/tools/parity_check.py --source bundle
   ```

   - Exit 0 means only the documented differences remain: bug 1 in S05 and bug 2 in S19. P01 and P02 are
     skipped; they have no baseline.
   - It must stay green for every PATCH.
   - A MINOR that changes plans makes it report differences by design: record them in the CHANGELOG. From
     then on the `expected/` sets are the reference.
   - It needs the research repo (the CSV and `baseline_v0`), so it does not run from a tarball.

4. **Golden** (§5):

   ```bash
   $PY -m walk_planner golden run                         # real set: 26/26 for a PATCH
   $PY -m walk_planner golden run --bundle tests/fixtures/mini/bundles \
       --scenarios tests/fixtures/mini/scenarios.json --expected-root tests/fixtures/mini/expected   # mini: 8/8
   ```

5. **Generated docs.** Regenerate them with the pinned fastapi and pydantic of `deploy/requirements.lock`:
   the JSON schema depends on those versions.

   ```bash
   $PY tools/export_openapi.py && $PY tools/export_messages.py
   $PY tools/export_openapi.py --check && $PY tools/export_messages.py --check
   ```

6. **Version and changelog.**
   - Bump `ALGORITHM_VERSION` in `walk_planner/version.py`.
   - Add a `CHANGELOG.md` entry (§6).
   - New environment variables go into `deploy/env.example`, with a default that keeps the old behaviour.
   - New dependencies go into `deploy/requirements.lock` with hashes, for CPython 3.12 on Linux x86_64 and
     aarch64 (see its header).
7. **CI green.** Both jobs of `.github/workflows/walk-planner-ci.yml`: tests on Linux with the locks, and
   the image build, smoke and mini acceptance.
8. **Commit and tag.** Everything under `services/walk_planner` must be committed: `git archive` (step 9) packs
   only committed files, and the tag's commit is the `GIT_SHA` the backend builds with.

   ```bash
   git status --porcelain services/walk_planner        # must print nothing
   git tag -a walk-planner-vX.Y.Z -m "walk-planner X.Y.Z"
   git push origin walk-planner-vX.Y.Z
   git rev-parse --short=12 "walk-planner-vX.Y.Z^{commit}"   # the GIT_SHA for the release notes
   ```

9. **Artifacts:**

   ```bash
   V=X.Y.Z
   git archive --format=tar.gz --prefix=walk-planner-$V/ -o walk-planner-$V.tar.gz walk-planner-v$V:services/walk_planner
   shasum -a 256 walk-planner-$V.tar.gz > walk-planner-$V.tar.gz.sha256
   # only when the release ships a new bundle:
   B=<bundle_id>
   COPYFILE_DISABLE=1 tar -C ../../recommendation_system/ai_location_recommender/data/walk_bundles -czf $B.tar.gz $B
   shasum -a 256 $B.tar.gz > $B.tar.gz.sha256
   ```

   `COPYFILE_DISABLE=1` keeps macOS `._*` files out of the tarball.
10. **Hand over** the tarballs (or run `sync_bundle.sh`), the image if you publish one, and the release
    notes with the tag, the commit and the sha256 values (§3, item 5). For 1.0.0, fill in
    [HANDOFF.md §1.1](HANDOFF.md#11-release-identity) and send the files of
    [HANDOFF.md §1.2](HANDOFF.md#12-files-that-live-outside-this-package) with it.

### Building a new bundle

```bash
$PY -m walk_planner bundle build --data-dir ../../recommendation_system/ai_location_recommender/data --city-slug bucharest
# -> data/walk_bundles/<city>-<YYYYMMDD>-<sha8>/ (about 10 s for Bucharest; deep validation runs after the build)
$PY -m walk_planner bundle validate <bundle dir> --photos-root ../../recommendation_system/ai_location_recommender/data/visual_photo_profiles/photos_cid
$PY -m walk_planner golden update --bundle <bundle dir>        # writes golden/expected/<new bundle_id>/
```

Build bundles with the package version being released. The builder records itself in `manifest.builder`,
and parses CSVs with `float_precision="round_trip"`, so a build gives the same `bundle_id` on macOS and on
Linux.

## 5. Golden update rules

- **PATCH:** nothing is regenerated. `golden run` passes as is.
- **MINOR or MAJOR:**
  1. Run `golden run -v` and review every difference.
  2. Bump the version.
  3. Run `golden update` (it writes `expected/<bundle_id>/` and `index.json`, and prints NEW, CHANGED or
     SAME per scenario). Regenerate the mini set too (`tests/fixtures/mini/README.md`).
  4. Summarise the differences in the CHANGELOG: how many of the 26 scenarios changed, which fields, and
     why.
- **New data** (new catalog, embeddings or photos = a new `bundle_id`): `golden update --bundle <new>`
  creates `expected/<new bundle_id>/`. Keep the old set as long as a deployment runs the old bundle; the
  acceptance picks the set by the served `bundle_id`.
- **The same data rebuilt** (new builder, another platform): prove before replacing the expected set.

  ```bash
  $PY -m walk_planner golden update --bundle <new bundle> --expected-root /tmp/expected_new
  $PY golden/tools/compare_expected_sets.py golden/expected/<old id> /tmp/expected_new/<new id> --bundles <old bundle> <new bundle>
  ```

  `SUBSTANTIVE` must be 0. `exact_tie` entries (a loop running the other way with exactly equal times, as
  in S19 variants 2 and 3 for `bucharest-20261002-d68a311e`) need a reviewer's sign-off, recorded in
  `golden/README.md`.
- Never edit expected files by hand. Generate them only with `golden update`, on the pinned numeric stack
  (numpy, pandas and pyarrow of `deploy/requirements.lock`; the versions are recorded in `index.json`).

## 6. CHANGELOG entry (template)

```markdown
## X.Y.Z — YYYY-MM-DD
Versions: algorithm X.Y.Z · API v1 · bundle schema 1 · interest walk_interest_v1
Accepted on: <bundle_id> (new | unchanged) — golden 26/26, mini 8/8 · image sloco-walk-planner:X.Y.Z (sha-<git12>)

### Plans (MINOR/MAJOR only)
- what changed and why; golden: N of 26 scenarios changed (fields …)
### API
- additive: new fields / codes / enum values (the gateway must pass them through; the app may ignore them)
- deprecated: … (removed in /v2, not before YYYY-MM-DD)
### Configuration
- new env vars with defaults; changed defaults
### Data
- new bundle: rows, coverage, what changed in the sources
### Operations
- deploy steps beyond the routine (§7), resource changes
### Rollback
- to <previous version> + <previous bundle_id>; constraints, if any
```

## 7. Upgrading (backend developer)

1. **Read the release notes.** They say which API fields or codes are new (and whether the gateway must
   map them), which environment variables are new, whether a bundle is new, and the rollback target.
2. **Get the code.** Choose one:
   - **Vendored in `backend_sloco`:** replace the whole folder, so deleted files go too.

     ```bash
     sha256sum -c walk-planner-X.Y.Z.tar.gz.sha256
     rm -rf services/walk-planner && tar xzf walk-planner-X.Y.Z.tar.gz && mv walk-planner-X.Y.Z services/walk-planner
     ```

     Then commit in `backend_sloco`. Never patch files of the vendored folder: changes go back through the
     research repo.
   - **On the server:** unpack next to the previous release, as `/opt/walk-planner/walk-planner-X.Y.Z`, and
     move the `current` symlink at the switch (step 7).
   - **From git:** `git fetch --tags` in the research repo, then the `git archive` command of §4 step 9.
3. **Environment.** `diff` the old and the new `deploy/env.example`, and add new variables to `walk.env`
   when the defaults are not what you want.
4. **Image.** `docker build … -t sloco-walk-planner:X.Y.Z` ([`DEPLOY.md`](DEPLOY.md) §3) with the release's
   commit from the notes as `GIT_SHA` (not `git rev-parse` of your own repository), or pull it.
5. **Bundle,** if the release ships a new one: sync it ([`DEPLOY.md`](DEPLOY.md) §6 step 5). Bundles never
   overwrite each other.
6. **Acceptance.** Start a temporary container with the new image and the release's bundle, routing off,
   and run `golden run --url` against it ([`DEPLOY.md`](DEPLOY.md) §7.1). It must report 26/26.
7. **Gateway changes** from the notes, if any. Ship them before or with the switch; additive fields need
   none.
8. **Switch.**
   - In `walk.env`, set `WALK_PLANNER_TAG=X.Y.Z` (and `WALK_BUNDLE_ID=<new id>` if the bundle is new).
   - Run `wpc up -d --no-build walk-planner`.
   - Move `/opt/walk-planner/current`; cron uses it.
9. **Verify.**
   - `/v1/meta`: `version`, `git_sha`, `bundles[].bundle_id`, `routing.chain`.
   - A plan, an edit and a search through the gateway.
   - For about 15 minutes, watch errors, `busy`, latency and the routing quality ([`DEPLOY.md`](DEPLOY.md)
     §11).
10. **Rollback**, when needed: the previous `WALK_PLANNER_TAG` (and `WALK_BUNDLE_ID`), then
    `wpc up -d --no-build walk-planner`, then the previous `current`. Keep the previous image and bundle
    until the next release is stable.

Edits keep working across a deploy. A plan built by 1.x can be edited on 1.y: the `request` echo is
accepted, and unknown echo fields are ignored. It can be edited on a new bundle too, unless one of its
stops left the catalog (409 `catalog_changed`) or became permanently closed (422).

## 8. Compatibility and deprecation policy

**Within `/v1`:**
- **Never** removed, renamed or retyped: request fields, response fields, units, meanings, error codes and
  HTTP statuses of existing cases.
- **May be added** in a MINOR release: response fields, optional request fields (their default keeps the
  old behaviour), message codes, error codes, enum values (for example a new activity or hours status), and
  endpoints.
- **Clients must tolerate additions:**
  - ignore unknown fields;
  - show an unknown message code as information, using its `text`;
  - handle an unknown error code by its HTTP status;
  - give every enum a fallback branch;
  - convert keys generically ([`INTEGRATION.md`](INTEGRATION.md) §2.1).
- **Validation** is not tightened within v1, except to refuse inputs that were already documented as
  invalid.

**Across versions:**
- **Request echo:** an echo from 1.x stays valid on every later 1.y, and the other way round, because
  unknown echo fields are ignored.
- **Deprecation:** mark the item in the CHANGELOG and in OpenAPI (`deprecated: true`). Keep it for at least
  2 MINOR releases and 3 months. Remove it only in `/v2`.
- **`/v2`** is served by the same service next to `/v1`, until the oldest supported app version uses
  `/v2`.
- **Bundles:** every 1.x reads `BUNDLE_SCHEMA_VERSION` 1. A new schema version is announced, needs the
  matching package, and ships with a bundle built for it.
- **Environment variables:** a new one always has a default that keeps the old behaviour. A renamed one
  keeps accepting the old name for at least one MINOR release.
- **Images:** tag `X.Y.Z`, plus `sha-<git12>`. Never overwrite a tag that was pushed to a registry: a
  rebuild of the same release for base-image security fixes is pushed as `X.Y.Z-r2`, `X.Y.Z-r3`, and so
  on. Never deploy `latest`.

## 9. One source of truth: the dashboard and the service

There is one implementation, the `walk_planner` package in `services/walk_planner/walk_planner`. Three
things use it:

| User | How it uses the package | Where its copy comes from |
|---|---|---|
| The **research dashboard** (Streamlit) | Its Walk Planner page is UI glue only: the form becomes a request, then `normalize_params` → `build_plan` / `schedule` / `insert_place` → `present`. | The repo checkout: `recommendation_system/ai_location_recommender/__init__.py` puts `services/walk_planner` on `sys.path`; the dashboard image copies the package and sets `PYTHONPATH`. |
| The **CLI** | `python -m walk_planner <command>` (`plan`, `schedule`, `insert`, `search`, `place`, `golden` …): the same calls as the service's endpoints (`walk_planner.cli.api_plan`, …) | any checkout, or the image |
| The **service** | the FastAPI endpoints | the `walk-planner` image of a tagged release |

- **Where changes happen.** Every change to planning, texts or data handling is made in the package,
  tried on the dashboard, and released by §4. `dashboard_app.py` holds no planning logic, and the research
  suite (`test/test_dashboard_walk.py`) checks that the page shows exactly the golden texts.
- **Version skew.** The dashboard is deployed from `main` on every push (`.github/workflows/deploy.yml`), so
  it may run ahead of production. Production runs a tagged release. Compare with
  `walk_planner.__version__` and the CHANGELOG.
- **What production returns, exactly:** run the CLI on the production bundle,
  `python -m walk_planner plan --bundle <bundle dir> …`. It is byte-identical to the service (that is what
  the golden set checks). The dashboard is close but not byte-identical:
  - it builds its catalog from the CSV (`CityCatalog.from_frame`) with pandas' default float parser, while
    bundles use the round-trip parser;
  - it loads the taste model from the research files;
  - its image's dependencies are not pinned.

  Coordinates can therefore differ in the last bit. In a rare exact tie, such as S19 variants 2 and 3, a
  loop can run the other way, and a navigation link can differ by one unit in its 6th decimal.
  Everything else is the same.

## 10. v1.0.0 at a glance

| | |
|---|---|
| Algorithm | `1.0.0`: the research dashboard's planner (git `f1b2196`) plus exactly these changes: the segment order fix for the `free` shape (bug 1); closed places refused or flagged for must-visits and inserts (bug 2); the routing chain OSRM → ORS → estimate with explicit quality; favourites personalisation by the ported taste model; stable codes and API normalisations. Other known defects stay as they are, for v1.1+. |
| API | `v1`: `/v1/health/live`, `/v1/health/ready`, `/v1/meta`, `/v1/walks/config`, `/v1/walks/plan`, `/v1/walks/schedule`, `/v1/walks/insert`, `/v1/walks/places/search`, `/v1/walks/places/{place_id}` |
| Data | bundle schema `1`; accepted bundle `bucharest-20261002-d68a311e` (12,961 places, 60 MB); interest `walk_interest_v1` |
| Golden | 26 scenarios (S01–S24, P01, P02) with edit chains; mini set: 8 scenarios |
| Image | `python:3.12.15-slim-trixie`; numpy 2.4.6, pandas 3.0.3, pyarrow 24.0.0, scikit-learn 1.8.0, fastapi 0.142.2, pydantic 2.13.4, uvicorn 0.49.0, redis 7.4.1 |
| OSRM | `ghcr.io/project-osrm/osrm-backend:v26.10.0-debian`, the same tag in `docker-compose.yml` and `prepare_osrm.sh`; change both together |
