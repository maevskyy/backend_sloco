# Walk Planner golden data

Two independent sets of reference outputs guard the Walk Planner. They answer different questions:

| | `baseline_v0/` | `expected/<bundle_id>/` |
| --- | --- | --- |
| Question | Did the refactor change behaviour? | Did the v1 API output change? |
| Produced by | The **pre-refactor dashboard** (git `f1b2196`), `tools/capture_baseline.py` | The **v1 package**, `python -m walk_planner golden update` |
| Content | Internal digests: candidates, `WalkPlan`s, the dashboard's rendered Russian text, navigation links | The full **API JSON** of `POST /v1/walks/plan` and of the `/schedule` / `/insert` calls of every edit chain |
| Data | `locations_bucharest_all.csv` (catalog sha256 in `baseline_v0/_meta.json`) | One fixed **data bundle** (`bucharest-20261002-d68a311e`) |
| Checked by | `tools/parity_check.py` (exit 0 = only the bug-1 / bug-2 differences) | `python -m walk_planner golden run` (exit 0 = identical); against a running service: `golden run --url` |
| Lifetime | Frozen: it records the pre-refactor page code (git `f1b2196`), which the dashboard no longer runs | Regenerated deliberately in a release that changes plans or the API (see "Updating") |
| Scenarios | S01–S24 | S01–S24 + P01, P02 |

`scenarios.json` drives both. Each line is one scenario in API codes (activities, styles, shapes); values a
scenario does not give default to S01's. Edit chains (`edits`) replay the dashboard editor's operations on
variant 1; see `baseline_v0/README.md` for the scenario interpretation and the op format.

## P01 / P02: personalization (no baseline_v0)

`P01_fav_s1` and `P02_fav_s4` are S01 plus favourites — the S1 and S4 seed sets of the personalization
research (`favourite_place_ids`, strength 0.5), with S01's five edit chains:

- P01 (S1: Origo, Herăstrău Park, National Museum of Art): one taste profile. Variant 1 is the research
  report's S1 route: Artmark · Palatul Cesianu-Racoviță · boteca13 · National Museum of Art · Royal Palace ·
  National Museum of Romanian Literature · Parcul Ateneului · MACE.
- P02 (S4: six mixed seeds incl. Mosto): two taste profiles (food vs museums). Clustering needs
  **scikit-learn** (the `interest` extra); without it the taste model falls back to one profile and P02 differs.

