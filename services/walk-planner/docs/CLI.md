# Walk Planner command line: `python -m walk_planner`

The developer CLI of the package. It runs the same code as the service: `plan`, `schedule`, `insert`, `search`,
`place` and `config` produce the JSON of the matching HTTP endpoint (`walk_planner.cli.api_plan` and the others
are the functions the golden outputs were recorded with). It also builds and checks data bundles, replays the
golden outputs, probes the routing chain and starts the service. `python -m walk_planner <command> --help` prints
the same options as this page; this page adds what the help does not say.

Run it from the package root (`services/walk_planner`), from any directory once the package is installed
(`pip install -e .`, console script `walk-planner`), or inside the service image
(`docker run --rm … sloco-walk-planner:<tag> python -m walk_planner …`). `python -m walk_planner --version` prints
the algorithm version.

**Exit codes.** 0 = success. 1 = the command ran and the answer is a failure: a request error (the error body is
printed as JSON, and `HTTP <status> <code>: <message>` goes to stderr), an invalid bundle, a golden difference.
2 = a usage or set-up problem: bad arguments, no bundle found, an unreadable file. 130 = interrupted.

## Common options

| Option | Commands | Meaning |
|---|---|---|
| `--bundle B` | every command that reads a bundle: `plan`, `schedule`, `insert`, `search`, `place`, `config`, `interest`, `bundle validate`, `bundle info`, `golden run`, `golden update`, `serve` | A bundle directory, `a,b`, or a directory of bundles (the newest bundle per city). Default: `$WALK_BUNDLE_DIR`, and inside the research repo `recommendation_system/ai_location_recommender/data/walk_bundles`. In any other copy, without either, the command stops with exit 2: "no bundle: pass --bundle PATH or set WALK_BUNDLE_DIR" |
| `--city C` | the same, except `serve` | The city to use when the bundles cover several |
| `--no-verify` | the same, except `serve` | Skip the sha256 check of the bundle files (faster; the service's `WALK_VERIFY_BUNDLE=false`) |
| `--compact` | `plan`, `schedule`, `insert`, `search`, `place`, `config`, `interest`, `route`, `bundle validate`, `bundle info` | One-line JSON instead of indented JSON |
| `--lang ru\|en` | `plan`, `schedule`, `insert`, `search`, `place` | Language of the texts (default `ru`) |
| `--geometry geojson\|polyline6` | `plan`, `schedule`, `insert`, `route` | Segment geometry format (default `geojson`) |
| `--pretty` | `plan`, `schedule`, `insert`, `search` | Human-readable output instead of JSON: for plans and edits the research dashboard's Russian summary (metrics, notes, stop cards, the Google Maps link); for search one line per place |
| `--links` | `plan`, `schedule`, `insert` | With `--pretty`, also the per-leg navigation links |
| `--routing auto\|estimate` | `plan`, `schedule`, `insert`, `route` | `auto` (default): the street-routing chain configured in the environment (`WALK_ROUTER_URL`, `ORS_API_KEY`, …), exactly as the service builds it. `estimate`: straight lines only, as in the golden outputs |
| `--photo-base-url URL` | `plan`, `schedule`, `insert`, `search`, `place` | `PHOTO_BASE_URL` for photo `url`s (default: the environment variable; without it `url` is `null`) |

## Planning and editing

**`plan`**: `POST /v1/walks/plan`. Without options it plans the default walk (10:00–14:00 today, loop from the
city centre, sight, coffee, park, food). Flags override the fields of `--request`.

| Option | Request field / meaning |
|---|---|
| `--request FILE` | a plan request JSON file (`-` = stdin), the HTTP body; the flags below override its fields |
| `--date YYYY-MM-DD` | `date`; default **today in the city's time zone** (the HTTP API has no default) |
| `--start HH:MM`, `--end HH:MM`, `--end-day-offset 0\|1` | `start_time` (default 10:00), `end_time` (default start + 4 h), `end_day_offset` |
| `--slots LIST` | `slots`, in order, with optional minutes: `sight,coffee:15,park,food:90` |
| `--shape`, `--style` | `shape` (`loop`, `one_way`, `free`), `style` (`max`, `chill`, `scenic`). The CLI also accepts the dashboard's Russian labels |
| `--start-latlon LAT,LON` | `start: {lat, lon}`; default the city centre |
| `--start-place ID` | `start: {place_id}` |
| `--must IDS` | `must_visit_place_ids`: a comma list, the option repeatable |
| `--fav IDS`, `--wtg IDS` | `favourite_place_ids`, `want_to_go_place_ids` (comma lists, repeatable) |
| `--strength X` | `personalization_strength` 0–1 (default 0.5) |
| `--variants N`, `--radius KM`, `--top-k N` | `variants` (1–5, default 3), `radius_km` (default 2.5), `top_k` (bench only, default 8) |
| `--no-fill`, `--known-hours-only` | `fill_window: false`, `known_hours_only: true` |
| `--debug` | add the `debug` block (search area, every candidate) |
| `--timing` | print the planning time to stderr |

**`schedule`** (`POST /v1/walks/schedule`) and **`insert`** (`POST /v1/walks/insert`) take the request body in one
of two ways:

| Option | Meaning |
|---|---|
| `--request FILE` | the HTTP body JSON (`-` = stdin) |
| `--from-plan FILE` | a saved plan or edit response: its `request` echo and the `sequence` of variant `--variant` (default 0) |
| `--edit OP` | `schedule` only: change the sequence first, in order, repeatable: `move:FROM:TO`, `remove:I`, `dwell:I:MIN` |
| `--place ID` | `insert` only: the place to add (`place_id`) |
| `--allow-temporarily-closed` | `insert` only: `allow_temporarily_closed: true` |
| `--dwell MIN` | `insert` only: `dwell_min` of the new stop (default: estimated) |

Example: plan, then move stop 0 to position 3, then add a place (straight-line routing):

```bash
python -m walk_planner plan --bundle "$B" --routing estimate --date 2026-10-03 > plan.json
python -m walk_planner schedule --bundle "$B" --routing estimate --from-plan plan.json --edit move:0:3 --pretty
python -m walk_planner insert --bundle "$B" --routing estimate --from-plan plan.json --place 10915586233752676659 --pretty
```

## Places and configuration

| Command | Endpoint | Options |
|---|---|---|
| `search QUERY` | `GET /v1/walks/places/search` | `--near LAT,LON` (rank nearby places higher, add `distance_m`), `--limit N` (default 20), `--include-closed` (also `closed_forever` places). The JSON is `{query, results}`, a shorter envelope than the HTTP response; `results` are the same |
| `place ID` | `GET /v1/walks/places/{place_id}` | — |
| `config` | `GET /v1/walks/config` | — (the labels of both languages are in the output) |

## Inspection

**`interest`**: what favourites do to the interest of places, without planning. Prints, per place, the cold-start
rank and value, the personalised value, the taste percentile and the most similar seed.

| Option | Meaning |
|---|---|
| `--fav IDS`, `--wtg IDS` | favourites and want-to-go ids (comma lists, repeatable) |
| `--strength X` | blend strength 0–1 (default 0.5) |
| `--top N` | places to show (default 20) |
| `--theme T` | only one catalog theme or theme group, e.g. `culture_sights` |
| `--json` | JSON instead of a table |

**`route`**: one call of the routing chain on coordinates, with the configuration of the environment. Prints the
provider, the quality and every leg, or with `--json` the legs with geometry, the chain's events and the routing
status. Needs no bundle.

| Option | Meaning |
|---|---|
| `--coords "lat,lon;lat,lon[;…]"` | required: the points in order |
| `--routing auto\|estimate` | as above |
| `--json` | JSON output |

## Data bundles

`bundle build` builds a bundle from the research data; `bundle validate` and `bundle info` check and describe
one. [DATA.md §2.4](DATA.md#24-validation-rules) and [§9](DATA.md#9-building-a-bundle) explain the checks and the
inputs.

| Command | Options |
|---|---|
| `bundle build` | `--data-dir DIR` fills every input from the research layout (`locations_<slug>_all.csv`, …) and sets `--out-root <data-dir>/walk_bundles`; or give each input: `--catalog-csv`, `--photo-manifest-csv`, `--text-npy`, `--text-meta-csv`, `--image-npy`, `--image-meta`, `--photos-root`. Also `--out-root`, `--city-slug`, `--city`, `--timezone`, `--max-photos N` (default 10), `--no-photos-check`, `--no-interest`, `--no-validate` |
| `bundle validate [PATH]` | `--shallow` (manifest, hashes, schema, alignment; what the image's entrypoint runs), `--photos-root DIR` (also count the photo files), `--json`. Exit 0 valid, 1 invalid, 2 usage error |
| `bundle info [PATH]` | `--json`: a summary of the manifest(s), no checks |

## Golden outputs

[golden/README.md](../golden/README.md) explains the comparison rules.

| Command | Meaning and options |
|---|---|
| `golden run` | Recompute every scenario with the bundle and compare with `golden/expected/<bundle_id>/`. `--scenarios FILE` (default `golden/scenarios.json`), `--expected-root DIR` (default `golden/expected`), `--only ID,ID`, `-v` (print every difference). With `--url http://host:port` it replays the recorded calls against a **running** service instead (the deployment acceptance): `--wait S` (seconds to wait until it is ready, default 60), `--timeout S` (per call, default 120). Exit 0 all pass, 1 a difference or a missing expected set, 2 the service is unreachable or not ready |
| `golden update` | Write the expected outputs of the bundle: `--scenarios`, `--expected-root`, `--only`, `-v`. Only in a release that changes plans, or for a new bundle ([RELEASE.md §5](RELEASE.md#5-golden-update-rules)) |
| `golden list` | The scenarios, their `baseline_v0` coverage and the expected sets: `--scenarios`, `--expected-root` |

## Running the service

**`serve`** starts `uvicorn walk_planner.service.app:create_app --factory` with this package on `PYTHONPATH`.

| Option | Meaning |
|---|---|
| `--bundle B` | sets `WALK_BUNDLE_DIR` (default: as for the other commands) |
| `--host H` | default `127.0.0.1`: reachable only from this machine. Use `0.0.0.0` to listen on every interface |
| `--port P` | default 8000 |
| `--workers N` | uvicorn worker processes; without it uvicorn reads `WEB_CONCURRENCY`, else runs one |
| `--reload` | restart on code changes (development only) |
| `--log-level L` | uvicorn's own log level; the service's level is `LOG_LEVEL` |

The other settings come from the environment ([DEPLOY.md §5](DEPLOY.md#5-environment-variables)); the service
reads no `.env` file. Set `ENVIRONMENT=development` for a local run, or `/v1/meta` reports `production`.
