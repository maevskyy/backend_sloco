# Walk Planner golden baseline v0

This is the ground truth for the Walk Planner refactor, which moves the planner logic out of the
Streamlit dashboard into a package plus a microservice. Each `<scenario_id>.json` holds what the
current code (git `f1b2196`) produces for one scenario in `../scenarios.json`:

- the candidates handed to the planner;
- every route variant, with its plan, its rendered texts and its navigation links;
- the route-editor chains.

The new code must reproduce these files. The only allowed differences are the documented bug fixes
listed below. Each fix must change exactly the fields named here and nothing else.

## How it was produced

```
cd sloco_recommendation_system
PYTHONDONTWRITEBYTECODE=1 ../../venv/bin/python services/walk_planner/golden/tools/capture_baseline.py
#   [--out DIR] [--only S01_default,...]   (_meta.json is written on full runs only; ~19 s for all 24)
```

The harness runs the dashboard's own code with no browser and no Streamlit session. Streamlit is
imported but never started.

- **Planning.** `dashboard_app._walk_build` is called the way `page_walk_planner` calls it:
  `rec=None`, `ctx=None`, `seed_ids=[]` and `has_hours=True`. With no seeds, interest comes from
  the cold-start popularity score; there is no personalization.
- **Catalog.** The catalog is `pd.read_csv(locations_bucharest_all.csv)` with
  `place_id.astype(str)`. This was checked against `LocationRecommender.from_artifacts(...).locations`:
  the two are identical in every column the planner reads, and in row order.
- **City rows and center.** These are built verbatim from the page:
  - drop rows with no latitude/longitude;
  - set `wp_hours = opening_hours.map(_walk_parse_hours)`;
  - set `center` to the mean latitude/longitude of the city rows.
- **Form values.** API codes are mapped to the dashboard's labels (`sight`→`Достопримечательность`,
  and so on).
  - `end_abs` is the first value in the "Закончить к" options `range(a+15, a+1441, 15)` that matches
    the end clock time and `end_day_offset`.
  - `t0 = weekday*1440 + start minutes`.
- **Edits.** The editor's own callbacks `_walk_remove_place` and `_walk_add_place` are called, with
  `st.session_state` replaced by a dict. Drag-and-drop moves, restores and the minutes editor are
  replicated line by line from `_walk_editor`.
  - In the minutes editor, every stop gets `dwell_min=float(int(round(m)))` and `dwell_fixed=True`.
  - After each edit, the plan is rebuilt with `plan_sequence(seq, _walk_seq_request(...))`.
- **Rendering.** What `_walk_render_result` would show is rebuilt without Streamlit, using the
  dashboard's own helpers:
  - the metrics, the variant caption, captions, warnings and stop cards;
  - card text has the markdown emphasis removed and is split into lines.

  `navigation` is `walk_planner.navigation_links(plan, start, shape, place_ids=gpid)`, where `gpid`
  is computed the way the page computes it (Google place ids that start with `ChIJ`).
- **Hermetic run.**
  - `ORS_API_KEY` is set to `""` before the repo is imported, so routing is always the straight-line
    estimate.
  - No `.env` is loaded.
  - Socket connects and DNS lookups raise an error.
  - No bytecode is written, and no repo file was modified.
- **Checks.**
  - **Determinism.** Three runs (default `PYTHONHASHSEED`, `0` and `987654`) produced byte-identical
    digests. Only `_meta.json.captured_at` differs.
  - **Independent spot-check.** S01, S09 and S19, plus S01 chains A, C and D, were recomputed in a
    separate script that calls `_walk_build` and `walk_planner` directly. That script loads the
    catalog differently and uses hand-coded form values. There were 0 mismatches.
  - **Internal consistency.** Chain E (remove stop 0, then restore it at position 0) gives a plan
    identical to the variant's optimal plan.

## What each scenario file contains