They exist only in `expected/`: favourites are new in v1 (the dashboard's old personalised path was replaced),
so there is **no `baseline_v0` digest** for them. Both carry `"baseline_v0": false` in `scenarios.json`.
`tools/parity_check.py` lists them as `SKIP` (and says so in its summary line) and `tools/capture_baseline.py`
does not capture them.

## Expected outputs: format

`expected/<bundle_id>/<scenario_id>.json`:

```text
{
 "format": "walk-golden/v1",
 "scenario": { ...the scenarios.json entry... },
 "plan": {"call": "POST /v1/walks/plan", "options": {"lang": "ru", "geometry": "geojson"},
          "body": { the plan request: the scenario's request fields }, "status": 200, "response": { ... }},
 "edits": {
   "<chain>": [ {"op": {...}, "resolved": {..., "place_id"}, "applied": true,
                 "call": {"call": "POST /v1/walks/schedule" | "POST /v1/walks/insert", "options": {...},
                          "body": {...}, "status": 200 | 4xx, "response": {...}} | null }, ... ]
 }
}
```

- `options` are the response options (language, geometry format), whether the service takes them as query
  parameters or body fields.
- An error is recorded like the API returns it: `status` 4xx and `response` = `{"error": {"code", "message", "params"}}`.
- `index.json` records the bundle (`bundle_id`, `content_sha256`, `catalog_sha256`, `interest_fingerprint`), the
  versions (api, algorithm, interest, bundle schema), the options, the tolerance, the scenarios file and its
  sha256, the environment (python / numpy / pandas / pyarrow / scikit-learn) and a sha256 per output file.
- Files are JSON written by `walk_planner.cli.golden_dumps`: one key per line, short objects and coordinate lists
  on one line, so a change shows up as a readable diff.

How the outputs are made (`walk_planner.cli.golden_scenario`):

- **Routing is forced to the straight-line estimate** (`core.RoutingProvider`): no router, no network, whatever
  the environment says. Segments carry `"quality": "estimate"` and every variant a `routing_estimate` message.
- `photo_base_url` is null, so photo `url`s are null (the keys are pinned).
- `body` of the plan call is the scenario's request fields (`walk_planner.cli.scenario_request`). S24 uses
  `top_k`, a bench-only field of the request.
- **Edit chains** run as the API calls a client makes, through `api_schedule` / `api_insert` (the same package
  calls as the service: `from_request_echo` → `schedule` / `insert_place` with the bundle's taste artifacts →
  `edit_response`). The client keeps variant 1's `sequence` and the removed stops:
  - `move`, `remove`, `restore` send the edited sequence to `/schedule`;
  - `set_dwell` is the dashboard's minutes editor: every stop gets its shown minutes (`int(round())`), the edited
    stop the new value, and all stops `dwell_fixed: true`; the sequence goes to `/schedule`;
  - `add` sends the place to `/insert` (`allow_temporarily_closed: false`). A refused add (404 / 409 / 422) is
    recorded and the chain continues with the unchanged sequence;
  - an op that changes nothing (a move onto itself) sends nothing: `call` is null, `applied` false.

  `applied` flags and the resulting routes agree with `baseline_v0` for every chain
  (`tests/test_golden.py::test_real_expected_set_agrees_with_baseline_v0`).

## Comparison rules (`golden run`)

- Object keys must match exactly; lists are compared in order (ids, stop order, messages, coordinates).
- Strings, booleans and null: exact. Numbers: absolute tolerance **1e-6** (`walk_planner.cli.golden_diffs`).
- `plan_id` is the sha1 of the exact request echo + versions, so last-bit float noise of an echoed value changes
  it (the city centre is a pandas mean: numpy 1.26 and 2.x differ by ~1e-14). It must always be the sha1 of the
  response's own request + versions, and it is compared with the expected one only when that request echo is
  bit-identical.
- A TimePoint clock (`"local"`) may differ by exactly one minute when its `offset_min` lies within 1e-6 of a half
  minute: the clock rounds the offset with Python's `round`, so 217.49999999999974 (numpy 2.4) gives 13:37 and
  217.50000000000003 (numpy 1.26) 13:38 — both correct. Seen once (S02 chain E) on numpy 1.26.
- Identity fields embed the bundle id: `plan_id`, `versions.catalog` and `request.catalog_version`. When the loaded
  bundle has no expected set of its own but one exists for the **same content** (`content_sha256`, e.g. the same
  data rebuilt on another day), that set is used and these three fields are not compared (a note says so).
- Exit code 1 on any difference, missing expected file, failed scenario, or scenario without a bundle for its city.

## How to run

From `services/walk_planner` (paths default to `golden/scenarios.json` and `golden/expected/`; the bundle defaults
to `$WALK_BUNDLE_DIR`, else `recommendation_system/ai_location_recommender/data/walk_bundles` of the research repo,
newest bundle per city):

```bash
PY=/path/to/venv/bin/python          # Python >= 3.10 with numpy, pandas, pyarrow (+ scikit-learn for P02)

# the bundle (once per data version; ~10 s for Bucharest)
$PY -m walk_planner bundle build --data-dir ../../recommendation_system/ai_location_recommender/data --city-slug bucharest

$PY -m walk_planner golden list                     # scenarios, baseline_v0 coverage, expected sets
$PY -m walk_planner golden run                      # ~25 s; table + differences; exit 1 on any difference
$PY -m walk_planner golden run --only S01_default,P02_fav_s4 -v
$PY -m walk_planner golden run --bundle /path/to/bundle_or_root

# the pre-refactor parity gate (independent of bundles; P01 / P02 are skipped: no baseline_v0)
PYTHONDONTWRITEBYTECODE=1 $PY golden/tools/parity_check.py [--source csv|bundle]
```

### Acceptance of a deployment: `golden run --url`

The same expected outputs replayed against a **running** service -- the check for the backend developer after
deploying the image with a bundle:

```bash
# the service must run WITHOUT street routing and photo URLs (the golden outputs use the straight-line estimate
# and null photo urls): no WALK_ROUTER_URL, no ORS_API_KEY, no PHOTO_BASE_URL
$PY -m walk_planner golden run --url http://127.0.0.1:18600            # [--only S01_default,P01_fav_s1] [-v]
# or from the image itself (it holds the package, not the golden files: mount them)
docker run --rm --network host -v "$PWD/golden:/golden:ro" sloco-walk-planner:<tag> \
    python -m walk_planner golden run --url http://127.0.0.1:18600 \
    --scenarios /golden/scenarios.json --expected-root /golden/expected
```

It reads `GET /v1/meta` (waiting up to `--wait` seconds, default 60, until the service is ready), picks the
expected set of each served bundle (`expected/<bundle_id>/`, or a set with the same `content_sha256`), sends every
recorded call -- the plan call and every `/schedule` / `/insert` call of the edit chains, with the recorded options
(`lang`, `geometry` as query parameters) and bodies, `X-Request-Id: golden-<scenario>-0` for the plan and `golden-<scenario>-<chain><step>` for edits (e.g. `golden-S01_default-A0`) -- and compares status +
response with the rules above. A clear `WARNING` is printed when `/v1/meta` shows street routing (chain other than
`estimate`) or a `PHOTO_BASE_URL`: those runs report differences by design. Exit 0 = every scenario passed; 1 =
a difference, a missing expected file or a city the service does not serve; 2 = the service is unreachable / not
ready / not the walk-planner.

CI without the real data uses the synthetic **mini set** in `tests/fixtures/mini/` (a 60-place city with its own
sources, bundle, scenarios and expected outputs; `tests/test_golden.py` runs it):

```bash
$PY -m walk_planner golden run --bundle tests/fixtures/mini/bundles \
    --scenarios tests/fixtures/mini/scenarios.json --expected-root tests/fixtures/mini/expected
```

## Updating

A change of any expected output is a change of the API contract or of the plans. Per `walk_planner/version.py`:
MINOR (intended change of plans) or MAJOR (API break) — never a PATCH.

1. Run `golden run -v` and review every difference.
2. Bump `ALGORITHM_VERSION`, then `golden update` (writes `expected/<bundle_id>/`, updates `index.json`; the table
   shows NEW / CHANGED / SAME per scenario). `--only` updates a subset and keeps the other index entries.
3. Commit the expected files together with the code change and a CHANGELOG entry.

A **new data bundle** (new catalog, embeddings or photos) gets its own `expected/<new_bundle_id>/` from
`golden update`; keep the old set while the old bundle is deployed. The mini set is regenerated the same way
(`tests/fixtures/mini/README.md`).

**A rebuilt bundle of the same data** (same sources, new builder or platform) must change nothing but float noise.
Prove it before replacing the expected set: write the new outputs to a scratch root, compare, then update.

```bash
$PY -m walk_planner golden update --bundle <new bundle> --expected-root /tmp/expected_new
$PY golden/tools/compare_expected_sets.py expected/<old id> /tmp/expected_new/<new id> \
    --bundles <old bundle> <new bundle>                  # exit 0 = float noise only
$PY -m walk_planner golden update --bundle <new bundle>  # then retire expected/<old id>
```

`compare_expected_sets.py` puts every differing value into a class: `identity` (plan_id, versions.catalog,
request.catalog_version), `float` (numbers within 1e-6), `clock_half_minute`, `coordinate_6dp` (a navigation URL
whose coordinate, printed with 6 decimals, moved by one unit because it sits on a half-way point), `exact_tie`
(only with `--bundles`: a variant whose stops are the same places and kinds in another order where both orders take
the same time on both bundles, re-timed with `pipeline.schedule`) or `SUBSTANTIVE` (must be 0).

## Bundle rebuild 2026-10-02: `bucharest-20261002-49fc956c` -> `bucharest-20261002-d68a311e`

**Why.** The bundle builder now parses every CSV with `float_precision="round_trip"` (`bundle.CSV_READ_OPTIONS`;
Python's correctly rounded `float()`). pandas' default C parser reads some 17-digit values one ulp off, and
differently on macOS and glibc: the catalog CSV stores 8,055 coordinates as 17-digit floats
(`44.478407499999996`), and the default parser read 1,787 latitudes and 2,631 longitudes one ulp away from them on
macOS (by <= 7.1e-15; on Linux 1,942 / 1,695 others), so a bundle built on Linux got another `bundle_id` than the
same data built on macOS. The catalog rows hash changed (`catalog_sha256` 98baa662... -> e667fd9c...); the taste
artifacts did not (fingerprint c6dd60e9... both: they use no coordinates). The old bundle is kept in
`data/walk_bundles/`; its expected set was retired from this directory (regenerate it any time with
`golden update --bundle <old bundle> --expected-root <dir>`: the code is golden-identical).

**Proof** (`compare_expected_sets.py expected/bucharest-20261002-49fc956c <new set> --bundles <old> <new>`, on the
26 scenarios, 99,642 values):

| class | values | what |
| --- | ---: | --- |
| identity | 228 | plan_id, versions.catalog, request.catalog_version (the bundle id) |
| float | 7,077 | largest difference 3.4e-11 (minutes, km, coordinates) |
| coordinate_6dp | 258 | navigation URLs only: a coordinate exactly on a half-way point, e.g. boteca13's longitude `26.098287499999998` -> `26.098287` (the default parser read `26.0982875` -> `26.098288`) |
| exact_tie | 366 | S19 variants 2 and 3 only, see below |
| clock_half_minute | 0 | |
| SUBSTANTIVE | 0 | |

Stop sequences (places and order) of every variant and every edit call are identical in 25 of 26 scenarios. In
**S19_must_closed variants 2 and 3** the loop runs the other way round: the same 6 places with the same kinds
(Ryan's Pub pinned and flagged temporarily closed, the slot, the on-the-way stops), only reversed. Both directions
take exactly the same time, on both bundles (re-timed with `pipeline.schedule`): variant 2 223.12317779318147 min
(old bundle) / 223.12317779318516 min (new bundle) either way; variant 3 219.0428374306698 / 219.04283743066983
(old) and 219.0428374306834 both ways (new). The solver met an exact tie between a loop and its reverse, and the
last bits of the coordinates decided which one it kept -- the same algorithm on inputs one ulp apart. This is the
only deviation from "routes, stops, order identical"; texts differ only where they follow that order (stop
numbers, stop_index of the stop messages). `tests/test_golden.py` checks the new set against `baseline_v0` like the
old one (navigation links: equal or one unit in the 6th decimal of a coordinate).

The research dashboard still loads the CSV with pandas' default parser (research code), so for this data it may
show such a tie the other way round, and a navigation-link coordinate one unit apart, compared with the service.

## Environment

Outputs are deterministic for given data and versions. The expected set was generated on macOS (arm64) with
Python 3.13, numpy 2.4.6, pandas 3.0.3, pyarrow 24.0.0 and scikit-learn 1.8.0 (recorded in `index.json`) — the
numpy / pandas / pyarrow versions `deploy/requirements.lock` pins for the service image. Other stacks and other
libms (glibc on Linux: `math.asin(0.3)` differs in the last bit) differ only by last-bit float noise, which the
rules above absorb: `golden run` passes all 26 scenarios on Python 3.12 with numpy 1.26.4, pandas 2.2.3 and
scikit-learn 1.6.1 as well, and the service image (Linux) passes `golden run --url`. Tests compare golden JSON
with the same comparator, never with `==`. Message texts embed rounded clock labels (`"arrival_label": "09:03"`)
that could in principle flip the same way at an exact half minute; no scenario does.
