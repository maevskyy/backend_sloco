# Mini golden fixture ("Minitown")

A synthetic 60-place city that exercises the whole data path without the real Bucharest data, so CI can run
the bundle builder, the bundle checks and the golden machinery (`tests/test_bundle.py`, `tests/test_cli.py`,
`tests/test_golden.py`).

| Path | What |
| --- | --- |
| `make_mini.py` | Generator of `source/` and `scenarios.json` (pure-arithmetic PRNG: byte-identical output on every platform) |
| `source/` | Catalog CSV, photo manifest, text store (8-d, rows in reverse order, one place without a vector) and image store (4-d, 80 % of the places + one foreign place) with their metadata |
| `scenarios.json` | 8 scenarios: loop with the five S01-style edit chains, one-way scenic from a point, free / chill, must-visits with closed places (closed_forever, temporarily_closed, the `CLOSED_PERMANENTLY` alias, an unknown id) and refused / accepted adds, favourites + want-to-go, night bars from a catalog place, all eight slots, and a 422 request |
| `bundles/<bundle_id>/` | The bundle built from `source/` (committed) |
| `expected/<bundle_id>/` | Its golden expected API outputs (committed) |

The city has opening hours (unknown, 24 h, past midnight), a place without coordinates (the planner ignores it;
validation warns), 19-20 digit CIDs (half above the int64 maximum), photos for most places.

The bundle builder parses CSVs with `float_precision="round_trip"` (platform-independent; see `golden/README.md`,
"Bundle rebuild 2026-10-02"). The mini CSVs parse to the same floats either way, so that change left this bundle
as it was (same catalog hash and bundle id; `tests/test_bundle.py` rebuilds it and compares).

## Regenerate (from `services/walk_planner`)

Only when the synthetic data or the outputs must change (the outputs change with the algorithm / API — see
`golden/README.md`, "Updating"):

```bash
PY=/path/to/venv/bin/python
M=tests/fixtures/mini
$PY $M/make_mini.py                                       # source/ + scenarios.json
rm -rf $M/bundles $M/expected                             # a new build gets a new bundle id (date + content)
$PY -m walk_planner bundle build --out-root $M/bundles --city-slug minitown --timezone Europe/Bucharest \
    --catalog-csv $M/source/locations_minitown.csv --photo-manifest-csv $M/source/photo_manifest_minitown.csv \
    --text-npy $M/source/text_minitown.npy --text-meta-csv $M/source/text_minitown_metadata.csv \
    --image-npy $M/source/image_minitown.npy --image-meta $M/source/image_minitown_metadata.csv
$PY -m walk_planner golden update --bundle $M/bundles --scenarios $M/scenarios.json --expected-root $M/expected
```

Only the expected outputs (same bundle):

```bash
$PY -m walk_planner golden update --bundle $M/bundles --scenarios $M/scenarios.json --expected-root $M/expected
```

The mini scenarios use at most three seeds, so their taste profiles never need scikit-learn.
