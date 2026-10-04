# Walk Planner data: bundles, catalog, hours, ids, photos

This document is for the backend developer who runs the `walk-planner` service. It covers what data the service
reads, where that data comes from, how it is checked, and how to rebuild it, ship it and add a city.

All numbers describe the Bucharest bundle `bucharest-20261002-d68a311e`, measured on 2026-10-03, unless a section
says otherwise. Paths written like `recommendation_system/...` are relative to the root of the research repository
(`sloco_recommendation_system`), which holds the build inputs. Links point into this package.

Related documents: [ROUTING.md](ROUTING.md) (street routing), [API.md](API.md), [DEPLOY.md](DEPLOY.md),
[golden/README.md](../golden/README.md) (golden outputs), [deploy/env.example](../deploy/env.example).

## Contents

1. [Summary](#1-summary)
2. [The data bundle](#2-the-data-bundle)
3. [Catalog columns](#3-catalog-columns-walk_catalogparquet)
4. [Opening hours](#4-opening-hours)
5. [Business status](#5-business-status)
6. [Place ids and Supabase](#6-place-ids-and-supabase)
7. [Photos](#7-photos)
8. [Interest artifacts](#8-interest-artifacts)
9. [Building a bundle](#9-building-a-bundle)
10. [Refreshing the data](#10-refreshing-the-data)
11. [Adding a city](#11-adding-a-city)
12. [Data quality caveats](#12-data-quality-caveats)
13. [Quick reference](#13-quick-reference)

---

## 1. Summary

- **One bundle per city.** A bundle is an immutable directory `<bundle_id>/` holding three things:
  - `manifest.json`;
  - `walk_catalog.parquet`, the places;
  - `interest/`, the personalisation vectors.

  Bucharest has 12,961 places in 7 files, 60.0 MB. The service mounts bundles read-only, picks them with
  `WALK_BUNDLE_DIR` and never writes to them.
- **Bundle id.** The format is `<city_slug>-<YYYYMMDD>-<sha8>`, for example `bucharest-20261002-d68a311e`: the build
  date (UTC) plus the first 8 hex digits of a hash of the content. The same data always gives the same hash. Every
  plan echoes the id as `versions.catalog` and `request.catalog_version`, so key gateway caches by it.
- **A bad bundle stops the service.** The image entrypoint exits with code 78 if a bundle is missing or corrupt.
  Every worker also re-checks the sha256 of every file before it serves.
- **`place_id` is the Google CID as a decimal string.** It has 15–20 digits and can exceed both int64 and 2^53. It
  equals Supabase `places.source_id`. Never parse it as a number.
- **Supabase gap.** About 5,624 of the 12,961 Bucharest places are probably **not in Supabase `places`**: all sights,
  all shopping, and part of food and things to do. Check this before launch with the SQL in
  [§6.3](#63-supabase-coverage-gap).
- **Hours and status.**
  - Opening hours are JSON intervals in week minutes. `null` means unknown, and the planner treats unknown as open.
    36% of places have unknown hours.
  - Business status is one of `""`, `closed_forever` or `temporarily_closed`.
  - Both are a snapshot from June–July 2026.
- **Photos.**
  - Photo keys look like `photos_cid/<cid>/<NN>_<vibe|all>.jpg`, with up to 10 per place.
  - Responses carry a URL only when `PHOTO_BASE_URL` is set.
  - The server probably does not have the sights and shopping photos yet ([§7.4](#74-server-status-check-before-launch)).

---

## 2. The data bundle

### 2.1 Layout

```
<bundles root>/bucharest-20261002-d68a311e/      60.0 MB, 7 files
  manifest.json                    identity, sources, coverage, size + sha256 of every other file
  walk_catalog.parquet   6.45 MB   the places: 12,961 rows x 30 columns (§3), zstd, in source-CSV row order
  interest/                        taste artifacts, in the same row order as the catalog (§8)
    text_f16.npy        39.82 MB
    image_f16.npy       13.27 MB
    has_image.npy        0.01 MB
    csls_density.npy     0.05 MB
    features.parquet     0.36 MB
    interest_meta.json   1.3 KB
```

| Where | Path |
| --- | --- |
| Research machine (build output) | `recommendation_system/ai_location_recommender/data/walk_bundles/<bundle_id>/` (git-ignored data directory) |
| Server | `/opt/sloco-data/walk/bundles/<bundle_id>/`, uploaded by [`deploy/sync_bundle.sh`](../deploy/sync_bundle.sh) (directories 755, files 444) |
| Container | `/bundles/<bundle_id>`, a read-only bind mount; compose sets `WALK_BUNDLE_DIR=/bundles/${WALK_BUNDLE_ID}` |
| Tests and CI | [`tests/fixtures/mini/bundles/minitown-20261002-17bd5998/`](../tests/fixtures/mini/README.md): a synthetic 60-place city, committed to git |

Bundles are never edited in place. If any input changes, build a new bundle: it gets a new content hash and a new
id. Ship it next to the old one.

The research dashboard does not read bundles. It reads the catalog CSV directly (`CityCatalog.from_frame`). The
service reads the bundle (`CityCatalog.from_bundle`). Both produce the same prepared table; [§12](#12-data-quality-caveats)
lists the one known difference.

### 2.2 `manifest.json`

The examples are the real values of the current Bucharest bundle. Keys marked \* are required: `read_manifest`
rejects a manifest that lacks any of them.

| Key | Example / type | Meaning |
| --- | --- | --- |
| `schema_version`\* | `1` | Bundle format version (`walk_planner.version.BUNDLE_SCHEMA_VERSION`). A package reads only its own version. |
| `bundle_id`\* | `"bucharest-20261002-d68a311e"` | Identity (§2.3). Should equal the directory name; validation warns if it does not. |
| `city`\* | `"Bucharest"` | The city name as requests send it. `city` is matched case-insensitively. |
| `city_slug`\* | `"bucharest"` | The id prefix, `[a-z0-9_]`. |
| `timezone`\* | `"Europe/Bucharest"` | IANA time zone. Opening hours and request times are wall-clock times in this zone. |
| `built_at`\* | `"2026-10-02T22:04:57Z"` | Build time in UTC. Its date is the `YYYYMMDD` in the id. "Newest bundle per city" (§2.5) compares this value. |
| `builder` | `{package, package_version: "1.0.0", git_sha, cmd, python, numpy, pandas, pyarrow, csv_float_precision: "round_trip"}` | What built the bundle. `git_sha` is the HEAD of the checkout, so uncommitted changes do not show in it. `cmd` is the exact command line. |
| `source` | `{catalog_csv: {file, sha256, bytes}, photo_manifest_csv, text_npy, text_meta_csv, image_npy, image_meta}` | The input files, by role, with their sha256 (§9.1). |
| `rows`\* | `12961` | Number of catalog rows, which is also the number of interest rows. |
| `rows_by_theme_group` | `{"food_drink": 8884, "shopping": 569, "sights": 2507, "things_to_do": 1001}` | |
| `coverage` | `{"opening_hours": 0.6401, "photos": 0.9646, "closed": 0.1884, "places_with_opening_hours": 8296, "places_with_photos": 12502, "closed_forever": 1961, "temporarily_closed": 481, "places_without_coordinates": 0}` | How complete the data is. `GET /v1/meta` repeats it under `bundles[].coverage`. |
| `photos` | `{"max_per_place": 10, "checked_against_files": true, "dropped_missing_files": 0, "key_pattern": ..., "keys": 78620, "places_with_photos": 12502}` | The photo keys in the catalog (§7). `checked_against_files: true` means the builder dropped manifest rows whose JPG was missing under `--photos-root`. |
| `bbox` | `[25.8891, 44.2882, 26.3123, 44.5864]` | `[minLon, minLat, maxLon, maxLat]` of all places. |
| `catalog`\* | `{"file": "walk_catalog.parquet", "columns": [30 names], "catalog_sha256": ...}` | |
| `interest` | object or `null` | The taste artifacts (§8). Its keys are in the next rows. |
| ↳ identity | `version: "walk_interest_v1"`, `dir: "interest"`, `fingerprint` | |
| ↳ models | `text_model: "text-embedding-3-small"`, `text_run_id`, `image_model: "openclip_vitb32_v1"`, `image_model_tag` | |
| ↳ scoring | `csls_k: 10`, `csls_penalty: 0.5`, `weights: {text .26, image .50, tag .08, axis .06, quality .06, price .04}`, `missing_image_policy: "zero"`, `quality: {version: "v2", shrinkage_prior: 25}` | |
| ↳ sizes | `rows`, `text_dim: 1536`, `image_dim: 512`, `places_with_text: 12961`, `places_with_image: 12510` | |
| ↳ `null` | — | The bundle was built with `--no-interest`. Requests with favourites then fall back to popularity, with the `personalization_unavailable` message. |
| `content_sha256`\* | `"d68a311e..."` | The content hash (§2.3). |
| `files`\* | `{"walk_catalog.parquet": {sha256, bytes}, "interest/text_f16.npy": {sha256, bytes, shape: [12961, 1536], dtype: "float16"}, ...}` | Every file except the manifest itself, with the exact bytes of this build. |

### 2.3 Identity and hashes

| Hash | Computed over | Changes when |
| --- | --- | --- |
| `catalog.catalog_sha256` | The catalog rows as canonical JSON lines: file order, sorted keys, `null` for NaN or missing | Any cell, column or row order changes. It does not depend on the parquet writer, its version or the compression. |
| `interest.fingerprint` | Place ids, themes, every taste array and the tags (not `interest_meta.json`) | Embeddings, tags, axes, quality scores or row order change. |
| `content_sha256` | `{"format": "walk-bundle-content/v1", schema_version, city, timezone, catalog_sha256, interest_fingerprint}` | Anything above changes. |
| `bundle_id` | `<city_slug>-<UTC build date>-<content_sha256[:8]>` | The content changes, or the same content is built on another day (then only the date part differs). |
| `files[*].sha256` | The exact bytes of each file | A rebuild writes different bytes, for example with another pyarrow. These hashes check integrity; they are not part of the identity. |

What follows from this:

- **Same sources, same UTC day:** same id. `bundle build` refuses to overwrite an existing directory
  (`BundleExistsError`). A rebuild from the same sources on 2026-10-02 at 22:49 UTC (into a scratch directory)
  produced `bucharest-20261002-d68a311e` again.
- **Same sources, another day:** the id becomes `bucharest-<new date>-d68a311e`. The content is identical, and the
  golden check accepts it (§10.4).
- **CSV parsing is platform-independent.** CSVs are parsed with `float_precision="round_trip"`, so macOS and Linux
  builds of the same CSV get the same hashes. Before 2026-10-02 they did not, which is why
  `bucharest-20261002-49fc956c` was replaced. The details are in [golden/README.md](../golden/README.md), section
  "Bundle rebuild 2026-10-02".
- **`plan_id` follows the bundle.** It is a sha1 of the request echo plus `versions`, and `versions` contains the
  bundle id.

### 2.4 Validation rules

All checks live in [`walk_planner/bundle.py`](../walk_planner/bundle.py). There are four levels, each including the
previous one.

**1. Layout.** `layout_problems()` always runs, including in `load_bundle(verify=False)`. It does no hashing.

- `manifest.json` is a JSON object, `schema_version` is 1, and the required keys are present.
- Every key of `manifest.files` is a plain relative POSIX path inside the bundle:
  - not absolute;
  - no `..`, `.` or empty component;
  - no backslash or NUL;
  - it does not leave the bundle through a symlink.

  An unsafe path is reported and never opened.
- `catalog.file` is `walk_catalog.parquet`, and `interest.dir` is `interest`.
- `walk_catalog.parquet` is **listed** in `manifest.files`. When the manifest has an `interest` block, all six
  interest files are listed too, so every file the planner reads is hashed.
- `walk_catalog.parquet` and `interest/` themselves do not resolve outside the bundle.

**2. Integrity.** `load_bundle(verify=True)` runs these; it is the service default (`WALK_VERIFY_BUNDLE=true`).
`bundle validate` always runs them.

- Every listed file exists with the recorded size and sha256. Files that are not listed only cause a warning in
  `validate`, and the service never reads them.
- The interest files have as many rows as the catalog, and their place-id order equals the catalog's.
- The loaded catalog's version equals `bundle_id`.

**3. `bundle validate --shallow`.** This is what the image entrypoint runs.

- The id:
  - `bundle_id` matches `<slug>-<YYYYMMDD>-<sha8>`;
  - its slug equals `city_slug`;
  - its sha8 is the start of `content_sha256`;
  - the directory name equals the id (a mismatch only warns).
- The catalog:
  - the parquet file is readable;
  - it has the required columns `place_id, name, latitude, longitude, photos, photo_count`;
  - the column types are right: float64 numbers, string text, `list<string>` photos, integer `photo_count`;
  - the row count and the column list equal the manifest's;
  - unknown columns only warn.
- The interest files:
  - all six exist;
  - `text_f16.npy` and `image_f16.npy` are 2-D float16 arrays with `rows` rows;
  - the text dimension equals `interest.text_dim`;
  - `has_image` and `csls_density` have shape `(rows,)`;
  - `features.parquet` lists the place ids in catalog order;
  - the fingerprint in `interest_meta.json` equals the manifest's.

**4. Deep.** This is the default for `bundle validate`, and it runs after every build.

- Each `place_id` is all decimal digits and unique. A `google_place_id` that does not start with `ChIJ` only warns.
- Each `opening_hours` value is a JSON list of `[open, close]` numbers:
  - `0 <= open < close <= open + 1440`;
  - `open < 10080`;
  - the intervals are sorted by `open` (§4.1).
- `business_status` is one of `""`, `closed_forever`, `temporarily_closed`. A `theme_group` outside the four known
  groups only warns.
- Coordinates are finite and inside the city:
  - inside the box in `CITY_BBOXES` for cities that have one (Bucharest: longitude 25.70–26.50, latitude 44.20–44.70);
  - otherwise within 80 km of the median point.

  Missing coordinates only warn; the planner ignores those rows.
- Photo keys:
  - match `^photos_cid/<the row's own place_id>/<NN>_(vibe|all|review)\.jpg$`;
  - there are at most `photos.max_per_place` per place, with no duplicates;
  - `photo_count` equals the number of keys.
- `catalog_sha256`, `content_sha256` and the taste fingerprint are recomputed and compared, and the catalog is loaded
  exactly the way the service loads it.
- With `--photos-root DIR`, the check also counts which photo files exist. Missing files only warn.

Timings for Bucharest on an Apple-silicon Mac:

| Check | Time |
| --- | --- |
| Shallow | 0.1 s, plus about 0.4 s of Python imports |
| Deep | 1.4–1.8 s |
| Deep with photo files | about 1.7 s |

```bash
python -m walk_planner bundle validate <bundle dir | a,b | root> [--shallow] [--photos-root <photos_cid dir>] [--json]
python -m walk_planner bundle info <bundle dir | root> [--json]       # summary of the manifest(s), no checks
# exit codes: 0 valid · 1 invalid · 2 usage error
```

### 2.5 How the service finds, loads and verifies bundles

**Selecting bundles.** `WALK_BUNDLE_DIR` takes one of three forms (`bundle.resolve_bundle_dirs`):

- **One bundle directory**, which contains `manifest.json`.
- **Several bundle directories, comma-separated** (`a,b`). There must be one per city; two bundles of the same city
  is an error.
- **A root directory of bundles.** The service takes the newest bundle of each city: the largest `built_at`, with
  ties broken by bundle id. It skips hidden directories such as `.incoming-<id>` (uploads in progress) and
  `.<id>.*.tmp` (builds in progress).

Compose uses the first form, `WALK_BUNDLE_DIR=/bundles/${WALK_BUNDLE_ID}`, so switching data is a deliberate change of
`WALK_BUNDLE_ID`. Keep explicit ids in production. With a root, a newly synced bundle would be picked up silently at
the next restart.

**Start-up sequence in the container:**

1. **Entrypoint** ([`deploy/docker-entrypoint.sh`](../deploy/docker-entrypoint.sh); it runs only for the `uvicorn`
   command).
   - `WALK_BUNDLE_DIR` must be set and must contain a manifest, either directly or one level down.
   - It then runs `python -m walk_planner bundle validate --shallow --json --compact "$WALK_BUNDLE_DIR"`.
   - On any failure it prints the JSON report on stderr and **exits with code 78** (EX_CONFIG) before uvicorn
     starts.
2. **Each uvicorn worker**, in its lifespan hook:
   1. `load_bundle(dir, verify=WALK_VERIFY_BUNDLE)` for every bundle. The default `true` hashes every file, which
      takes about 0.4 s for 60 MB.
   2. A check that there is one bundle per city.
   3. `catalog.warm()` and `interest.warmup()`, about 1 s, mostly the scikit-learn import.
   4. One smoke plan per city, with straight-line routing and no network, about 0.5–0.7 s
      (`WALK_STARTUP_SMOKE_PLAN`).
   5. Ready. Measured: about 1.7–2.2 s per worker in total (`startup_ms`, [SPEC.md §10](SPEC.md#10-performance-and-capacity)).
3. **Readiness.**
   - Until a worker is ready, `GET /v1/health/ready` answers 503 `not_ready` with `Retry-After: 5`; after that, 200
     with the bundle ids.
   - `GET /v1/meta` lists every bundle: id, city, timezone, `built_at`, rows, places, content and catalog sha256,
     coverage, interest models and `loaded`.
4. **Failure.** Any failure in step 2 stops the process. With one worker uvicorn exits with code 3. With several
   workers it exits with 0, a known limitation; the entrypoint check in step 1 catches the common causes with 78
   instead. Docker's restart policy applies either way.

**Log events:**

| Event | Fields |
| --- | --- |
| `bundle_loaded` | `bundle_id`, `city`, `places`, `taste`, `verified`, `ms` |
| `bundle_ready` | `warm_ms`, `smoke_plan` |
| `ready` | `bundles`, `cities`, `startup_ms` |
| `startup_failed` | `error` |

**Memory per worker** for the Bucharest bundle:

| Part | Size |
| --- | --- |
| Prepared catalog table | about 23 MB |
| Taste matrices | 53 MB, memory-mapped (page cache shared between workers) |
| Float32 working copies | about 101 MB |
| scikit-learn | about 79 MB |
| **Idle worker in total** | about 0.5–0.6 GB RSS |

[deploy/env.example](../deploy/env.example) has the measured figures under bursts.

**Edits after a bundle switch.** Every plan echoes `request.catalog_version`. `/schedule` and `/insert` re-read
names, coordinates, hours and status, by `place_id`, from the bundle that is loaded *now*:

- a place that no longer exists gives 409 `catalog_changed` when the echoed version differs (404 `unknown_place`
  otherwise);
- a place that has become `closed_forever` gives 422 `place_closed_forever`.

---

## 3. Catalog columns (`walk_catalog.parquet`)

**Columns.** The column order is `catalog.BUNDLE_COLUMNS`. Columns missing from the source CSV are left out; only
`place_id, name, latitude, longitude, photos, photo_count` are required. The source CSV has 64 columns, of which the
bundle keeps 30.

**Rows.** Rows stay in source-CSV order. This matters: ties in candidate ranking are broken by row order. Closed
places are kept, because the must-visit status check and the cold-start normalisation need them.

**Missing values.** Numbers are float64; a missing value is an Arrow `null`. "Coverage" below means non-null,
non-empty values in Bucharest.

| Column | Arrow type | Missing value | Coverage (Bucharest) | Meaning | Used for |
| --- | --- | --- | --- | --- | --- |
| `place_id` | string | never | 100%, unique | Google CID, decimal (§6) | Key everywhere: requests, responses, photos, interest |
| `google_place_id` | string | null | 100%; all `ChIJ...`, 27 characters, unique | Google Places id | Named pins in Google Maps navigation links (`origin_place_id`, `destination_place_id`, and `waypoint_place_ids` only when every middle stop has one); `PlaceCard.google_place_id` |
| `city` | string | | 100% (`Bucharest`) | City label | A bundle holds one city |
| `name` | string | | 100%; median 16, maximum 125 characters | Google title, mostly Romanian | Cards; search (accents and case ignored); food-slot keyword text |
| `latitude`, `longitude` | double | null: the row is ignored by the planner | 100%; latitude 44.288–44.586, longitude 25.889–26.312 | WGS84 | Everything. The mean over all rows (44.44029, 26.09771) is the `"city_center"` start and the area of shape `free` |
| `theme_group` | string | | 100%: food_drink 8,884 · sights 2,507 · things_to_do 1,001 · shopping 569 | 4 groups | Slot gating; labels of the place-screen sections |
| `theme` | string | | 100%; 9 values (table below) | Finer theme | Which slot a place can fill; base visit length of must-visit and added places |
| `primary_type` | string | | 100% | Google primary type (`romanian_restaurant`, `park`, ...) | Slot type filters, keyword text, visit-length subtype, card `primary_type` |
| `ai_place_type_summary` | string | | 100%, English | Short AI type (`traditional Romanian restaurant`) | Keyword text; non-venue filter; quick-look visit cap (monument, statue, ...); card `type_label` |
| `ai_card_summary` | string | | 100%, English; median 130, maximum 183 characters | One-line description | Card `summary` (`summary_lang: "en"`); "brief / small / courtyard" shortens the visit by 40% |
| `google_rating` | double | null | 90.91% | Google stars | Card `rating` |
| `google_user_rating_count` | double | null | 90.91%; 1–83,400 (Caru' cu bere) | Google review count | Cold-start interest (log scale, normalised by the city maximum); the "near the start" pool needs at least 20 reviews; visit-length size factor; search tie-break; card `rating_count` |
| `bayesian_rating` | double | | 100% | Rating shrunk towards the mean | Cold-start quality term `clip(r - 4, 0, 1)` |
| `map_visibility_score` | double | | 100% | Recommender popularity score (0–100) | Cold-start fallback, used only when the review-count column is absent |
| `opening_hours` | string (JSON) | **null = unknown** | 64.01% (8,296) | Week-minute intervals (§4) | Hours checks, labels, place screen |
| `business_status` | string | **never null; `""` = open or unknown** | 18.84% non-empty: closed_forever 1,961 · temporarily_closed 481 | §5 | Status policy |
| `address` | string | null | 99.88% | DataForSEO address | Card; search (address tier) |
| `price_level` | string | null | 9.66%: `moderate` 873, `inexpensive` 316, `expensive` 61, `very_expensive` 2 | Google price level, as text | Card `price_level` (the app's `priceLevel` is an Int: map it) |
| `ai_vibe` | string | null | 90.70% | AI section | Place screen "Vibe" |
| `ai_what_to_expect` | string | null | 97.75% | AI section | "What to expect" |
| `ai_food_and_drinks` | string | null | 93.31% | AI section; meaning depends on `theme_group`: food and drinks / the sight / the goods / the experience | Section keyed by group (`GET /v1/walks/places/{id}` labels it) |
| `ai_price` | string | null | 77.52% | AI section | "Price" |
| `ai_service` | string | null | 87.61% | AI section: service / significance / what to buy / service | Section keyed by group |
| `ai_the_move` | string | null | 89.78% | AI section | "The move" |
| `ai_watch_out` | string | null | 63.01% | AI section | "Watch out" |
| `ai_tags_csv` | string | null | 99.61% | Comma-separated tags | Place screen `tags` |
| `ai_confidence` | string | | 100%: high 6,705 · medium 2,743 · low 3,513 | Confidence of the AI summary | Place screen only; the planner does not filter on it |
| `photos` | list\<string\> | never null (empty list) | 96.46% non-empty; 78,620 keys | Photo keys, in display order (§7) | Cards (4), place screen (10), search (1) |
| `photo_count` | int32 | never | equals `len(photos)` | | |

The planner itself reads `catalog.USED_COLUMNS`, which are the rows from `place_id` to `business_status` above. The
display columns (`address` through `ai_confidence`) and `photos` only feed cards, the place screen and search.

**Prepared table at load time** (`CityCatalog.from_bundle`, identical to the dashboard's `from_frame`):

- rows without coordinates are dropped;
- `place_id` becomes `str`;
- `opening_hours` is parsed into `wp_hours`;
- the status is normalised (§5).

**Themes and activity slots.** Most slots map to catalog themes. The three food-and-drink slots match keywords
inside `food_drink`, because every place there has theme `food_drink`.

| Theme (rows) | Slot it fills | Extra filters |
| --- | --- | --- |
| `culture_sights` (1,193), `religious_sights` (445) | `sight` | A deny-list of primary types; AI types that read like a business (tour operator, agency, ...) are dropped |
| `nature_outdoors` (823) | `park` | An allow-list of nature types; non-venues dropped |
| `markets_walks` (46) | `market` | An allow-list of market and square types; non-venues dropped |
| `performing_arts` (139), `leisure_active` (850) | `entertainment` | Non-venues dropped |
| `shopping_souvenirs` (569) | `shopping` | — |
| `food_drink` (8,884) | `coffee`, `food`, `bar` | Substring keywords in primary type + AI type + name (e.g. `coffee`, `cafe`, `espresso` / `restaurant`, `bistro`, `grill` / `bar`, `pub`, `wine`) |
| `transport_resorts` (12) | none; only as a must-visit or added place | — |

The type filters are relaxed when they leave fewer than 3 places. Closed places and places closed for the whole time
window never enter a slot. [ALGORITHM.md](ALGORITHM.md) has the full selection rules.

---

## 4. Opening hours

### 4.1 Format

`opening_hours` is a JSON string: a sorted list of `[open, close]` integer pairs.

- **Units.** Both values are minutes from Monday 00:00, in the city's local wall-clock time
  (`manifest.timezone`; `Europe/Bucharest`). Monday is day 0 and Sunday is day 6, so day `d` starts at
  `d * 1440`. A week is 10,080 minutes.
- **Overnight.** `close` may pass midnight: `close <= open + 1440`. `00:00` as a closing time means the end of the
  day (1440), and `00:00–00:00` means open 24 hours.
- **End of week.** On Sunday `close` may pass the end of the week (`> 10080`). The planner reads such an interval as
  continuing into early Monday.
- **Validation** (§2.4): `0 <= open < close <= open + 1440`, `open < 10080`, sorted by `open`.
- **How the planner reads it.** The planner copies the week one week back and one week forward and merges spans that
  touch (`core._merged_week`). So a Sunday-night opening covers early Monday, and seven back-to-back 24-hour days
  form one continuous span.

### 4.2 Examples (real rows)

| Place (`place_id`) | `opening_hours` | Reading |
| --- | --- | --- |
| 5 to go - Gheorghe Șincai 16 (`10379863645010145296`) | `[[420,1110],[1860,2550],[3300,3990],[4740,5430],[6180,6870],[7740,8340],[9180,9780]]` | Mon–Fri 07:00–18:30, Sat and Sun 09:00–19:00 |
| Caru' cu bere (`14593284683301693268`) | `[[600,1440],[2040,2880],...,[9240,10080]]` | Daily 10:00–24:00 (a 00:00 close is stored as the end of the day) |
| Gyros Thessalonikis (`10008423346367752387`) | `[[600,1560],[2040,3000],[3480,4440],[4920,5880],[6360,7440],[7800,8880],[9240,10200]]` | Mon–Thu 10:00–02:00, Fri–Sat 10:00–04:00, Sun 10:00 to Mon 02:00 (`10200 > 10080`: past the end of the week) |
| Lulu Cuisine (`10338027582428753495`) | `[[0,1440],[1440,2880],...,[8640,10080]]` | 24/7 |
| Cambun (`11259351839760359119`) | `[[5040,5340],[5400,5640],...,[9720,9960]]` | Thu–Sun 12:00–17:00 and 18:00–22:00 (split shifts); closed Mon–Wed |
| Bucharest Fountains (`12673764917825354871`) | `null` | Unknown (this place is also `temporarily_closed`) |

### 4.3 Unknown (`null`) versus `[]`

| Value | Meaning | Planner | API |
| --- | --- | --- | --- |
| `null` (no timetable from DataForSEO) | **Unknown** | Treated as **always open**. `known_hours_only: true` removes such places from the slot and on-the-way pools, but not must-visits or added places | `stops[].hours.status: "unknown"`, `hours.day: null`; place screen `opening_hours_known: false`, `opening_hours_week: null`; Russian label «часы работы неизвестны» |
| `[]` | **Known, closed all week** (Google lists no open day) | Never open. Never enters a slot. As a must-visit or added place it is kept but flagged `closed_at_arrival` | `hours.day.closed_all_day: true`; label «пн закрыто» |
| `[[...], ...]` | Known | Checked against each visit (§4.4) | `hours.status: open / open_after_wait / closes_during_visit / closed_at_arrival` |

No current row has `[]`. Weekdays missing from a timetable are closed on that day, and the other days are kept.
Since the format allows `[]`, clients must not treat an empty list as unknown.

### 4.4 How the planner uses hours

- **Choosing candidates.** A place enters a slot or on-the-way pool only if a visit fits somewhere in the whole time
  window. The solver then requires that the **whole visit** fits inside one opening span, after waiting at most
  30 minutes at the door.
- **Final schedule.** The assembled plan re-checks every stop at its actual arrival time. When street routing is on,
  the times come from the router (see [ROUTING.md](ROUTING.md)). A stop that fails the check is kept and flagged
  (`stop_hours_conflict`), not removed.
- **No time-zone conversion.** The request carries the city-local date and `HH:MM` times, and
  `t0 = weekday(date) * 1440 + start minutes`. The gateway must send local time in the city, not UTC.
- **Not modelled:** public holidays, and windows that cross a daylight-saving change. Such a window is planned in
  wall-clock minutes, so it is one hour off in real time. In 2026 that affects the nights of 28–29 March and 24–25
  October.

### 4.5 How the API shows hours

- **`stops[].hours`** is `{status, day}`. `day` is evaluated on the weekday of the visit start:

  ```json
  {"weekday": 6, "open_24h": false, "closed_all_day": false,
   "intervals": [{"open": "10:00", "close": "02:00", "close_day_offset": 1}],
   "carryover_until": null}
  ```

  `carryover_until` is set when last night's opening is still running. For Gyros Thessalonikis on Monday 01:00 it is
  `"02:00"`.
- **`GET /v1/walks/places/{place_id}`** returns `opening_hours_week`, one such object per weekday Mon–Sun, plus
  `opening_hours_known`.

### 4.6 Statistics (Bucharest)

**Overall:**
- 8,296 places have known hours (64.01%) and 4,665 unknown (35.99%). Among places that are not closed, 76.95% have
  known hours.
- 53,585 intervals: 7 per place at the median, between 1 and 35.
- 928 places are open 24/7.
- 544 places have an interval that runs past midnight.
- 350 places run past the end of the week. The latest close is 10,740, Monday 11:00.
- 202 closed places still list hours.

**Known hours by theme:**

| Theme | Known hours |
| --- | --- |
| food_drink | 69.2% |
| shopping_souvenirs | 74.2% |
| leisure_active | 61.8% |
| culture_sights | 49.6% |
| nature_outdoors | 46.8% |
| markets_walks | 41.3% |
| religious_sights | 36.2% |
| performing_arts | 23.0% |
| transport_resorts | 75% (12 places) |

By group, sights have known hours for only 46.2%.

### 4.7 Where the hours come from

- **Source field.** DataForSEO Business Listings `work_time.work_hours.timetable`.
- **Conversion.** `build_city_catalog.py` converts it with `walk_planner.core.week_intervals_from_timetable`:
  - `open = hour * 60 + minute`, and `close` likewise;
  - if `close <= open`, add 1440;
  - malformed entries are skipped;
  - no timetable at all gives unknown.
- **CSV vs bundle.** The CSV stores `""` for unknown; the bundle stores `null`.
- **Snapshot dates.** The DataForSEO data was fetched on 2026-06-10 (11,315 rows) and 2026-07-16 (1,646 rows).

---

## 5. Business status

**Values.**
- `""` means operational, or unknown.
- `closed_forever` and `temporarily_closed` are Google's closed states.
- Google's enum names `closed_permanently` and `closed_temporarily` are accepted and normalised.
- Any other value becomes `""`.

**Source.** The source is DataForSEO `work_time.work_hours.current_status`. The builder keeps only the two closed
states. `open` / `close` only describe the moment of the fetch, so they become `""`.

**v1 policy** (product decision; enforced in `candidates.place_candidate` and `pipeline`):

| Where | `closed_forever` | `temporarily_closed` |
| --- | --- | --- |
| Slot and on-the-way candidates | Never | Never |
| `must_visit_place_ids` | Dropped; request message `must_visit_closed_forever` `{place_ids, names}` | Kept and pinned; stop message `place_temporarily_closed`; `stops[].business_status: "temporarily_closed"` (show a badge) |
| `POST /v1/walks/insert` | 422 `place_closed_forever` | 422 `place_temporarily_closed`, unless `allow_temporarily_closed: true` (ask the user, then resend). Then the place is added and flagged |
| `POST /v1/walks/schedule` (sequence) | 422 `place_closed_forever` (e.g. it closed in a newer bundle) | Kept and flagged |
| `start.place_id` | Allowed: the start is only a coordinate | Allowed |
| `GET /v1/walks/places/search` | Excluded unless `include_closed=true` | Included, with `business_status` |
| `GET /v1/walks/places/{id}`, `PlaceCard` | Served, with `business_status: "closed_forever"` | Served |

The API reports an operational place as `business_status: "operational"`. The empty string appears only inside the
bundle.

**Counts (Bucharest).** 10,519 operational, 1,961 `closed_forever`, 481 `temporarily_closed`.

| Theme group | Operational | closed_forever | temporarily_closed |
| --- | --- | --- | --- |
| food_drink | 6,917 | 1,571 | 396 |
| sights | 2,286 | 177 | 44 |
| things_to_do | 835 | 135 | 31 |
| shopping | 481 | 78 | 10 |

The statuses are a snapshot and include seasonal closures (§12).

---

## 6. Place ids and Supabase

### 6.1 `place_id`

- **What it is.** The **Google Maps CID** (DataForSEO's `cid`), written as a decimal **string** of 15–20 digits. It
  is unique per catalog. A check of 3,000 DataForSEO records confirmed `cid == int(feature_id.split(":")[1], 16)`.
- **It does not fit a number type.**

  | Limit | Bucharest ids above it |
  | --- | --- |
  | int64 maximum (9,223,372,036,854,775,807) | 6,442 |
  | 2^53, JavaScript's largest exact integer | 12,954 |

  The largest id is `18444330390184695581`.
- **Types to use:**

  | Context | Type |
  | --- | --- |
  | JSON | string |
  | TypeScript | `string` |
  | Postgres | `text` |
  | Swift | `String` |

  Never use bigint, `Number`, `Int64` or a double. pandas reads the raw CSV column as `uint64` only because every
  value happens to fit; the bundle stores strings.
- **What the service does with it:**
  - It refuses numeric JSON ids with 422 `validation_error`.
  - The HTTP schema accepts `^[0-9]{1,20}$` exactly, with no spaces.
  - The ids in responses are strings.
- **Other identifiers:**
  - `google_place_id` (`ChIJ...`) is a separate Google id, used only for navigation pins.
  - The Google Maps link is `https://www.google.com/maps?cid=<place_id>` (`PlaceCard.google_maps_url`).
- **Supabase.** `place_id` = Supabase **`places.source_id`** (a string), per
  `docs/handoffs/2026-07-16-onboarding-backend-endpoints.md` in the research repo (not in this package;
  [INTEGRATION.md §3.2](INTEGRATION.md#32-id-mapping-sourceid--placeid) repeats what it says). The app's numeric
  `places.id` is a different id. The gateway maps between them in both directions (see
  [INTEGRATION.md](INTEGRATION.md)):
  - **request** (start place, must-visits, added places, favourites): `places.id` → `source_id`, batch-resolved with
    the existing `feed_places_by_source_ids` RPC pattern;
  - **response** (stops): `source_id` → `places.id`, or `null` when there is no row.
- **Favourites outside the bundle.** Favourite ids that are not in the bundle are ignored and listed in
  `personalization.favourites_ignored`.

### 6.2 What we could not verify

This machine has no `backend_sloco` checkout and no database access. So the following are inferences from the
handoff documents, not checked facts:

- the Supabase schema;
- the `source` value used for CID rows. The iOS app's older API notes show rows with `source: "tripadvisor"`, so
  resolve on `(source, source_id)`, not on `source_id` alone;
- the coverage gap described next.

### 6.3 Supabase coverage gap

**What we know.**
- The production recommender catalog (`locations_combined_food_ttd.csv`, 12,578 rows) has **7,337** of the 12,961
  Bucharest walk places.
- The 2026-08-01 coverage check suggests Supabase `places` holds the same set.
- If so, **5,624 walk places have no `places.id`:**

  | Group | Places without a `places.id` |
  | --- | --- |
  | sights | 2,507 (all) |
  | shopping | 569 (all) |
  | food_drink | 2,090 |
  | things_to_do | 458 |

**What breaks for those places.** They cannot be:
- saved;
- opened on the app's regular place screen;
- found by the app's `GET /v1/search/places`.

They also never appear among favourites, because favourites come from `saved_places`.

**Check it** before launch. First, export the bundle's ids. The service image has pandas and pyarrow; the script
writes the CSV to stdout, so the file lands in the host's current directory (the container's user cannot write
inside the image):

```bash
docker run --rm -i --network none -v /opt/sloco-data/walk/bundles:/bundles:ro sloco-walk-planner:1.0.0 \
  python - > walk_ids_bucharest-20261002-d68a311e.csv <<'EOF'
import sys
import pandas as pd
b = "bucharest-20261002-d68a311e"
df = pd.read_parquet(f"/bundles/{b}/walk_catalog.parquet", columns=["place_id", "theme_group", "name"])
df.rename(columns={"place_id": "source_id"}).to_csv(sys.stdout, index=False)   # ids stay text
print(len(df), "ids written", file=sys.stderr)
EOF
```

It prints `12961 ids written`; the CSV has a header and 12,961 rows. Any Python with pandas and pyarrow can run
the same script with the host path of the bundle.

Then, with psql connected to the Supabase database:

```sql
-- 0. source_id must be a text column (a CID does not fit bigint)
select column_name, data_type
  from information_schema.columns
 where table_schema = 'public' and table_name = 'places'
   and column_name in ('id', 'source', 'source_id');

-- 1. which sources exist (the value used for CID rows is not documented on our side)
select source, count(*) from public.places group by source order by count(*) desc;

-- 2. load the bundle's ids
create temp table walk_ids (source_id text primary key, theme_group text, name text);
\copy walk_ids from 'walk_ids_bucharest-20261002-d68a311e.csv' with (format csv, header true)

-- 3. coverage per theme group (last row = total)
select coalesce(w.theme_group, 'TOTAL')                   as theme_group,
       count(*)                                           as walk_places,
       count(*) filter (where p.source_id is not null)    as in_places,
       count(*) filter (where p.source_id is null)        as missing
  from walk_ids w
  left join (select distinct source_id from public.places) p on p.source_id = w.source_id
 group by rollup (w.theme_group)
 order by w.theme_group nulls last;

-- 4. ambiguous mappings: one CID on several rows (e.g. under different sources)
select p.source_id, count(*) as n_rows, array_agg(p.id) as ids, array_agg(p.source) as sources
  from public.places p
  join walk_ids w on w.source_id = p.source_id
 group by p.source_id
having count(*) > 1;

-- 5. a sample of missing places to spot-check (open https://www.google.com/maps?cid=<source_id>)
select w.*
  from walk_ids w
 where not exists (select 1 from public.places p where p.source_id = w.source_id)
 order by w.theme_group, w.name
 limit 50;
```

**Without psql** (for example in the Supabase SQL editor), inline the ids:
`with walk_ids(source_id) as (select unnest(string_to_array('<id1>,<id2>,...', ',')))`. The list is about 260 KB for
Bucharest. Alternatively, resolve the ids in batches through `feed_places_by_source_ids`.

**Expected result** if the inference is right: about 7,337 found and 5,624 missing.

**Options:**

| Option | What | Effect |
| --- | --- | --- |
| **A (recommended)** | Import the missing places into `places` with `source_id = place_id` and the CID `source` value. Take the data from the bundle: name, coordinates, address, `google_place_id`, type, rating, photo keys, AI texts | Every stop gets a `places.id`. Saving, the regular place screen, app search and favourites then work for all walk places |
| **B (launch fallback)** | The gateway returns `placeId: null` for unmapped stops. The walk screens use the walk-planner's own data, which covers all 12,961 places: the card payload (`places` map in the plan response), place screen (`GET /v1/walks/places/{place_id}`) and search (`GET /v1/walks/places/search`) | Walks fully work, but unmapped stops cannot be saved or favourited, and the main app search does not find them |
| C (not recommended) | Build a bundle restricted to places that exist in Supabase | Removes every sight and shop, which is most of what a walk is for, and changes all plans |

---

## 7. Photos

### 7.1 Keys and order

- **Key format.** Each place has up to 10 keys (`manifest.photos.max_per_place`) of the form
  `photos_cid/<place_id>/<NN>_<label>.jpg`:
  - `NN` is two digits (00–19) and can have gaps;
  - `label` is `vibe` (atmosphere photos) or `all` (any photo). The validator also accepts `review`.
  - No place mixes labels. For 7,577 places the first photo is `all`, for 4,925 it is `vibe`. Over all keys: 48,745
    `all` and 29,875 `vibe`.
- **Order.** Keys are listed in the dashboard's order: label `vibe`, then `review`, then anything else, then by photo
  index. Always show them in the order given; the first one is the hero.
- **How many each response carries:**

  | Response | Photos |
  | --- | --- |
  | Plan / edit `places[pid].photos` (PlaceCard) | first 4 (one hero, three thumbnails) |
  | Place screen | up to 10 |
  | Search result | first 1 (`photo`) |

  Each photo is `{"key": "...", "url": "..." | null}`.
- **Keys are opaque, stable references**, the same convention as the onboarding artifacts. They are not
  content-addressed: a re-scrape may replace `00_all.jpg` in place.

### 7.2 Coverage (Bucharest)

**Places with photos:** 12,502 (96.46%); 459 places have none.

| Theme group | With photos |
| --- | --- |
| food_drink | 96.7% |
| things_to_do | 96.7% |
| shopping | 97.0% |
| sights | 95.3% |

The weakest themes are markets_walks (84.8%) and nature_outdoors (93.2%).

**Photos per place:** 5,547 places have 10 and 2,305 have exactly 1.

**Files:** the 78,620 keys point to 31.4 GB of original JPEGs, about 400 KB on average, at most 1.8 MB, and about
1440 px on the long side.

| Group | Size |
| --- | --- |
| food_drink | 19.7 GB |
| sights | 8.2 GB |
| things_to_do | 2.3 GB |
| shopping | 1.2 GB |

Only the first 4 per place: 39,964 files, 17.0 GB. Only the first: 5.1 GB.

### 7.3 Where the files live

| Where | Path |
| --- | --- |
| Research machine | `recommendation_system/ai_location_recommender/data/visual_photo_profiles/photos_cid/<cid>/` (81,777 files, 33 GB). All 78,620 referenced files exist; the 2026-10-03 build checked them with `--photos-root` |
| Server | `/opt/sloco-data/visual_photo_profiles/photos_cid/<cid>/` |
| Container | Not mounted. **The service never reads photo files**: it only returns keys, plus URLs when `PHOTO_BASE_URL` is set. A missing file shows up only as a broken image (an nginx 404) |

To ship the files, use `SLOCO_SSH=user@host deploy/sync_bundle.sh --photos`. It is an incremental
`rsync --size-only`, about 20 GB the first time, and it makes the tree world-readable. Alternatively use the research
repo's `deploy/sync_data_to_server.sh`.

### 7.4 Server status: check before launch

According to the 2026-08-01 handoff, the server tree held 11,483 place directories (20 GB): the food and
things-to-do subset with at most 6 photos per place, and **no sights or shopping photos**. To match this bundle the
server needs either:
- 52,531 more files (20.6 GB) for all 10 photos per place; or
- 18,281 more files (8.0 GB) for the first 4.

It is not known whether the all-theme sync has run since. Count the files on the server:

```bash
docker run --rm \
  -v /opt/sloco-data/walk/bundles:/bundles:ro \
  -v /opt/sloco-data/visual_photo_profiles:/photos:ro \
  sloco-walk-planner:<tag> \
  python -m walk_planner bundle validate /bundles/<bundle_id> --photos-root /photos/photos_cid
# look at:  [ok] photo_files — keys=78620, existing=..., missing=..., places_with_photo_file=...
```

The entrypoint passes this command through without a bundle pre-check. The container runs as uid 10001, so the tree
must be readable by others.

### 7.5 Serving: `PHOTO_BASE_URL` and nginx

**URL format.** `url = PHOTO_BASE_URL.rstrip("/") + "/" + key`.
- `PHOTO_BASE_URL` must start with `http://`, `https://` or `/`; otherwise the service refuses to start.
- Empty or unset means `url: null`, and responses carry keys only.
- `/v1/meta` shows the value without credentials, query string or fragment.

**nginx.** [`deploy/photos.nginx.conf.example`](../deploy/photos.nginx.conf.example) serves the tree with the host's
existing nginx; no application is involved. Example: with `PHOTO_BASE_URL=https://api.example.com/walk-media`, the
URL `https://api.example.com/walk-media/photos_cid/10001948988812759057/00_all.jpg` maps to
`/opt/sloco-data/visual_photo_profiles/photos_cid/10001948988812759057/00_all.jpg`.

The example config:
- serves only `photos_cid/<digits>/<NN>_<label>.jpg`; everything else, including directory listings and dotfiles,
  is 404;
- allows GET and HEAD only;
- caches for 7 days with `stale-while-revalidate` (keys are not content-addressed) and never caches a 404;
- sends CORS `*`, uses `open_file_cache`, writes its own access log, and includes an optional per-IP rate limit.

Install it:
1. Paste the `location` block into the server block that should serve the photos.
2. Run `nginx -t && systemctl reload nginx`.
3. Check:
   - `curl -sI <base>/photos_cid/<cid>/00_all.jpg` should give 200 `image/jpeg` with `Cache-Control`;
   - `curl -sI <base>/photos_cid/` should give 404.

### 7.6 Resizing (recommended before launch)

Originals average about 400 KB, while a phone card needs 50–150 KB. Average sizes after resizing, measured on 120
photos at JPEG quality 80:

| Long side | Average size |
| --- | --- |
| original (~1440 px) | 398 KB |
| 1080 px | 124 KB |
| 600 px | 47 KB |
| 240 px | 10 KB |

Four photos per place at 600 px, plus 240 px thumbnails, come to about **2.3 GB**. The first four originals take
17.0 GB.

Two ways to serve smaller images:

1. **Pre-generate offline.** Create, for example, a 1080 px hero size and a 240–480 px thumbnail size, in WebP or
   JPEG. Put each in a sibling tree with the same relative key (`photos_cid_w1080/<cid>/<NN>_<label>.jpg`, ...).
2. **Resize on the fly.** Put an image proxy (imgproxy) or a resizing CDN with caching in front of `/walk-media/`.

Either way, keep the key format. Responses return the `key` next to the `url`, so the gateway can build a
size-specific URL from the key, for example a thumbnail base for list cards and the hero base for the gallery.

### 7.7 Rights

These are Google Maps place photos collected through Scrapingdog, not SLOCO content. Confirm that the product may
serve them publicly. If they must not be world-readable, protect the URL prefix instead, for example with nginx
`secure_link` and expiring URLs signed by the gateway (the nginx example describes this).

---

## 8. Interest artifacts

Personalisation by favourites and want-to-go places uses the files in `interest/`. Their rows are in the same order
as `walk_catalog.parquet`, which validation checks. Plans without favourites use only the catalog columns: cold-start
interest from review count and rating.

| File | Bucharest | Content |
| --- | --- | --- |
| `text_f16.npy` | 12,961 × 1536 float16, 39.82 MB | Text embedding of each place's AI summary (OpenAI `text-embedding-3-small`), L2-normalised |
| `image_f16.npy` | 12,961 × 512 float16, 13.27 MB | OpenCLIP ViT-B/32 (laion2b) embedding of the place's photos, L2-normalised. Places without one get a zero row; shape `(n, 0)` when built without an image store |
| `has_image.npy` | 12,961 bool, 0.01 MB | 12,510 true |
| `csls_density.npy` | 12,961 float32, 0.05 MB | Hubness density (CSLS, k = 10) over the city, precomputed so that a request takes tens of milliseconds |
| `features.parquet` | 0.36 MB | Per place: `place_id`, `theme`, `has_text`, `has_axes`, `quality` (v2, prior 25), the 8 vibe-axis columns and the `tags` list |
| `interest_meta.json` | 1.3 KB | Version `walk_interest_v1`, models, channel weights, CSLS parameters, row counts, source file names, fingerprint |

**Cost per worker:**
- loading memory-maps the arrays in 30–70 ms;
- a request with 3–6 favourites takes 21–26 ms over all 12,961 places;
- memory: about 27 MB of features and ids, 53 MB of mapped float16 (shared between processes), and 101 MB of float32
  working copies.

**When personalisation is unavailable.** If a bundle has no interest block, or the module fails, plans fall back to
popularity with the `personalization_unavailable` message. Multi-profile favourites need scikit-learn, which the
service image includes.

---

## 9. Building a bundle

### 9.1 Inputs

All inputs come from the research data directory `recommendation_system/ai_location_recommender/data/`. Sizes are
from the current manifest.

| Role (`--flag`) | File under the data dir | Bytes | Produced by |
| --- | --- | ---: | --- |
| `--catalog-csv` | `locations_bucharest_all.csv` (12,961 rows × 64 columns) | 83,859,042 | `build_city_catalog.py` |
| `--photo-manifest-csv` | `visual_photo_profiles/image_metadata/visual_photo_metadata_cid_bucharest_all.csv` (78,620 rows: `place_id, photo_source, local_file, photo_index, photo_index_in_place`) | 18,394,528 | `build_city_catalog.py`, from the Scrapingdog download manifest |
| `--text-npy` | `embedding_store/location_embeddings_bucharest_all.npy` (12,961 × 1536 float32) | 79,632,512 | `build_city_catalog.py`, which stacks the per-group vectors from `data_scraping/output/ai_location_summaries/_cities/<city>/<group>/` |
| `--text-meta-csv` | `embedding_store/location_embeddings_bucharest_all_metadata.csv` | 949,028 | `build_city_catalog.py` |
| `--image-npy` | `direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1.npy` (multi-city store, 26,453 × 512 float16; the builder picks the city's rows by place id) | 27,088,000 | `colab_image_embedding.py` (OpenCLIP, run on Colab), merged 2026-09-20 |
| `--image-meta` | `direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1_metadata.parquet` | 1,108,464 | same |
| `--photos-root` | `visual_photo_profiles/photos_cid/` (33 GB) | — | Only checked for existence; JPEGs are never copied into a bundle |

### 9.2 Command

From the root of the research repository, with the SLOCO venv:

```bash
cd "/Users/ilya/Documents/VisualStudioCode/SLOCO/untitled folder/sloco_recommendation_system"
PYTHONPATH=services/walk_planner /Users/ilya/Documents/VisualStudioCode/SLOCO/venv/bin/python \
  -m walk_planner bundle build \
  --data-dir recommendation_system/ai_location_recommender/data \
  --city-slug bucharest
# -> recommendation_system/ai_location_recommender/data/walk_bundles/<bundle_id>/   (default --out-root)
```

`--data-dir` fills every input from the research layout (§9.1) for `--city-slug bucharest`. The same build with every
input spelled out, for example when the files live elsewhere:

```bash
D=recommendation_system/ai_location_recommender/data
PYTHONPATH=services/walk_planner python -m walk_planner bundle build \
  --out-root "$D/walk_bundles" --city-slug bucharest --city Bucharest --timezone Europe/Bucharest \
  --catalog-csv "$D/locations_bucharest_all.csv" \
  --photo-manifest-csv "$D/visual_photo_profiles/image_metadata/visual_photo_metadata_cid_bucharest_all.csv" \
  --photos-root "$D/visual_photo_profiles/photos_cid" \
  --text-npy "$D/embedding_store/location_embeddings_bucharest_all.npy" \
  --text-meta-csv "$D/embedding_store/location_embeddings_bucharest_all_metadata.csv" \
  --image-npy "$D/direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1.npy" \
  --image-meta "$D/direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1_metadata.parquet"
```

**Other options:**

| Option | Effect |
| --- | --- |
| `--max-photos N` | Photos per place (default 10) |
| `--no-photos-check` | Keep manifest rows even if the JPG is absent (when the photo tree is not on the build machine) |
| `--no-interest` | No `interest/`, so no personalisation |
| `--no-validate` | Skip the deep validation after the build |

The package-root form `cd services/walk_planner && python -m walk_planner bundle build --data-dir ../../recommendation_system/ai_location_recommender/data --city-slug bucharest`
is equivalent; the current manifest records this command with an absolute `--data-dir` (`builder.cmd`). The build needs numpy, pandas and pyarrow, plus
scikit-learn for the interest part. A Python ≥ 3.10 with `deploy/requirements.lock` works.

### 9.3 What the builder does

1. **Catalog.** Reads the catalog CSV with `CSV_READ_OPTIONS`: `place_id` as `str`, floats with
   `float_precision="round_trip"`. Keeps the city's rows in CSV order, all of them: closed places and places without
   coordinates too.
2. **Photos.** Reads the photo manifest. With `--photos-root`, drops rows whose JPG is missing, as the dashboard
   loader does. Keeps at most `--max-photos` keys per place, in display order.
3. **Ids.** Checks that `place_id` values are decimal and unique.
4. **Taste artifacts.** Builds them from the text and image stores (`interest_build.taste_artifacts_from_files`),
   aligned to the catalog rows by `place_id`.
5. **Identity.** Computes `catalog_sha256`, the fingerprint and `content_sha256`, which give the `bundle_id`.
6. **Write.** Writes everything into a temporary directory next to the target, then:
   - writes the manifest with the size and sha256 of every file;
   - checks that the result loads exactly as the service will load it;
   - makes it readable (permissions 0777/0666 minus the umask);
   - renames it into place in one atomic step.

   It never overwrites an existing bundle.
7. **Validate.** Runs the deep validation (§2.4) and prints the report. The exit code is 1 if the bundle is invalid.

### 9.4 Time, memory, output

Measured 2026-10-03 on the research Mac (Apple silicon, Python 3.13):

| Step | Detail | Time |
| --- | --- | --- |
| Build | catalog 0.8 s, photos 1.7 s, catalog table 0.5 s, interest 2.7 s, write 0.3 s | 6.2 s |
| Deep validation | including all 78,620 photo files | 1.4 s |
| **Total** | 8.3 s wall clock; 9–10 s on a busier machine | |

- **Peak memory:** about **1.2 GB** RSS.
- **Output:** 60.0 MB.
- **Disk:** keep about 3 bundles per city; each Bucharest bundle is about 60 MB.

### 9.5 Shipping and switching

```bash
# from the build machine: verify locally, upload to .incoming-<id>, sha256-check on the server, publish atomically
SLOCO_SSH=deploy@<server> deploy/sync_bundle.sh <bundle dir> --validate

# on the server: select it, restart the planner, check
#   /opt/sloco-data/walk/walk.env:  WALK_BUNDLE_ID=<bundle_id>
docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml up -d walk-planner
curl -fsS http://127.0.0.1:18600/v1/health/ready      # {"status":"ready","bundles":["<bundle_id>"]}
```

**What `sync_bundle.sh` guarantees:**
- it never overwrites an existing bundle;
- if the server copy is identical, it exits 0; if it differs, it exits 4;
- `--dry-run` shows the transfer without doing it.

**Rollback:** set the previous `WALK_BUNDLE_ID` and run `up -d walk-planner` again. The full runbook is in
[DEPLOY.md](DEPLOY.md).

---

## 10. Refreshing the data

### 10.1 What goes stale

| Data | Source | Current snapshot | Risk |
| --- | --- | --- | --- |
| `opening_hours` | DataForSEO Business Listings | 2026-06-10 (11,315 rows), 2026-07-16 (1,646) | Seasonal and holiday hours, changed schedules |
| `business_status` | DataForSEO | same | New closures and reopenings; seasonal `temporarily_closed` |
| Rating, review count, address, coordinates, price level | DataForSEO | same | Slow drift. Cold-start interest moves with review counts, so plans change |
| AI texts, tags, axes, text embeddings | LLM summaries and embeddings (SLOCO `data_processing/`) | per place | Changes only when places are added or re-summarised |
| Image embeddings | OpenCLIP store | merged 2026-09-20 | New places have no vector until it is re-run |
| Photos | Scrapingdog | June–July 2026 | New places have no photos |
| The set of places | DataForSEO + classification + summaries | | New venues are missing |

### 10.2 Recommended cadence

- **Hours and status: before launch, then monthly.** They are 3–4 months old today, and the snapshot contains
  seasonal closures. A refresh costs a few dollars per city in DataForSEO fees ($0.01 per request plus $0.0003 per
  returned listing). The existing Bucharest download (795 categories, 88 requests, 13,193 places) cost about $5.9
  in total. The machine time is about 10 minutes.
- **New places: quarterly or on demand.** New places need AI summaries, embeddings and photos before
  `build_city_catalog.py` includes them; places without a summary are skipped. This costs LLM, embedding and photo
  budget.
- **The OSRM street map: monthly.** It is independent of the bundle; see [ROUTING.md](ROUTING.md).

### 10.3 Refresh procedure (hours, status, ratings)

1. **Re-download the city from DataForSEO.** Run this from the SLOCO root; it needs the DataForSEO credentials of
   `get_data_pipeline`. A full run truncates `places.jsonl`, so back it up first.

   ```bash
   cd /Users/ilya/Documents/VisualStudioCode/SLOCO
   C=get_data_pipeline/data/dataforseo/bucharest_romania
   cp "$C/places.jsonl" "$C/places.$(date +%Y%m%d).jsonl"          # a full run truncates places.jsonl
   # the exact category set of the existing download (795 slugs, recorded in city_summary.json)
   CATS=$(venv/bin/python -c 'import json, sys; print(",".join(json.load(open(sys.argv[1]))["categories"]))' "$C/city_summary.json")
   venv/bin/python -m get_data_pipeline.cli.download_city --city "Bucharest, Romania" --categories "$CATS" --dry-run
   venv/bin/python -m get_data_pipeline.cli.download_city --city "Bucharest, Romania" --categories "$CATS" --force --no-resume
   ```

   - **Reuse the recorded category set.** `get_data_pipeline/config/` has no `categories_selected.yaml`, only
     `.example` and `.suggested` files, and a wider list returns places that have no summary yet.
   - **`--dry-run` is free.** `estimate_city` projects the exact cost, but its probes cost about $0.7.
   - **`--no-resume` is needed.** Otherwise the old `progress.json` makes the run skip groups.
2. **Rebuild the catalog CSV.** Keep a copy of the old CSV for diffing. This also rewrites the stacked text store and
   the photo manifest; nothing is re-embedded, and no paid API is called.

   ```bash
   cd "/Users/ilya/Documents/VisualStudioCode/SLOCO/untitled folder/sloco_recommendation_system"
   PYTHONPATH=.:services/walk_planner /Users/ilya/Documents/VisualStudioCode/SLOCO/venv/bin/python \
     recommendation_system/ai_location_recommender/build_city_catalog.py --city bucharest_romania
   ```

   `build_city_catalog.py` imports `walk_planner`, so `services/walk_planner` must be on `PYTHONPATH`. Its docstring
   still says `PYTHONPATH=.`, which is stale.
3. **Build and validate the bundle** (§9.2). It gets a new content hash and a new id.
4. **Review the change against the deployed bundle:**

   ```python
   import pandas as pd
   cols = ["place_id", "name", "opening_hours", "business_status"]
   old = pd.read_parquet("<old bundle>/walk_catalog.parquet", columns=cols).set_index("place_id")
   new = pd.read_parquet("<new bundle>/walk_catalog.parquet", columns=cols).set_index("place_id")
   print("removed", len(old.index.difference(new.index)), "added", len(new.index.difference(old.index)))
   both = old.index.intersection(new.index)
   print("hours changed", (old.loc[both, "opening_hours"].fillna("") != new.loc[both, "opening_hours"].fillna("")).sum())
   print(pd.crosstab(old.loc[both, "business_status"], new.loc[both, "business_status"]))
   ```
5. **Generate the golden outputs** for the new bundle (§10.4), then ship and switch (§9.5).
6. **Tell the gateway.** Its caches keyed by `versions.catalog` (id map, photo URLs) must be refilled. It may also want
   to re-import new places into Supabase (§6.3).

### 10.4 Golden outputs for a new bundle

The golden expected outputs are tied to one bundle: `golden/expected/<bundle_id>/`.

- **A new data version** gets its own set:
  - run `python -m walk_planner golden update --bundle <new bundle>` from `services/walk_planner`;
  - commit it with the release;
  - keep the old set while the old bundle is deployed.

  Plans legitimately change with new data, so review the differences rather than expecting none:

  ```bash
  python golden/tools/compare_expected_sets.py golden/expected/<old id> golden/expected/<new id> -v
  ```

  This lists every changed value per scenario. It exits 1 on substantive differences, which is expected here.
- **A rebuild of the same data** (same `content_sha256`, another date) needs no new set; `golden run` uses the
  existing one.
- **A rebuild that should be float noise only** (new builder or platform) must be proven with
  `golden/tools/compare_expected_sets.py`. See [golden/README.md](../golden/README.md), section "Updating".
- **Acceptance of a deployment.** Run `golden run --url` against a temporary instance that runs **without** street
  routing and photo URLs, for example the same image on another port with only `WALK_BUNDLE_DIR` set. The golden
  outputs use straight-line estimates and null photo URLs, so the production instance (with OSRM) reports
  differences by design.

### 10.5 What clients notice after a switch

- `versions.catalog` and `request.catalog_version` change, and so do `plan_id` values.
- Saved plans can still be edited: names, hours and status are re-read from the new bundle. Expect:
  - 409 `catalog_changed` for a place that disappeared;
  - 422 `place_closed_forever` for one that closed.
- Cold-start interest is normalised by the city's maximum review count, so some candidate rankings move.

---

## 11. Adding a city

### 11.1 What exists today

From the 2026-10-02 research dry runs of `build_city_catalog.py`, which wrote nothing:

| City | Status |
| --- | --- |
| **Bucharest** | Built. The only city with an all-theme catalog |
| **Berlin** | **37,738 rows buildable today** (food 23,267 · sights 10,179 · things to do 2,248 · shopping 2,044), all in one embedding recipe. Hours 68.7%; 5,722 closed. The photo manifest covers 96.6% of places (242,934 photos, about 97 GB of originals), but **none are on local disk**. Text embeddings would be about 232 MB |
| **Tbilisi** | **3,289 rows buildable** (food top-up 647 · sights 2,198 · shopping 444). The main food set (`food_drink_main`, about 4,967) and `things_to_do` (about 274) must first be seeded with `data_processing/reembed_from_catalog.py --city tbilisi_georgia` (SLOCO root). Hours 40.9%; 472 closed. 80% of places are in the Scrapingdog manifest (18,150 photos), none on local disk |
| **Kyiv** | Raw DataForSEO, stage0, Apify and Scrapingdog data only. No summaries or embeddings, and not registered in `build_city_catalog.py` |

The OpenCLIP image store is multi-city, but its coverage of Berlin and Tbilisi has not been measured. Places without
an image vector get zero on the image channel, so personalisation then relies on text, tags, axes, quality and price.

### 11.2 Steps

1. **Inputs for `build_city_catalog.py`:**
   - `_cities/<city>/<group>/` with `order_place_ids.json`, `emb_<city>_<group>.npy` and
     `<city>_<group>_summaries.jsonl` (missing groups are skipped);
   - DataForSEO `places.jsonl`;
   - stage0 `<city>_places_with_reviews.jsonl` and `place_classification.csv`;
   - optionally Apify `reviews.jsonl`;
   - the Scrapingdog `_download/manifest.shard*.jsonl`.

   It finds them under the SLOCO root (`SLOCO_ROOT`).
2. **Register and build the catalog.** Add the city to `CITY_LABEL` in `build_city_catalog.py` if it is not there
   (`berlin_germany` and `tbilisi_georgia` are), then run it with `--city <city>`. It writes
   `locations_<prefix>_all.csv`, the text store and the photo manifest; the prefix is the part of the city id before
   the first `_` (`berlin`).
3. **Photos.** Put the JPEGs on the build machine (`deploy/unpack_photos_from_zips.py` extracts them from the Google
   Drive zips), or build with `--no-photos-check`. Sync them to the server (§7.3).
4. **Time zone.** `catalog.CITY_TIMEZONES` knows Bucharest, Berlin, Tbilisi and Kyiv. For any other city pass
   `--timezone <IANA name>`.
5. **Validation area.** Add a box to `bundle.CITY_BBOXES`; otherwise validation accepts places within 80 km of the
   median.
6. **Build.** `python -m walk_planner bundle build --data-dir <data dir> --city-slug berlin`. `--data-dir` finds
   `locations_<slug>_all.csv` and the other inputs by the slug.
7. **Street routing.** The OSRM dataset must cover the city, otherwise its legs are flagged estimates. See
   [ROUTING.md](ROUTING.md), multi-city.
8. **Serve both cities.** The service serves one bundle per city, and requests choose by `city`;
   `GET /v1/walks/config` lists the cities.
   - Set `WALK_BUNDLE_DIR` to a comma-separated list: `/bundles/<bucharest id>,/bundles/<berlin id>`.
   - The compose file currently interpolates a single `WALK_BUNDLE_ID` into that variable, so change that line.
     Prefer explicit ids over the `/bundles` root (§2.5).
   - Memory per worker grows with each bundle: the catalog plus the taste arrays, roughly 3× Bucharest's for Berlin.
9. **Supabase.** Import the city's places with `source_id = place_id` (§6.3).
10. **Golden (recommended).** Add a few scenarios for the city to `golden/scenarios.json` and generate their expected
    set.

---

## 12. Data quality caveats

1. **Unknown hours count as open.**
   - 36% of places (4,665), and 54% of sights, have no hours. Their stops say «часы работы неизвестны» and
     `hours.status: "unknown"`.
   - `known_hours_only` excludes them from slots, but it shrinks the pools a lot. Across the whole city, sights go
     from 1,331 to 631 and parks from 605 to 286.
2. **Stale snapshot.**
   - Hours and status date from 2026-06-10 and 2026-07-16, with no holiday hours.
   - Seasonal closures are frozen in: Bucharest Fountains (13,842 reviews) and Divertiland Park are
     `temporarily_closed`. A manual override list is a v1.1 idea.
   - 202 closed places still list hours.
3. **AI text is in English** while the app is in Russian. This covers the card summary, type label, place-screen
   sections and tags; the card says `summary_lang: "en"`. Names are Google's titles, mostly Romanian. Translating
   during the bundle build is a v1.1 option.
4. **Two columns change meaning by theme group.** `ai_food_and_drinks` and `ai_service` mean different things per
   group (§3). The place endpoint already labels them by group; do not show the raw column names.
5. **Category quirks.**
   - `leisure_active` contains 146 casinos and 70 gambling houses, which can fill the "entertainment" slot.
   - Food-and-drink slots match keywords as substrings.
   - Both are known v1 limitations; see WP-02 in the research repo's
     `recommendation_system/ai_location_recommender/WALK_PLANNER_UX_TEST_REPORT.md`.
6. **Low AI confidence is not filtered.** 3,513 places (27%), including 1,023 sights, have `ai_confidence: "low"`.
   The planner uses them; the old recommender path dropped them.
7. **Sparse fields.**
   - `price_level` covers 9.7% of places and holds text, not a number.
   - Ratings cover 90.9%.
   - 459 places have no photos.
   - The server photo tree is probably incomplete (§7.4).
8. **Supabase coverage gap.** About 5,624 places may have no `places.id` (§6.3).
9. **Dashboard and service read the data differently.** The research dashboard still parses the CSV with pandas'
   default float parser, while the service uses the bundle, which is round-trip parsed. On exact ties the dashboard
   can therefore show a loop the other way round: golden S19, variants 2 and 3. A navigation-link coordinate can also
   differ by one unit in the 6th decimal. Routes and times are otherwise identical.
10. **Time zone.** All times are city-local wall-clock time. Windows that cross a daylight-saving change are one hour
    off in real time. Holidays are not modelled.

---

## 13. Quick reference

```bash
# all commands from services/walk_planner (or with PYTHONPATH=services/walk_planner from the research repo root)
python -m walk_planner bundle build --data-dir ../../recommendation_system/ai_location_recommender/data --city-slug bucharest
python -m walk_planner bundle validate <bundle> [--shallow] [--photos-root <photos_cid>]
python -m walk_planner bundle info <bundle or root>
python -m walk_planner search "stavropoleos" --bundle <bundle> --pretty     # catalog lookup
python -m walk_planner place <place_id> --bundle <bundle>                    # place screen JSON
python -m walk_planner golden update --bundle <new bundle>                   # expected set of a new data version
python -m walk_planner golden run --bundle <bundle>                          # 26/26 on the current bundle
SLOCO_SSH=deploy@<server> deploy/sync_bundle.sh <bundle> --validate          # ship (never overwrites)
SLOCO_SSH=deploy@<server> deploy/sync_bundle.sh --photos                     # photo tree (incremental)
```

| Fact | Value (Bucharest) |
| --- | --- |
| Current bundle | `bucharest-20261002-d68a311e` (`content_sha256` d68a311e..., `catalog_sha256` e667fd9c..., interest fingerprint c6dd60e9...) |
| Places | 12,961: food_drink 8,884 · sights 2,507 · things_to_do 1,001 · shopping 569 |
| Known opening hours | 64.01% |
| Closed | `closed_forever` 1,961 · `temporarily_closed` 481 |
| Photos | 12,502 places, 78,620 keys, 31.4 GB of originals |
| Interest | 12,961 with a text vector, 12,510 with an image vector |
| Size | 60.0 MB on disk; about 0.4 s to load with hashing |
| Build | about 8 s and 1.2 GB peak memory |