| Key | Contents |
| --- | --- |
| `scenario` | The scenario as it appears in `../scenarios.json`. |
| `inputs_resolved` | Resolved inputs: `start`, `center`, `t0`, `budget_min`, `end_abs`, `slot_labels` and related values. Exact `start_exact` and `center_exact` values are stored as repr strings. |
| `error` | The `st.error` text when `_walk_build` returns None. In this baseline it is always null. |
| `notes` | The info and warning notes shown above the result. |
| `search_km`, `area` | The search radius actually used, and the search-circle center. |
| `request` | The `WalkRequest` parameters (extra, for debugging). |
| `candidates.slot` | `[place_id, slot, interest, dwell_min, dwell_fixed, theme, subtype]`, in the order passed to `WalkRequest`. |
| `candidates.extra` | `[place_id, slot, interest, dwell_min, activity label]`. The label is the activity the extra was fetched for. |
| `candidates.must` | Must-visit rows: slot-row fields plus `pinned`. |
| `variants[]` | One entry per variant: `dropped` (Russian slot labels), `plan`, `render` (for that variant selected, `manual=False`) and `navigation`. |
| `edits.<chain>` | `ops` (indices resolved, plus `place_id` and `applied`), `manual`, `removed`, `plan`, `render` (`manual=True`, so the extras caption and the dropped-slots warning are suppressed, as in the dashboard) and `navigation`. |

Notes on the plan and render fields:

- `plan.stops[].hours_label_ru` is `_walk_hours_label(hours, t0 + arrival_min + wait_min)`.
- `plan.segments[].n_geom` is `len(geometry)`. With the straight-line estimate it is always 2.
- `render.infos` is non-empty only for an empty route.
- `render.variants_caption` is the "Вариант 1: … · Вариант 2: …" caption shown when there is more
  than one variant.

### Scenario interpretation

These are decisions I made where the spec was ambiguous:

- **"As S01" includes S01's edit chains.** I read "as S01" literally for S02 (chill), S03 (scenic),
  S04 (one way), S06 (fill off) and S24 (top-K 20). Each of these carries S01's five chains (A–E).
  S05 replaces them with its own chain A (move 1 to 0). S12 has its own chain A (add Origo).
  That gives 32 chains in total.
- **Edit op format.** Ops are written as:
  - `move {from,to}`, where a negative index counts from the end;
  - `remove {index}`;
  - `add {place_id}`;
  - `set_dwell {index,dwell_min}`;
  - `restore {removed_index,to}`.

  Each chain starts from a fresh copy of variant 0.
- **Default values.** Any value a scenario does not specify comes from S01: Saturday 2026-10-03,
  10:00–14:00, loop, starting at the city center, style max, slots sight/coffee/park/food, radius
  2.5 km, 3 variants, fill on, known-hours-only off, top-K 8. "Sat", "Sun" and "Mon" mean 2026-10-03,
  2026-10-04 and 2026-10-05.

### Place ids looked up (exact catalog name match unless noted)

| Requested | Catalog row used | place_id | Note |
| --- | --- | --- | --- |
| Stavropoleos Monastery Church | `The Church of the "Stavropoleos" Monastery` (historical_landmark, 5781 reviews) | 10915586233752676659 | No exact match, so I used the famous church. I rejected "The crosses from the Stavropoleos Monastery" (12 reviews). |
| Origo | `Origo` (cafe, 4846 reviews) | 14169121335398956031 | Ambiguous: there are 2 exact matches. I took the one with the most reviews; the other is 13436942836921118737 (229 reviews). |
| National Museum of Art of Romania | `National Museum of Art` (art_museum, 10235 reviews) | 16044012576954065712 | This is the catalog's name for the museum. |
| Mosto - Natural Wine Bar & Bistró | same (wine_bar, 586 reviews) | 104526001739669038 | Exact match. |
| La Mama (closed_forever) | `La Mama` (romanian_restaurant, 6383 reviews, closed_forever, hours unknown) | 14018219728270213257 | The id was given in the spec. Four rows are named "La Mama"; this one has the most reviews. |
| Ryan's Pub (temporarily_closed) | `Ryan's Pub` (pub, 1902 reviews, temporarily_closed, hours unknown) | 10366085341954844986 | The id was given in the spec. |

## Known pre-fix behaviours visible in this baseline

These two bugs are scheduled to be fixed. The fix must change exactly these fields.

1. **Bug 1 – `Segment.from_order` / `to_order` are off by one for shape `free`.**
   - **Cause.** `_assemble_plan` labels legs as if a start anchor existed.
   - **Where to see it.** In every S05 variant and in S05 chain A, segments read
     `(-1,0),(0,1),…,(n-3,n-2)` instead of `(0,1),…,(n-2,n-1)`.
   - **Scope.** No other field is affected, including times, distances and navigation.
2. **Bug 2 – closed must-visits are accepted silently.** Places with `business_status` of
   `closed_forever` or `temporarily_closed` are accepted as must-visits without any message.
   - **Cause.** Slot candidates exclude these statuses, but `_walk_build` builds must-visits with
     `_walk_row_candidate` and never checks the status.
   - **Where to see it.** In S19, La Mama (closed_forever) and Ryan's Pub (temporarily_closed) are
     pinned stops in all 3 variants. Both have unknown hours, so `hours_ok` is null, the label reads
     "часы работы неизвестны", and there is no warning.
   - **Gap.** The same check is also missing on the editor's "➕ Добавить место" path. No scenario
     here exercises it.

### Other current behaviours captured as-is

The new code must match these unless a change is deliberate and documented:

- **S08 variant 2 (`variants[1]`).** A loop whose return leg is 46.1 min disables the fill.
  - The slot solver does not cap the walk back to the start against `max_leg_min=45`.
  - The strict `_route_minutes` check used by `_fill_extras` does apply that cap, returns None, and
    the fill is skipped.
  - The result is 3 stops in a 24 h window, with 1236 min to spare.
- **S08, all variants.** "Кофе" is dropped.
  - The slot order is fixed and the departure is 00:00.
  - Every coffee candidate opens at 08:00 or later, which is beyond the 30-min maximum wait.
- **S17 (must-visits only).** The National Museum of Art is visited at 09:03 even though it opens at
  11:00.
  - `best_insertion` falls back to a non-strict position, so the stop gets `hours_ok=false` and a
    warning.
  - That closed stop makes the strict route check return None, so no extras are added.
  - All 3 variants come out identical, so 1 is kept, with the "Разных маршрутов получилось 1 из 3"
    note.
- **S18 (must-visit Mosto, which opens at 18:30).** In every variant Mosto is placed at around
  09:06–09:51, while it is closed.
  - The slots are solved first; with fill off, the route finishes at about 13:14–13:52.
  - No insertion position passes the strict check, so `best_insertion` takes the shortest position
    and ignores hours.
- **S10 (15-min window).** Nothing fits, so `plan_walk` falls back to the best plan without a budget.
  All 3 variants are over budget, with the over-budget warning.
- **S11 (Sunday 05:30).** "Кофе" is dropped, because the earliest coffee opening is 07:30.
- **Manual edits are scheduled without the solver's constraints.** S01 chain A:
  - The National Museum of Art is moved first and waits 57 min, which is allowed:
    `_assemble_plan` permits waits up to `max_wait_min + 60 = 90`.
  - St. Nicholas gets `hours_ok=false`.
  - Edits and added places can exceed the budget; this shows as the over-budget warning in chains A,
    C and D.
- **Minutes-editor rounding.** In S02 chain D, a dwell of 97.5 becomes 98.0. The editor uses
  `int(round())` (round half to even) on every stop, and the edited stop becomes 120. All stops become
  `dwell_fixed=True`.
- **Keyword matching for food/drink slots.** These slots match keywords as plain substrings of
  `primary_type + ai_place_type_summary + name`. For example, `bar` also matches `barbecue`.
- **Unknown hours.** A missing `opening_hours` value means always open: `hours_ok` is null and the
  label reads "часы работы неизвестны".

## Comparing a new implementation against this baseline

- **Inputs.** Feed `inputs_resolved.center_exact` / `start_exact`, not the 6-decimal `center` /
  `start`. A rounded center shifts walk minutes in the 4th decimal. The exact center is
  `mean(latitude), mean(longitude)` of the 12961 Bucharest rows: (44.4402865461307,
  26.097708464099995).
- **Numbers and strings.** Floats are rounded to 6 decimals; compare them with an absolute tolerance
  of about 2e-6. Compare strings exactly. Render strings embed the dashboard's own rounding:
  - `_walk_clock` uses `round()`;
  - metrics use `:.0f` and `:.1f`;
  - both round half to even.
- **Tie-breaking.** `_walk_slot_candidates` ranks with
  `sub.sort_values("wp_interest", ascending=False)`. That is pandas' default quicksort, which is not
  stable. It then uses `.head(top_k)`, `nsmallest(top_k, "_c")` (keep first) and
  `drop_duplicates`-style keep-first, so ties depend on the sort algorithm and the catalog row order.
  I ran two checks:
  - forcing a stable sort changed 14 of 24 scenarios;
  - shuffling the catalog rows (3 seeds, center held fixed) changed 10, 14 and 11 of 24.

  In every case, only `candidates.slot` / `candidates.extra` changed, and only among rows with tied
  interest. Every affected row had interest below 0.3, which is `EXTRA_MIN_INTEREST`, so plans,
  renders, navigation and edits stayed byte-identical. Compare tied-interest candidate rows as sets,
  or keep pandas plus the CSV row order.
- **Environment.** Python 3.13.3, numpy 2.4.6, pandas 3.0.3 (the new `str` dtype), streamlit 1.58.0,
  macOS arm64. The catalog sha256 is in `_meta.json`; if the CSV changes, this baseline is void.

## Re-running `tools/capture_baseline.py` after the dashboard switch-over

`capture_baseline.py` drives the dashboard's own Walk Planner functions (`_walk_build`,
`_walk_slot_candidates`, `_walk_row_candidate`, `_walk_add_place`, `_walk_remove_place`,
`_walk_seq_request`, `_walk_clock`, `_walk_end_label`, `_walk_hours_label`, `_walk_parse_hours`,
`WALK_ACTIVITY_TYPES` and the `wp_*` aliases). Since the dashboard page was switched to the
package, those functions are gone from `dashboard_app.py`: the page is UI glue only, and the logic
lives in `walk_planner.{slots,catalog,candidates,interest,pipeline,present}`. On the current tree the
tool therefore stops with `AttributeError: ... has no attribute 'WALK_ACTIVITY_TYPES'`.

It needs the **pre-refactor checkout (git `f1b2196`)**. One way that leaves the repo untouched:

```
cd sloco_recommendation_system
T=/tmp/wp_f1b2196 && mkdir -p $T && git archive f1b2196 | tar -x -C $T
mkdir -p $T/services/walk_planner/golden && cp -R services/walk_planner/golden/{scenarios.json,tools} $T/services/walk_planner/golden/
ln -s "$PWD/recommendation_system/ai_location_recommender/data" $T/recommendation_system/ai_location_recommender/data   # gitignored data
PYTHONPATH=$T/recommendation_system/ai_location_recommender PYTHONDONTWRITEBYTECODE=1 \
    ../../venv/bin/python $T/services/walk_planner/golden/tools/capture_baseline.py --out /tmp/baseline_rerun
```

(`PYTHONPATH` makes the old single-module `walk_planner.py` importable as top-level `walk_planner`,
which the tool imports.) Checked on 2026-10-02: S01 and S19 come out byte-identical to the files here.

To compare the CURRENT code with this baseline, use `tools/parity_check.py` (the package pipeline);
the dashboard page itself is checked against it by `test/test_dashboard_walk.py` (its glue functions
for every scenario and edit chain, plus a `streamlit.testing` run of the page).
