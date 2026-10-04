# Walk Planner v1: how the planner works

**Who this is for.** Backend developers who run, debug and tune the `walk-planner` service. This document
explains the algorithm as the code executes it. It is not a specification to reimplement: the package is the
single source of truth (the research dashboard and the service both call it). Every rule cites its code as
`module:function`, with modules under `walk_planner/`.

**Versions covered.** `ALGORITHM_VERSION` 1.0.0, `INTEREST_VERSION` walk_interest_v1 and
`BUNDLE_SCHEMA_VERSION` 1 (`walk_planner/version.py`). The examples use the Bucharest bundle
`bucharest-20261002-d68a311e` (12,961 places) and the golden scenarios in `golden/scenarios.json` (`S01`…`S24`,
`P01`, `P02`). Unless a section says otherwise, routing is forced to the straight-line estimate, as in the golden
outputs. Every number in the examples was re-run on the current code.

**What v1 is.** v1 is the research dashboard's planner (git `f1b2196`) moved into this package without
changes, plus five v1 changes:

1. Segment indices are fixed (BUG 1).
2. Business status is enforced for places the user chooses (BUG 2).
3. A production routing chain with quality flags.
4. Personalization by favourites, using an in-package taste model.
5. Validation at the API boundary.

Everything else is deliberate v1 behaviour, and the golden outputs pin it (`golden/README.md`). That includes the
known defects listed in §19. Changing anything in §18 changes plans, which makes it a MINOR release with
regenerated golden outputs.

**Related documents.**
- [HANDOFF.md](HANDOFF.md): start here; [SPEC.md](SPEC.md) is the overview.
- [API.md](API.md) and `openapi.json`: the HTTP API.
- [messages.md](messages.md) and `messages.json`: every message and error code, with RU and EN texts.
- [DATA.md](DATA.md): the data bundle, the catalog and opening-hours data.
- [ROUTING.md](ROUTING.md): the street-routing chain.
- [`golden/README.md`](../golden/README.md): the acceptance gate.

## Contents

0. [In one page](#0-in-one-page)
1. [Vocabulary](#1-vocabulary)
2. [Overview: request to response](#2-overview-request-to-response)
3. [Request, window and search area](#3-request-window-and-search-area)
4. [Interest: where it comes from, where it is used](#4-interest-where-it-comes-from-where-it-is-used)
5. [Candidates](#5-candidates)
6. [The solver](#6-the-solver-coreplan_walk-slots-mode)
7. [Assembly](#7-assembly-core_assemble_plan)
8. [Variants](#8-variants-coreplan_variants)
9. [The API view](#9-the-api-view-presentplan_response)
10. [Editing](#10-editing)
11. [Opening-hours model](#11-opening-hours-model)
12. [Dwell estimation](#12-dwell-estimation)
13. [Personalization](#13-personalization)
14. [Routing](#14-routing)
15. [Determinism](#15-determinism)
16. [Complexity and measured performance](#16-complexity-and-measured-performance)
17. [Debugging recipes](#17-debugging-recipes)
18. [Tuning: the constants](#18-tuning-the-constants)
19. [Known limitations and defects of v1](#19-known-limitations-and-defects-of-v1)
20. [Ideas for v1.1+](#20-ideas-for-v11)

---

## 0. In one page

- **Problem.** This is a small Tourist Trip Design (orienteering) problem. An ordered list of activity *slots*
  (for example sight, then coffee, then park, then food) gets one place per slot inside a time window. Every
  visit must fit the place's opening hours. Spare time is filled with optional *on-the-way* stops.
- **Pipeline.** The stages run in this order (§2):
  1. Validate the request; compute the window and clock.
  2. Narrow the search radius.
  3. Compute interest.
  4. Collect candidates: per-slot candidates, must-visits and the on-the-way pool.
  5. Run a beam search over the slots.
  6. Insert must-visits; fill the window greedily.
  7. Assemble the plan with street legs.
  8. Repeat for up to 5 variants, then build the API view.
- **What decides places and order.** Only three inputs: the catalog bundle, the interest map, and a
  straight-line travel estimate (18 minutes per straight-line km). The street router is called once per variant,
  after planning. It changes times, geometry and flags, never the places or their order (§14).
- **Heuristics, not an exact optimizer.** A 256-state beam picks the slot places. Greedy insertion places
  must-visits and on-the-way stops. A 4-hour request takes 0.3–0.45 s in process; a 24-hour one takes about
  3 s (§16).
- **Deterministic.** There is no randomness. Ties go to input order, which comes from the catalog row order
  (§15).
- **Known defects are kept on purpose in v1** (§19). Examples:
  - a must-visit is inserted after the slots and can land at a time when the place is closed (D4, WP-15);
  - on-the-way stops have no cap on detour or count (WP-24, WP-25);
  - meals have no time windows (WP-12).

## 1. Vocabulary

| Term | Meaning | Code |
|---|---|---|
| activity, slot | An activity type (`sight, coffee, food, bar, park, market, entertainment, shopping`). A request is an ordered list of slots, each activity at most once. | `slots:ACTIVITY_TYPES`, `pipeline:SlotSpec` |
| slot stop | A place that fills a requested slot. | `Candidate.slot` in `[0, n_slots)` |
| on-the-way stop (*extra*) | An optional short stop that uses spare time. Its API kind is `on_the_way`. | `Candidate.extra` |
| pinned stop | The user's own place: a must-visit at build time, or a place added in the editor. | `Candidate.pinned` |
| window, *B* | Minutes from the departure to the end of the window, 15..1440. | `PlanContext.budget_min` |
| week-minute, *t0* | Minutes since Monday 00:00, city-local wall clock. *t0* is the departure. | `PlanContext.t0` |
| interest | A score in 0..1 per place: popularity, optionally reordered by the user's taste. | §4, §13 |
| dwell | Visit length in minutes. | §12 |
| λ (interest weight) | How many walking minutes one unit of interest is worth (set per style). | `core:STYLE_PRESETS` |
| *w* (walk weight) | Cost of one walking minute (≤ 1), adapted to how roomy the window is. | `core:_adaptive_walk_weight` |
| estimate | Straight line × 1.35 at 4.5 km/h. | `core:RoutingProvider` |
| streets | A leg that came from OSRM or ORS. | `routing:ChainProvider` |
| variant | One of up to 5 different routes for one request. | `core:plan_variants` |
| sequence, EditStop | The editable form of a variant, which the client sends back. | `pipeline:EditStop` |

## 2. Overview: request to response

```text
POST /v1/walks/plan (JSON)
   |
   v
(1) validate + normalise ................................ pipeline:normalize_params
   |  codes, limits, defaults; shape "free" => no start                     422 validation_error, unknown_city,
   |  -> PlanParams                                                          invalid_window, start_required, ...
   v
(2) window, clock, start, search area ................... pipeline:make_context
   |  B = window minutes; t0 = weekday*1440 + departure (opening-hours clock)
   |  search_km = min(radius_km, core:reach_radius_km(B, sum of slot base dwells, shape))   -> radius_shrunk
   v
(3) interest map {place_id: 0..1} ....................... pipeline:resolve_interest
   |  no favourites: cold start (popularity)             -> interest:cold_start
   |  favourites: taste port + quantile rank blend        -> interest:interest_map   (failure -> cold start +
   v                                                                                  personalization_unavailable)
(4) slot candidates, per slot ........................... candidates:slot_candidates
   |  membership (theme | keyword + group) -> radius -> precision filters (relaxed if < 3)
   |  -> business status -> known hours -> open_within(window)
   |  -> top-K by interest + K best "near the start"                         -> slots_no_candidates
(5) must-visits ......................................... candidates:place_candidate
   |  closed_forever dropped, temporarily_closed flagged, unknown reported   -> must_visit_closed_forever,
   |  (no slot candidate and no must-visit: status no_candidates)               unknown_place_ids, no_candidates
(6) on-the-way pool ..................................... candidates:extra_candidates
   |  requested non-food types (scenic: park + market), top-30 + 30 near each
   v
   core.WalkRequest(slots mode, candidates, must_visit, start, shape, style, B, t0, provider)
   |
(7) variants, n = 1..5 .................................. core:plan_variants
   |   for each variant: places used by earlier variants get interest x 0.35
   |   core:plan_walk
   |     a. style preset, dwell scaling, adaptive walk weight w
   |     b. beam search over the slots ................... core:_solve_slots
   |     c. must-visit insertion ......................... core:best_insertion
   |     d. fill the window with on-the-way stops ........ core:_fill_extras
   |     e. assembly: ONE route_legs call, times, waits, hours flags ... core:_assemble_plan
   |   drop empty / duplicate routes                                          -> fewer_variants, no_route
   v
(8) view ................................................ present:plan_response
      stop kinds, TimePoints, hours status, segments, messages, navigation links,
      "sequence" + "request" echo for /schedule and /insert
```

| Stage | Code | Produces | Cost (S01) |
|---|---|---|---|
| 1–2 | `pipeline:normalize_params`, `pipeline:make_context` | `PlanParams`, `PlanContext` | < 1 ms |
| 3 | `pipeline:resolve_interest` | `PlanInterest` (map, mode, used/ignored seeds) | 0 ms cold (cached); 20–45 ms with favourites |
| 4–6 | `candidates:*` | candidate lists (S01: 54 slot, 79 pool) | ~0.2 s |
| 7 | `core:plan_variants` | 1..n `WalkPlan` | ~0.1 s (S01) up to ~3 s (24 h) |
| 8 | `present:plan_response` | response JSON | a few ms |

`pipeline:build_plan` runs stages 2–7 and returns `PlanResult(status, messages, variants, interest, candidates,
request)`. The service wraps it in `POST /v1/walks/plan`. A valid request that finds nothing still returns HTTP
200, with `status` `no_candidates` or `no_route`, no variants, and request messages that say why.

## 3. Request, window and search area

### 3.1 Validation (`pipeline:normalize_params`)

Validation changes nothing in the algorithm. It rejects inputs the core would silently misread (defect D13) and
applies the dashboard's defaults.

| Field | Rule | Default | Error |
|---|---|---|---|
| `city` | must equal the bundle's city (case-insensitive) | the catalog's city | `unknown_city` |
| `date` | `YYYY-MM-DD`, city-local | required | `validation_error` |
| `start_time`, `end_time` | `HH:MM` | 10:00; start + 4 h | `validation_error` |
| `end_day_offset` | 0 or 1. When absent: an end at or before the start means the next day | inferred | `validation_error` |
| window | `offset*1440 + end - start`, must be in [15, 1440] | 240 | `invalid_window` |
| `shape` | `loop`, `one_way` or `free` (RU labels accepted) | `loop` | `validation_error` |
| `style` | `max`, `chill` or `scenic` | `max` | `validation_error` |
| `start` | `"city_center"`, `{lat, lon[, place_id]}` or `{place_id}`. Required for `loop` and `one_way`; forced to none for `free` | — | `start_required`, `unknown_place` |
| `slots` | ≤ 8; codes or `{activity, dwell_min}`; each activity at most once; dwell 5..480 | sight, coffee, park, food | `unknown_activity`, `duplicate_activity` |
| `must_visit_place_ids` | ≤ 10 string ids, de-duplicated with order kept | [] | `too_many_must_visits` |
| `favourite_place_ids` + `want_to_go_place_ids` | ≤ 500 together | [] | `validation_error` |
| `personalization_strength` | 0..1 | 0.5 | `validation_error` |
| `variants` | 1..5 | 3 | `validation_error` |
| `radius_km` | 0.3..50 | 2.5 | `validation_error` |
| `fill_window`, `known_hours_only` | booleans | true, false | `validation_error` |
| `top_k` | 1..50, bench and CLI only | 8 | `validation_error` |
| (whole request) | at least one slot or one must-visit | — | `no_slots_or_must_visits` |

- Place ids are JSON strings holding the Google CID in decimal. A CID can exceed 2^53, so JSON numbers are refused.
- Unknown keys are ignored, so the `request` echo of a plan response is itself a valid request.
- v1 keeps the dashboard's one-slot-per-activity rule (WP-13). The core accepts repeated activities, but the
  pipeline looks slots up by activity code: `slot_codes.index(...)` in the on-the-way pools and in edit
  re-hydration.

### 3.2 Window and clock (`pipeline:make_context`)

```text
B        = end_day_offset*1440 + end_min - start_min        window length, 15..1440
end_abs  = start_min + B                                    minutes from 00:00 of the departure day (>= 1440: next day)
t0       = date.weekday()*1440 + start_min                  Monday = 0; the opening-hours clock (§11)
```

Inside a plan, every time is in minutes since departure; the week-minute of a stop is `t0 + offset`. The clock is
the city-local wall clock with no time-zone arithmetic: the request's date and times are read in the city's time
zone (`CityCatalog.timezone`, `Europe/Bucharest`). A DST change inside the window is not modelled.

*Example (S01).* Saturday 2026-10-03, 10:00–14:00: B = 240, weekday 5, t0 = 5·1440 + 600 = 7800.

If the catalog has no opening-hours column (`CityCatalog.has_hours` is false), the core gets
`start_week_min = None` and checks no hours at all. Every stop's hours status is then `not_checked`.

### 3.3 Start and search area

- **`start_resolved`**:
  - `"city_center"` resolves to `CityCatalog.center`, the mean lat/lon of all catalog rows, closed places included;
  - a place resolves to its coordinates;
  - coordinates are used as given;
  - shape `free` has no start.
- **`area`**, the centre of the candidate search, is the start or, without one, the city centre. A `free` walk is
  therefore always searched around the city centre (WP-28).
- **The echo** returns the resolved start as `{lat, lon, place_id}`.

### 3.4 Radius narrowing (`core:reach_radius_km`, applied in `pipeline:make_context`)

```text
dwell_total = sum over slots of (the user's dwell_min, else the activity's BASE dwell)
              # type base, not per place, not style-scaled; must-visits are not counted
walk_left   = max(B - dwell_total, 15)                       # minutes of walking left
reach_km    = walk_left / 60 * 4.5 / 1.35                    # straight-line km
reach_km   /= 2   unless shape == "one_way"                  # a loop comes back; "free" must fit a disc
search_km   = min(radius_km, reach_km)
```

**Example (S01).** Base dwells are 45 + 30 + 40 + 75 = 190, so walk_left = 50 → 2.78 km → 1.39 km for a loop.
The requested 2.5 km shrinks to 1.39 km, and the request gets `radius_shrunk`:
- `reason` is `"reach"`, or `"overpacked"` when dwell_total ≥ B;
- `anchor` is the start or the centre.

**Floor.** When the slots alone overfill the window, the floor applies: 15 minutes of walking gives 0.83 km
one-way, 0.42 km for a loop (S10, a 15-minute window).

**Consequences.**
- Long windows widen the search up to the requested radius. In S07 (10:00–22:00, radius 15 km) the search reaches
  14.7 km, so long plans spread across the city.
- Must-visits do not enter the radius computation.

## 4. Interest: where it comes from, where it is used

`pipeline:resolve_interest` builds one map `place_id -> interest in [0, 1]` per build:

| Request | Map | `personalization.mode` |
|---|---|---|
| No favourite or want-to-go ids | Cold-start popularity, `CityCatalog.cold_interest()` (§13.1) | `popularity` |
| Ids given, taste artifacts loaded, at least one valid seed, strength > 0 | The taste model reorders the cold values within each catalog theme (§13.2–13.5) | `favourites` |
| Ids given, but none is a valid seed, or strength = 0 | Cold start; the invalid ids are listed in `favourites_ignored` | `popularity` |
| Ids given but no artifacts, or any exception | Cold start, plus the request message `personalization_unavailable` (`reason`). A plan never fails because of personalization | `popularity` |

Interest is used in exactly five places:

1. Ranking the top-K slot candidates (§5.1).
2. The "near the start" list, which ranks by walking minutes − 8·interest (§5.1).
3. The beam objective: −λ·interest per stop (§6.2).
4. The on-the-way pool: a place is eligible when interest ≥ 0.3, and its value is interest² (§6.5).
5. The variant penalty: interest × 0.35 for places an earlier variant used (§8).

The taste blend keeps each theme's set of interest values (§13.5). So the constants tuned on the cold start keep
their meaning when personalization is on: λ, the 0.3 eligibility floor, the interest² value and the near-list
weight 8.

## 5. Candidates

### 5.1 Slot candidates (`candidates:slot_candidates`)

`build_plan` calls this once per slot, passing the area, `search_km`, the window `(t0, t0 + B)` and the slot's
user dwell. The steps, in order:

| # | Step | Rule | Constants (`slots.py`) |
|---|---|---|---|
| 1 | Membership | If any city row has one of the activity's catalog themes: `theme in themes` (exact match). Otherwise (the food slots, which have no themes): any keyword as a **substring** of the lowercased `primary_type + ai_place_type_summary + name`. Keyword matches are then gated to `theme_group` = the activity's group (or its legacy group) when the catalog has that group. | `ACTIVITY_TYPES`, `LEGACY_GROUP` |
| 2 | Radius | haversine distance from the area ≤ `search_km` | — |
| 3 | Precision filters | `primary_type` must be in `type_allow` and not in `type_deny`; for `ai_deny` activities the AI place type must not match `NON_VENUE_RE` (tour, agency, rental, school, ...). If fewer than 3 places pass, all three filters are dropped together. The count is taken **before** steps 4–6. | `MIN_FILTERED` = 3, `NATURE_TYPES`, `MARKET_TYPES`, `SIGHT_DENY`, `NON_VENUE_RE` |
| 4 | Business status | drop `closed_forever` and `temporarily_closed` | `CLOSED_STATUS` |
| 5 | Known hours only | if the request asks for it, drop places with unknown hours | — |
| 6 | Hours prefilter | drop places where no visit of the activity's **base** dwell (or the user's dwell) fits anywhere in the window: `core:open_within` (§11.4) | — |
| 7 | Top-K | the K places with the highest interest (`sort_values` descending, `head(K)`) | K = `top_k` = 8 |
| 8 | Near the start | among places with ≥ 20 Google reviews, the K smallest `18·d_km − 8·interest`: straight-line walking minutes minus 8 minutes per unit of interest (`nsmallest`, first on ties) | `NEAR_MIN_REVIEWS` = 20, `NEAR_WEIGHT` = 8 |
| 9 | Merge | the top list, then the near-only places; the first occurrence of a place wins | — |

Each place becomes a `core.Candidate` with these fields:
- `slot` = the slot index;
- `theme` = the activity's dwell key (for example `coffee`; not the catalog theme);
- `subtype` = `primary_type`;
- `interest`;
- `open_hours`;
- `dwell_min` = the user's dwell (with `dwell_fixed`), else the per-place estimate (§12).

A slot therefore gets up to 2K = 16 candidates: the best-known places in the radius, plus nearby alternatives.
The list order is the solver's tie-break order (§15).

*Example (S01, 1.39 km around the city centre).* The sight, coffee, park and food slots get 15, 15, 8 and 16
candidates. The park slot gets only 8 because few nature places lie within 1.39 km.

What this produces, kept in v1 (§19):
- Substring keywords put a Korean BBQ restaurant and a juice bar into the bar slot (S18, WP-02).
- A place right at the start costs `0 − 8·interest` in the near list, so it almost always makes the list (WP-06).
- There is no rating floor (WP-03).

A slot with no candidate produces the request message `slots_no_candidates` (activities and indices). Such slots
are absent from the solver and from `dropped_slot_indices`.

### 5.2 Must-visit places (`candidates:place_candidate`, the v1 status policy)

For each id in `must_visit_place_ids`, in order:

| Catalog fact | Outcome | Message |
|---|---|---|
| unknown place | skipped | request `unknown_place_ids` |
| `closed_forever` | skipped; never routable | request `must_visit_closed_forever` (ids and names) |
| `temporarily_closed` | accepted and flagged | stop `place_temporarily_closed` in every variant that contains it |
| anything else | accepted | — |

An accepted must-visit is `candidates:row_candidate`:
- slot −1, interest 0, pinned;
- dwell estimated from the place's **own** catalog theme (§12), not style-scaled;
- hours from the catalog.

`known_hours_only` does not apply to must-visits. When there is no slot candidate and no accepted must-visit, the
build stops with status `no_candidates`. The on-the-way pool is built only after this check.

### 5.3 On-the-way pool (`candidates:extra_candidates`)

The pool is built only when `fill_window` is on. Its activities come from `candidates:extra_activities`:

| Style and slots | Pool activities |
|---|---|
| `scenic` | park and market |
| any other style | the requested non-food activities |
| any other style, every requested slot food or drink | sight |

For each pool activity, `slot_candidates(top_k = 30)` runs with the same filters and no user dwell, so the pool
gets up to 60 places per activity: the top 30 by interest plus 30 near the start. Each entry:
- is marked `extra = True`;
- keeps `slot` = the activity's index among the requested slots, or −1;
- if the place is in two pools, keeps the first pool's activity as its label (`activity_of`).

*Example (S01).* 79 pool entries from sight and park; 58 of them have interest ≥ 0.3. The core caps the pool
further (§6.5).

## 6. The solver (`core:plan_walk`, slots mode)

`pipeline:build_plan` builds one `core.WalkRequest`:
- `mode="slots"`;
- `candidates` = slot candidates + the pool;
- `must_visit`, `start`, `shape`, `style`;
- `time_budget_min` = B, `start_week_min` = t0;
- `fill_window` and `provider`.

The core defaults stay in force: `fit_budget=True`, `max_wait_min=30`, `max_leg_min=45`, and an adaptive walk
weight. `core:plan_variants` calls `plan_walk` once per variant. The `auto` mode of the core is never used by v1
(§19, D12).

### 6.1 Preparation

1. **Style preset** (`core:STYLE_PRESETS`):

   | Style | λ | fill | dwell_scale | walk_scale | Intent |
   |---|---|---|---|---|---|
   | `max` | 8 | 0.95 | 1.0 | 1.0 | pack the window |
   | `chill` | 16 | 0.75 | 1.3 | 1.0 | fewer, longer visits; interest weighs more |
   | `scenic` | 12 | 0.90 | 1.0 | 0.5 | walking is cheap; the pool is parks and squares |

   `max_stops` (chill: 4) only applies in `auto` mode, so in v1 it has no effect.
2. **Dwell scaling.**
   - On-the-way stops: `min(dwell, 10)`.
   - User-fixed dwell: unchanged.
   - Every other slot candidate: `× dwell_scale`. For chill that is × 1.3, food included.
   - Must-visits are not scaled (D6).
3. **Grouping.** Slot candidates are grouped by slot (`core:_group_by_slot`). Slots without candidates are
   skipped.
4. **Adaptive walk weight** (`core:_adaptive_walk_weight`):

   ```text
   need = sum over non-empty slots of (shortest scaled dwell in the slot) + 15 * (number of non-empty slots)
   w    = min(1, max(0.05, (need / B)^2)) * walk_scale
   ```

   A tight window keeps w = 1, so the route stays near the start. A roomy window makes walking cheap, so better
   places further out can win.

   *Example (S01).* The shortest dwells are 10 + 30 + 10 + 75 = 125, so need = 185 and w = (185/240)² = 0.594.
5. **Must-visit reservation.** `must_min = sum over must-visits of (dwell + 10)`. The slots get the budget
   `B' = max(B − must_min, 0)` (`MUST_VISIT_WALK_MIN` = 10).

### 6.2 Beam search over the slots (`core:_solve_slots`)

A state is `(cost, chosen stops, used place ids, elapsed minutes)`; the search starts from `(0, [], {}, 0)`.
There is one layer per non-empty slot, in the requested order. For each state, every candidate `c` of the layer
that is not yet used is tried:

```text
prev  = the start for the first stop (if there is a start), else the last chosen stop, else none (shape free)
step  = walk(prev, c)                       # estimate minutes; 0 without prev
prune if step > 45                          # max_leg_min: legs INTO stops only (not a loop's return, D3)
wait  = visit_wait(c.hours, t0 + elapsed + step, c.dwell, 30)    # prune if None: closed then (§11.3)
t     = elapsed + step + wait + c.dwell
prune if t + back(c) > B' + 1e-6            # back(c) = walk(c, start) for a loop, else 0
child = (cost + w*step + wait - λ*c.interest,  chosen + [c],  used + {c},  t)
```

With a budget (always, except in the fallback of §6.3), each state also gets a **skip child** at
`cost + 10,000` (`SKIP_SLOT_PENALTY`), which leaves the slot empty. After each layer, all children are sorted by cost (a stable sort) and the 256 cheapest are kept (48 without a
budget). After the last layer, a loop adds `w·walk(last, start)` to every non-empty state. The cheapest state
wins.

```text
minimise   sum over stops of [ w*walk_in + wait - λ*interest ]  +  10,000*(skipped slots)  +  [loop] w*walk_back
subject to every prefix plus the walk back fits B';  each leg into a stop <= 45 min;  wait <= 30 min;
           every place at most once
```

How to read the objective:
- **Interest against walking.** One unit of interest is worth λ/w walking minutes. In S01 (λ 8, w 0.594), a place
  0.3 more interesting may cost up to 0.3·8/0.594 ≈ 4 more minutes of walking.
- **Waiting.** Waiting costs 1 per minute, more than walking whenever w < 1. The solver never adds idle time
  beyond a 30-minute wait, so stops are packed from the departure onwards (WP-14).
- **Dwell.** Dwell is not in the cost. It only consumes the budget.
- **Skipping.** Skipping a slot is so expensive that a plan filling more slots always wins, as long as the beam
  still holds it.

*Worked example (S01, variant 1).* The solver chose these four slot stops, with w = 0.594 and λ = 8:

| Stop (slot) | Walk in | Wait | Dwell | Interest | Δcost = w·walk + wait − 8·interest | Cost | Elapsed |
|---|---|---|---|---|---|---|---|
| "Theodor Aman" Museum (sight) | 0.60 | 0 | 60 | 0.614 | 0.36 − 4.91 = −4.55 | −4.55 | 60.6 |
| boteca13 (coffee) | 1.33 | 0 | 30 | 0.662 | 0.79 − 5.30 = −4.50 | −9.06 | 91.9 |
| Piața Revoluției (park) | 2.42 | 0 | 10 | 0.415 | 1.44 − 3.32 = −1.89 | −10.94 | 104.4 |
| Stadio Atrium (food) | 3.67 | 0 | 75 | 0.787 | 2.18 − 6.30 = −4.12 | −15.06 | 183.0 |
| return to the start | 6.76 | | | | +4.02 | **−11.04** | 189.8 ≤ 240 |

**The beam is heuristic** (D7). A synthetic counterexample exists. On real Bucharest requests, a 20,000-state beam
chose the same routes in 8 of 8 tested scenarios, at 0.1–3.3 s instead of 20–56 ms (research measurement).

### 6.3 When nothing fits

If the beam returns no stop and there is no must-visit, the solver runs again without the budget and without the
45-minute leg cap. Opening hours are still enforced, and the beam width is 48. Assembly then flags the result
`over_budget`.

In this mode a state that cannot be extended skips the slot for free, with no penalty, so the solver can prefer
fewer stops (D14). If even this finds nothing, the variant is empty. When every variant is empty, the status is
`no_route`.

*Example (S10, 12:00–12:15, sight + coffee).* No single stop fits 15 minutes. All three variants are full
2-stop plans of 59–66 minutes, each with `over_budget` (WP-18).

### 6.4 Must-visit insertion (`core:best_insertion`)

Each must-visit is processed in request order:

1. If the solver already picked the same place for a slot, it is only marked pinned; it then fills that slot.
2. Otherwise it is inserted at the position with the **shortest total route time** (`core:_route_minutes`, waits
   included), searched in two passes:
   - **Strict pass:** only positions where every stop is still open on arrival (wait ≤ 30) and no leg, a loop's
     return included, exceeds 45 minutes.
   - **Non-strict pass:** if no position passes, the shortest position ignoring hours and the leg cap.
3. On equal times, the earliest position wins.

Insertion never checks the window (D5). A must-visit placed in the non-strict pass makes every later strict check
fail (D4):
- no on-the-way stops are added;
- later must-visits are also placed ignoring hours.

See S17 and S18 in §19.

### 6.5 Filling the window (`core:_fill_extras`)

The fill runs when `fill_window` is on, the pool is not empty and the route has at least one stop. It also needs
`fit_budget`, which is always on in v1. The target is `T = fill · B`; for max and a 240-minute window that is 228.

```text
pool  = extras sorted by interest (descending, stable); stop at the first one below 0.3;
        skip places already in the route; de-duplicate; keep at most 80
total = _route_minutes(route)        # strict; None (a closed stop, a long leg) => NO extras at all
while pool and total < T:
    for each c in pool with total + c.dwell <= T, for each insert position i:
        t = _route_minutes(route with c inserted at i)       # strict: hours, legs <= 45, the return leg
        skip if t is None or t > T
        score = (c.interest^2 + 0.01) / max(c.dwell + w*(t - total - c.dwell), 1)
    insert the best (the first maximum, in pool order then position order); total = t
```

The score is value per added minute:
- **Value.** interest² favours real highlights over merely good places next door.
- **Cost.** The 10-minute look, plus the extra walking (and any change in later waits) weighted by w.

Slot stops keep their order. Every insertion re-checks the hours of all later stops.

*Worked example (S01, variant 1).* The slot route takes 189.8 min (§6.2), and the pool holds 56 entries:

| Round | Inserted (interest) | Position | Route minutes | Extra walk | Value | Cost | Score |
|---|---|---|---|---|---|---|---|
| 1 | National Museum of Art (0.750) | after Stadio Atrium, before the return | 189.8 → 202.9 | 3.09 | 0.573 | 11.84 | 0.0484 |
| 2 | St. Nicholas in-a-Day Church (0.681) | between Piața Revoluției and Stadio Atrium | 202.9 → 214.4 | 1.48 | 0.474 | 10.88 | 0.0436 |
| 3 | Kretzulescu Church (0.640) | between Stadio Atrium and National Museum of Art | 214.4 → 224.7 | 0.39 | 0.420 | 10.23 | 0.0411 |
| — | stop: 224.7 + 10 > 228 | | | | | | |

The result is 7 stops (4 slots + 3 on the way); the walk returns at 13:45 with 15.3 minutes of slack.

Variant 2 of S01 gets no on-the-way stops: its slot route already takes 234 minutes, which is more than T = 228.

The fill has no cap on detour, count or type mix (WP-24, WP-25), and it may put stops before the first slot.

### 6.6 Dropped slots

`plan.dropped_slots` lists the slots that had candidates but got no stop from the beam. It is computed before
must-visits and on-the-way stops are added. It becomes the variant message `slots_dropped`, shown only while the
variant is unedited.

The message always says "did not fit the window", even when opening hours are the real cause. S08 is an example:
the coffee slot is dropped at night, when cafés are closed (WP-42).

## 7. Assembly (`core:_assemble_plan`)

**Points.** The start (unless the shape is `free` or there is no start), then the stops, then the start again for
a loop. An empty route has no points: no router call and no phantom segment (v1 fix).

**Legs.** Exactly **one** `provider.route_legs(points)` call per assembled plan. It returns one leg per
consecutive pair: geometry, duration, distance, `quality` and `provider` (§14).

**Segments.** `from_order` and `to_order` are the stop indices of the leg's two ends, −1 for the start. v1 counts
them over the actual point list, which is correct for `free` too (BUG 1, WP-50).

**Clock.** For each stop:

```text
arrival   = previous departure + the leg's duration          # the router's duration when the leg is "streets"
wait      = visit_wait(hours, t0 + arrival, dwell, 30 + 60)  # known hours and a clock only
hours_ok  = wait is not None                                  # None when hours are unknown or there is no clock
departure = arrival + (wait or 0) + dwell
```

**Totals.**
- `total_walk` is the sum of the legs, the return included.
- `total_time = walk + dwell + wait`, which equals the final clock.
- `total_distance_km` is the sum of the leg distances.
- `over_budget = total_time > B + 1e-6`.
- `routing_quality` is `streets`, `estimate`, `mixed` or `none`.
- `note` is an English over-budget string that the API does not use.

**Assembly with the estimate.** Assembly reproduces the solver's and the fill's times exactly, because the legs
are the same `walk_minutes`. So every stop the solver or the fill chose, if it has known hours, gets
`hours_ok = True`. Only these cases can produce `hours_ok = False`:
- must-visits placed in the non-strict pass, and the stops after them;
- orders the user edited;
- times from street routing.

The assembly tolerance of 30 + 60 = 90 minutes absorbs moderate drift; such waits are scheduled and shown as
`open_after_wait`.

*Example (S01, variant 1).* The first leg, start to "Theodor Aman" Museum, is 0.60 min and 45 m: arrival 10:01,
visit 10:01–11:01. The 8 legs add up to 19.7 min and 1.48 km. The total is 224.75 min, so the walk returns at
13:45 with `slack_min` 15.25.

## 8. Variants (`core:plan_variants`)

```text
used = {}
repeat n times:
    candidates' = candidates with interest x 0.35 for places in `used`   # from the ORIGINAL values: not compounded
    plan = plan_walk(request with candidates')
    keep the plan if it has stops and its ordered tuple of place ids is new
    used = used + (non-pinned stops of the plan)                         # a dropped duplicate also counts
```

- Must-visits are never penalised: they are not candidates, and pinned stops never enter `used`.
- A duplicate is dropped and not retried, so fewer variants can come back. That gives the request message
  `fewer_variants` (built, requested). When no variant has stops, the status is `no_route`.
- Every attempt runs the whole solver and makes one router call, duplicates included. Variants run sequentially,
  because variant k needs variant k−1's `used`.

*Example (S01).*
- Variant 1: 7 stops.
- Variant 2: 4 stops (Artmark, Cafe Chocolat Ateneu, Kretzulescu Park, The Lobby).
- Variant 3: 9 stops. It reuses Piața Revoluției, because the park slot has only 8 candidates. In the solver its
  interest is the penalised 0.145 (0.415 × 0.35); the API reports the original 0.415 (`VariantState.interest`;
  D9).

The penalty also applies to the on-the-way pool, and eligibility (≥ 0.3) is checked on the penalised value. A
reused place therefore needs interest ≥ 0.857 to stay eligible (D8). Later variants of long windows run out of
on-the-way stops: S08 gives 55, 3 and 37 stops.

## 9. The API view (`present:plan_response`)

`pipeline:_variant_state` turns each plan into a `VariantState`, which holds:
- the plan;
- the candidate sequence behind it (dwell as scheduled, hours from the catalog);
- the dropped slots;
- the activity of each on-the-way stop;
- each stop's original interest.

`present` derives the response from these.

**Stop fields.**
- **`kind`:** `pinned` > `on_the_way` > `slot`, in that order of precedence (`present:stop_kind`).
- **`slot_index`:** the slot the stop fills. It is null for on-the-way stops, and for pinned stops that are not
  also the solver's pick for a slot (`present:stop_slot_index`).
- **`activity`:** the slot's code; for on-the-way stops, the code of the pool the stop came from.
- **`interest`:** the un-penalised value; 0 for a pinned stop without a slot.

**`hours.status`** (`present:hours_status`):

| Status | When |
|---|---|
| `not_checked` | the catalog has no hours, so there is no clock |
| `unknown` | the place's hours are unknown; it is treated as open |
| `open` | `hours_ok` and wait < 1 min |
| `open_after_wait` | `hours_ok` and wait ≥ 1 min; the visit starts at arrival + wait |
| `closes_during_visit` | `hours_ok` is False and the place is open at arrival (`visit_wait(h, t0 + arrival, 0, 0)` is not None) |
| `closed_at_arrival` | `hours_ok` is False and the place is closed at arrival |

`hours.day` (that day's opening hours) is evaluated at the visit start. The `stop_hours_conflict` message is
evaluated at arrival.

**TimePoints.**
- `offset_min` is raw minutes since departure.
- `local` is the window start + `round(offset_min)` minutes, using Python's round-half-to-even, like the
  dashboard's clock.

**Segments.**
- `from` and `to` are `{kind: start|stop, stop_index}`, derived from the point list.
- `depart` is the previous stop's departure, or 0 at the start; `arrive = depart + walk_min`.
- `distance_m`, `quality` and `provider` are included.
- Geometry is GeoJSON, or polyline6 on request.

**Variant messages,** in this order:
1. `extras_added`, `slots_dropped`: only while the variant is unedited;
2. `over_budget`;
3. `stop_hours_conflict`, per stop;
4. `place_temporarily_closed`, per stop;
5. `routing_estimate`;
6. `route_empty`, for an empty route.

Codes, params and RU/EN texts are in `docs/messages.json`.

**Summary.** It holds:
- stop counts: `stops_total`, `slots_requested`, `slots_filled`, `on_the_way`, `pinned`;
- `dropped_slot_indices`;
- `finish_at` and `finish_kind` (`return` or `finish`);
- the totals: walk, dwell, wait and distance;
- `slack_min = B − total`, `over_budget` and `routing`.

**Navigation** comes from `core:navigation_links`:
- Google "whole route" links, split every 9 waypoints; part N+1 starts where part N ends. They carry the stops'
  `ChIJ…` place ids.
- Per-leg Google and Apple links, keyed by segment index.

**`plan_id`** is the sha1 of the request echo plus the versions: the same request on the same data gives the same
id.

## 10. Editing

v1 editing is stateless. The client keeps the plan response's `request` (the echo) and a variant's `sequence`, and
sends them back. Each EditStop holds `place_id`, `kind`, `slot_index`, `activity`, `dwell_min` and `dwell_fixed`.

```text
POST /v1/walks/schedule  {request, sequence}            -> the exact order        (reorder, remove, restore, minutes)
POST /v1/walks/insert    {request, sequence, place_id}  -> one more place at its best position
```

### 10.1 Re-hydration (`pipeline:from_request_echo`, `pipeline:sequence_candidates`)

The echo is validated again and the context recomputed. Each EditStop becomes a `Candidate`. Name, coordinates,
hours and business status always come from the catalog; the client never supplies them.

| EditStop kind | `slot`, `theme` | `interest` | `dwell_min` |
|---|---|---|---|
| `slot` (needs `slot_index`) | slot index; the slot's dwell key | interest map value | as sent (5..480) |
| `on_the_way` (needs `activity`) | the activity's index among the slots, or −1; its dwell key | map value | as sent |
| `pinned` with `slot_index` | like `slot` | map value | as sent |
| `pinned` without | −1; the place's own theme | 0 | as sent |

The interest map is the cold start, unless the taste artifacts and favourites are present (the service passes
them). Interest never changes an edit's result; it is only reported.

Refusals:
- an unknown place: 404; 409 `catalog_changed` when the echo's `catalog_version` differs from the loaded bundle;
- a `closed_forever` place: 422;
- duplicate places, bad kinds, bad slot indices or a dwell out of range: 422;
- more than 150 stops: 422.

### 10.2 Schedule (`pipeline:schedule` → `core:plan_sequence`)

Schedule runs `_assemble_plan` on the exact order. Nothing is dropped, reordered or rescaled:
- waits of up to 90 minutes are scheduled;
- a stop that is closed at its new time is kept and flagged;
- `over_budget` reports an overrun.

It makes one router call.

### 10.3 Insert (`pipeline:insert_place` → `core:best_insertion` → `core:plan_sequence`)

The new place is a pinned stop. Its dwell is the own-theme estimate (not style-scaled), or `dwell_min` if given,
which is then fixed.

Status policy:
- `closed_forever`: 422;
- `temporarily_closed`: 422 `place_temporarily_closed`, unless `allow_temporarily_closed` is set (then it is added
  and flagged);
- already in the route: 409.

The position follows the two-pass rule of §6.4, applied to the current order with a 30-minute wait limit and a
45-minute leg cap. The response carries `inserted_index`.

### 10.4 What editing does not do

- It does not refill or trim on-the-way stops, and does not fit the window (WP-26). Example: adding Stavropoleos
  to S01 variant 1 puts it at position 4 and gives 279 min in the 240-min window; the 3 on-the-way stops stay.
- It does not re-optimise the order (WP-36).
- It does not replace a slot of the same type (WP-34).
- An edited variant loses `extras_added` and `slots_dropped`.

Measured on S01 variant 1, in process with the estimate: schedule ≈ 0.8 ms, insert ≈ 1.0 ms.

## 11. Opening-hours model

### 11.1 Representation and parsing

- A place's hours are a list of `[open, close]` week-minutes, Monday 00:00 = 0, city-local. `close` may pass the
  end of the day. For example, Sunday 20:00–03:00 is `[9840, 10260]`.
- The research catalog builder (`build_city_catalog.py`) converts DataForSEO timetables with
  `core:week_intervals_from_timetable`:
  - a missing or null weekday is closed;
  - `close <= open` means after midnight (+1440), so 00:00 is the end of the day and 00:00–00:00 means 24 h;
  - malformed intervals are skipped.
- The catalog, and the bundle after it, stores the hours as JSON text. `catalog:parse_hours` accepts only a JSON
  list; NaN, an empty string, other text or broken JSON all mean unknown.
- **Unknown (`None`) is open everywhere:** wait 0, `hours_ok` None, status `unknown`. 4,665 of Bucharest's 12,961
  places have no hours (WP-16).
- **`[]` means never open** (D16). Today no catalog row has it.

### 11.2 The merged week (`core:_merged_week`)

Each interval is copied at −1, 0 and +1 week. The copies are sorted, and overlapping or touching ones are merged.
As a result:
- a Sunday-night opening covers early Monday;
- back-to-back 24-hour days form a single span; seven 24-hour days become one span from −10080 to 20160.

The result is memoised by value (`lru_cache`, 65,536 entries).

### 11.3 `visit_wait`: the whole-visit rule (`core:visit_wait`)

```text
visit_wait(hours, t, dwell, max_wait):
    hours is None                       -> 0
    t' = t mod 10080
    first merged span (o, c) with  s = max(t', o),  s + dwell <= c  and  s - t' <= max_wait   -> wait = s - t'
    no such span                        -> None ("closed")
```

The whole visit must fit inside one opening span; a place that closes mid-visit counts as closed. Verified
examples:

| Hours | Arrival | Dwell | max_wait | Result |
|---|---|---|---|---|
| museum, Sat 10:00–18:00 | Sat 09:45 | 60 | 30 | wait 15 |
| same | Sat 09:15 | 60 | 30 (planning) | None: infeasible for the solver |
| same | Sat 09:15 | 60 | 90 (assembly) | wait 45: `open_after_wait` |
| same | Sat 17:30 | 60 | 30 | None: the visit would end at 18:30 |
| bar, Fri and Sat 18:00–02:00 | Sat 00:30 | 60 | 30 | wait 0 (inside Friday's opening) |
| same | Sat 01:30 | 60 | 30 or 90 | None (the visit would end at 02:30); open at arrival, so `closes_during_visit` |
| Sun 20:00–03:00 | Mon 01:00 | 60 | 30 | wait 0 (via the previous week's copy); RU label "пн до 03:00" |

### 11.4 `open_within`: the candidate prefilter (`core:open_within`)

`open_within` is true when some merged span overlaps the window `[t0, t0 + B]` by at least `dwell` minutes. It
uses the activity's base dwell (45 for sights), not the place's estimate. It also ignores when the slot will
actually be reached: the solver checks that.

*Example.* A Saturday window from 05:30 to 10:30, a museum open 10:00–18:00 and a dwell of 45: the overlap is
30 minutes, so the museum is dropped. The visit has to end inside the window.

### 11.5 Tolerances by stage

| Stage | What is checked | Max wait |
|---|---|---|
| candidate prefilter (`open_within`) | the visit fits somewhere in the window | — |
| beam search | at the exact arrival time | 30 |
| `_route_minutes`, strict (fill; insertion pass 1) | every stop at its arrival | 30 |
| `_route_minutes`, non-strict (insertion pass 2) | not checked | — |
| assembly | flag only (`hours_ok`) | 90 |
| view | status from `hours_ok` plus the open-at-arrival test | 0 |

Neither the solver nor the fill ever schedules free time: a wait of at most 30 minutes is the only slack. A slot
whose places all open later than that is dropped (WP-22). Example: coffee at 05:30.

### 11.6 Time zone

The core has no time zone. `t0` comes from the request's local date and time, and the bundle's hours are local
too. A DST shift inside a window is not modelled.

## 12. Dwell estimation

### 12.1 Formula (`core:estimate_dwell_min`)

```text
base = DWELL_MINUTES[subtype] if subtype is a key, else DWELL_MINUTES[theme] if theme is a key, else 40
if "museum" in subtype:                    base = max(base, 60)
if theme not in {food_drink, restaurant, cafe, coffee, bar}:          # food & drink keep their base
    if QUICK_LOOK matches subtype + AI type ("_" -> " "):   base = min(base, 15)   # monument, statue, tower, square, ...
    if the review count is known:          base *= 0.5 (<20) | 0.75 (<200) | 1 (<2,000) | 1.25 (<10,000) | 1.5
    if BRIEF_HINT matches AI type + card summary:   base *= 0.6       # brief, quick, small, tiny, pocket, courtyard
dwell = clamp(5 * round(base / 5), 10, 180)          # Python round = half to even (D17)
```

`DWELL_MINUTES` keys include `restaurant` 75, `coffee`/`cafe` 30, `bar` 60, `culture_sights` 45, `museum` 60,
`church` 20, `park` 40, `viewpoint` 15, `performing_arts` 90 and `shopping_souvenirs` 30. §18 lists them all.

### 12.2 Inputs per kind of stop

| Stop | `theme` | `subtype` | Afterwards |
|---|---|---|---|
| slot candidate | the activity's dwell key (sight → `culture_sights`, coffee → `coffee`, food → `restaurant`, park → `nature_outdoors`, ...) | `primary_type` | × `dwell_scale` (chill 1.3) |
| slot with a user dwell | — | — | the user's minutes, fixed |
| on-the-way | as for a slot | as for a slot | capped at 10 in the core |
| must-visit or inserted place | the place's own catalog theme (`theme`, else `theme_group`) | `primary_type` | not scaled; or the given `dwell_min` (insert) |
| edited stop | — | — | as sent by the client |

### 12.3 Examples (Bucharest bundle)

| Place (slot) | Inputs | Computation | Dwell |
|---|---|---|---|
| National Museum of Art (sight) | `art_museum`, 10,235 reviews | 45 → museum 60 → × 1.5 = 90 | 90 |
| Stavropoleos church (sight) | `historical_landmark`, 5,781 reviews, "brief" hint | 45 × 1.25 = 56.25 → × 0.6 = 33.75 | 35 |
| Piața Revoluției (park) | `park`, 39 reviews, "historic town square" | 40 → quick look 15 → × 0.75 = 11.25 | 10 |
| Cafe Chocolat Ateneu (coffee) | `primary_type` = restaurant | the subtype key wins: 75; food: no scaling | 75 (D15, WP-17) |
| boteca13 (coffee) | `cafe` | 30 | 30 |
| any sight with 10 reviews | — | 45 × 0.5 = 22.5 → 5·round(4.5) = 5·4 | 20 (D17) |

### 12.4 Which dwell each stage uses

| Stage | Dwell used |
|---|---|
| radius sizing (§3.4) | the activity's base dwell |
| hours prefilter (§11.4) | the activity's base dwell |
| beam search and the hours checks | the scaled per-place dwell |
| adaptive walk weight | the shortest scaled dwell in each slot |

## 13. Personalization

### 13.1 Cold start (`interest:cold_start`; cached as `CityCatalog.cold_interest`)

```text
fame(p)    = log1p(reviews_p) / log1p(max reviews in the city)     # NaN -> 0; closed places count in the max
quality(p) = clip(rating_p - 4.0, 0, 1)                             # bayesian_rating, else google_rating; NaN -> 0.3
cold(p)    = 0.7 * fame(p) + 0.3 * quality(p)
```

Without a review-count column, the cold start is a min-max of `map_visibility_score`, else of `google_rating`;
without either, it is zeros.

*Example.* National Museum of Art: 10,235 reviews, city maximum 83,400, Bayesian rating 4.6.
- fame = 9.234 / 11.331 = 0.815
- cold = 0.7·0.815 + 0.3·0.6 = 0.750

The highest value in Bucharest is about 0.877.

### 13.2 Seeds (`interest:resolve_seeds`)

- Seeds are favourites (weight 1.0) plus want-to-go places (weight 0.55). A place in both lists counts as a
  favourite.
- A seed is valid when the city's taste artifacts know it and it has a text embedding.
- At most 200 seeds are used, favourites first. Every id that is not used goes to `favourites_ignored`: unknown
  to the city's artifacts (favourites from another city included), without a text embedding, or over the cap.
- No valid seed means the cold start (mode `popularity`).

### 13.3 Taste profiles (`interest:cluster_profiles`)

- With fewer than 4 seeds there is one profile.
- Otherwise the seeds' text vectors are clustered agglomeratively (cosine, average linkage) for
  K = 2..min(6, n//3, n−1, ⌊log₂ n⌋):
  - every cluster must have at least 2 seeds;
  - the K with the best silhouette wins if that silhouette is ≥ 0.10;
  - otherwise there is one profile.
- Clustering needs scikit-learn. Without it there is one profile, and the P02 golden output would differ.

*Example.* P02's six seeds split into 2 profiles, food and museums.

### 13.4 Taste score of a profile (`interest:_profile_score`)

The pool is every place with a text embedding, minus the seeds. For a profile k with seeds S_k:

| Channel | Formula | Weight |
|---|---|---|
| text | pct over the pool of `cos(t_p, text centroid) − 0.5·csls_density_p` | 0.26 |
| image | pct over the pool places with a photo of `cos(v_p, image centroid)`; 0 for a place without a photo (the "zero" policy); the channel is off when no seed of the profile has a photo | 0.50 |
| tag | Jaccard(tags of p, union of the tags of S_k); 0 when p has no tags | 0.08 |
| axis | `clip(1 − mean_i abs(axis_p,i − ā_i)/100, 0, 1)` over the 8 vibe axes, where ā is the weighted seed mean; a place without axes gets the median of the others | 0.06 |
| quality | quality v2: the Bayesian-shrunk Google rating / 5 (m = 25, prior = the city mean) | 0.06 |
| price | `clip(1 − abs(cheap_expensive_p − ā)/100, 0, 1)` | 0.04 |

- **S_k(p)** is the weighted average over the active channels. Weights are renormalised when the image channel is
  off.
- **taste(p) = max over k of S_k(p).** On a tie, the first profile wins.
- **pct** is the average-rank percentile; ties share their mean rank.
- **Centroids** are weighted means of unit vectors, normalised again.
- **csls_density** is a place's mean cosine to its 10 nearest city neighbours, precomputed at bundle build. It is a
  hubness penalty that demotes generic, "central" descriptions.

This is a numpy port of the v4 feed engine's per-profile blend (`TEXT_DIRECT_WEIGHTS`, missing image policy
"zero"). Against the engine it measures Spearman 0.9998–0.9999 and top-50 overlap 0.98–1.00 (research-repo test
`test/test_walk_interest_parity.py`). One deliberate difference: v1 also scores the low-confidence places the
engine drops.

### 13.5 The quantile-preserving rank blend (`interest:interest_map`)

For each catalog theme θ, over its places that have a taste score and are not seeds:

```text
b(p)        = (1 - β) * pct_θ(cold(p)) + β * pct_θ(taste(p))      β = personalization_strength (default 0.5)
rank the places by b, ascending (ties -> place_id)
interest(p) = the cold values of θ, sorted ascending, handed out in that rank order
```

Seeds and places without taste keep their cold value. With β = 0, or without a valid seed, the result is the cold
start exactly.

*Worked example.* One theme with 4 places, β = 0.5:

| Place | Cold | Taste | pct cold | pct taste | b | New interest |
|---|---|---|---|---|---|---|
| A | 0.2 | 0.9 | 0.25 | 1.00 | 0.625 | 0.4 |
| B | 0.4 | 0.1 | 0.50 | 0.25 | 0.375 | 0.2 |
| C | 0.6 | 0.5 | 0.75 | 0.75 | 0.750 | 0.6 |
| D | 0.8 | 0.3 | 1.00 | 0.50 | 0.750 | 0.8 (ties with C; C comes first by place_id) |

The set of values {0.2, 0.4, 0.6, 0.8} does not change; only which place gets which value does. With β = 1 the
result is A 0.8, C 0.6, D 0.4, B 0.2.

*Real example (P01).* The seeds are Origo, Herăstrău Park and National Museum of Art; the theme is
`culture_sights`, with 1,193 places (`python -m walk_planner interest`).
- The Royal Palace of Bucharest moves from cold 0.643 (68th) to 0.803 (taste percentile 1.00; most similar seed:
  National Museum of Art).
- Arcul de Triumf moves from 0.818 to 0.864.

**Why a rank blend.** The planner's constants were tuned on the cold distribution: λ, the ≥ 0.3 gate, interest²
and the near weight 8. The rank blend keeps that distribution in each theme. Personalization changes which places
win without inflating every score. The dashboard's previous path replaced interest with the engine score, which
pushed stop interest to 0.87–0.99 and gave the favourites themselves 0; v1 replaced that path.

**Measured effect** (research, β 0.5, the S01 request):
- 3–8 of each slot's top 8 change.
- 5–8 of the 7–9 route stops change.
- Famous places stay: 6–8 of each top 8 have ≥ 500 reviews.
- P01 variant 1: Artmark · Palatul Cesianu-Racoviță · boteca13 · National Museum of Art · Royal Palace · National
  Museum of Romanian Literature · Parcul Ateneului · MACE.

### 13.6 Plumbing and failure modes

- **`pipeline:resolve_interest`.**
  - No ids: the cold start.
  - No artifacts: the cold start plus `personalization_unavailable` with reason `taste_unavailable`.
  - Any exception: the cold start plus the message, with reason `interest_error:<type>`.
- **Response `personalization`:** `mode`, `strength`, `favourites_used`, `favourites_ignored`, `profiles`. The
  per-place `taste_pct` and `similar_to` are computed but not in the v1 response (WP-43).
- **Cost.** 20–45 ms for 3–6 seeds over 12,961 places; up to about 90 ms for 200 seeds. The first request with 4
  or more seeds pays the scikit-learn import (about 0.9 s), unless `interest.warmup` ran; the service runs it at
  startup.
- **Artifacts** (`interest_build:build_taste_artifacts`, built with the bundle):
  - L2-normalised text vectors (1536-d) and OpenCLIP image vectors (512-d), both float16;
  - `has_image`;
  - the CSLS density (k = 10);
  - tags: AI tags without low-confidence or negative ones;
  - 8 vibe axes (a missing value counts as 50);
  - quality v2.

  For Bucharest they take 53.5 MB.

## 14. Routing

### 14.1 The estimate drives every decision

```text
walk_minutes(a, b) = haversine_km(a, b) * 1.35 / 4.5 * 60   = 18 minutes per straight-line km
```

`core:RoutingProvider.walk_minutes` is the only travel cost the optimizer knows. Every decision uses it:
- the reach radius;
- the near-start list (the same 18 min/km);
- the beam;
- `_route_minutes` (the fill and insertion);
- the 45-minute leg cap.

`routing:ChainProvider` inherits it unchanged, so **places and order never depend on the router or the network**
(`tests/test_routing.py`).

### 14.2 The router only times and draws the final legs

There is exactly one `route_legs(points)` call per assembled plan: one per variant attempt and one per edit. It
goes through a `ChainProvider` built once per HTTP request (`routing:make_provider(start=...)`), which tries, in
order:

```text
leg cache (L1 in-process LRU + optional Redis)  ->  self-hosted OSRM, foot profile (ONE GET for all legs)
  ->  ORS cloud (<= 50 waypoints per POST, local rate limiter, quota-aware breaker)  ->  straight-line estimate
```

- One routing budget of 6 s (`WALK_ROUTING_DEADLINE_S`) is shared by all variants of a request. Once it is spent,
  the remaining legs are estimated.
- A router that failed in a request is skipped for the rest of that request.
- On fallback, street legs already in the cache are kept, so the plan's quality is `mixed`.
- `route_legs` never raises. In the worst case every leg is an estimate, flagged as one.
- With nothing configured (no `WALK_ROUTER_URL`, no `ORS_API_KEY`), the provider is the plain estimate. That is
  the golden setting.

[ROUTING.md](ROUTING.md) and the `walk_planner/routing.py` docstring cover the chain itself: cache keys and TTLs,
breakers, OSRM and ORS specifics, operations.

| Operation | `route_legs` calls | HTTP requests per call |
|---|---|---|
| plan with n variants | n (duplicates and empty variants included; empty routes make no call) | 0 if every leg is cached; else 1 OSRM GET; ORS only if OSRM fails (chunks of ≤ 50 waypoints) |
| schedule, insert | 1 | same |
| solver, insertion, fill | 0 | — |

### 14.3 Quality flags

| Flag | Values |
|---|---|
| `Segment.quality` | `streets` or `estimate` |
| `Segment.provider` | `osrm`, `ors` or `haversine` |
| `WalkPlan.routing_quality` (API `summary.routing`) | `streets`, `estimate`, `mixed` or `none` |
| variant message `routing_estimate` | sent whenever any leg is an estimate: `{segments_estimated, segments_total}` |
| `versions.routing` | the OSRM `data_version` |

The service log carries the chain's events and summary.

### 14.4 What street routing changes, and what it does not

Street routing changes arrival times, waits, the hours flags (`hours_ok` is re-evaluated with the 90-minute
tolerance), totals, distances, `over_budget` and the geometry. It never changes the places, their order or the
dropped slots.

OSRM's foot profile walks real streets at about 5 km/h; the estimate's effective speed is 4.5 / 1.35 ≈ 3.3 km/h of
straight-line distance. The window was packed against the estimate, so a plan can end slightly over the window on
streets, or with more slack (WP-19). v1 has no repair step after routing.

## 15. Determinism

- **No randomness.** The same request on the same bundle and versions gives the same response and the same
  `plan_id`. This was verified across `PYTHONHASHSEED` values, threads and two interpreter stacks.
- **Tie-breaks:**
  - beam: a stable sort, so candidate order decides;
  - `best_insertion`: the earliest position;
  - fill: the first maximum, by pool order then position order;
  - interest blend: `place_id`;
  - taste profiles: the first profile.
- **Candidate lists follow the catalog row order:** pandas `sort_values` default, `nsmallest(keep="first")`,
  first occurrence in the merge. The bundle keeps the CSV row order. A catalog rebuilt in a different row order can
  change plans wherever interests tie, and cold-start interests tie a lot: the research measured 6,107 distinct
  values over 12,961 places.
- **Floating-point ties.** A loop and its mirror image take mathematically the same time. Which one wins is
  decided by floating-point rounding, which means by the last bits of the coordinates. The bundle builder parses
  CSVs with `float_precision="round_trip"`, so every platform reads the same floats. When that parser setting
  changed, S19's variants 2 and 3 reversed direction (`golden/README.md`).
- **Cross-platform libm.** Floats can differ in the last bit (≤ 1e-14) between platforms. Golden outputs compare
  numbers with a tolerance of 1e-6, and a clock label can differ by one minute when it sits exactly on a half
  minute.
- **Not deterministic:**
  - street-routed times, which depend on the router's data, the cache and outages; they never change places or
    order;
  - personalised plans if the taste artifacts change; their fingerprint is part of the bundle.

## 16. Complexity and measured performance

### 16.1 Complexity

Notation:
- N: catalog rows (12,961).
- S: non-empty slots; K: candidates per slot (≤ 2·top_k).
- W: beam width (256).
- h: merged opening spans per place (≤ ~21).
- L: route length; P: pool (≤ 80); R: on-the-way stops inserted; M: must-visits; n: variants.

| Part | Cost |
|---|---|
| distances from the area | O(N) once per build (~7 ms) |
| `slot_candidates` | O(N) per slot and per pool activity: pandas masks plus a Python `open_within` over every row with hours (~35 ms per call) |
| taste blend | O(N·(1536 + 512)) for the matrix-vector products, plus per-theme sorting |
| `_solve_slots` | O(S·W·K) extensions × O(h) for `visit_wait`, plus sorting O(S·W·K·log(W·K)) |
| `_fill_extras` | O(R·P·L·(L + h)): every round tries P × (L+1) positions, and each `_route_minutes` is O(L·h). **This dominates for long windows.** |
| `best_insertion` | O(M·L²·h) |
| `_assemble_plan` | O(L·h) plus one router call |
| `plan_variants` | n × `plan_walk`, sequential |

### 16.2 Measured times

Setup: in-process `build_plan`, Apple Silicon (arm64), Python 3.13.3, estimate routing, warm catalog. The machine
was shared with other jobs (load average 7–13), so treat the numbers as ±20 %.

| Scenario | Window, radius | search_km | Slot cands / pool | Stops per variant | `build_plan` | of which `plan_variants` | Fill's share of it |
|---|---|---|---|---|---|---|---|
| S01 default | 10–14, 2.5 | 1.39 | 54 / 79 | 7 / 4 / 9 | 0.30–0.45 s | 0.10 s | 15 % |
| P01 favourites, 3 seeds | 10–14, 2.5 | 1.39 | 54 / 81 | 8 / 6 / 9 | 0.38–0.45 s | 0.13 s | 61 % |
| P02 favourites, 6 seeds | 10–14, 2.5 | 1.39 | 54 / 79 | 7 / 4 / 7 | 0.36–0.44 s | 0.11 s | 15 % |
| S02 chill | 10–14, 2.5 | 1.39 | 54 / 79 | 4 / 4 / 4 | 0.27–0.45 s | 0.07 s | 0 % |
| S05 free | 10–14, 2.5 | 1.39 | 54 / 79 | 7 / 6 / 4 | 0.29–0.39 s | 0.08 s | 29 % |
| S15 five variants | 11–16:30, 2.5 | 2.5 | 58 / 85 | 12/10/13/11/9 | 0.57 s | 0.37 s | 69 % |
| S07 long day | 10–22, 15 | 14.7 | 63 / 113 | 29 / 22 / 20 | 1.45 s | 1.25 s | 96 % |
| S22 huge radius, 5 slots | 8–22, 50 | 16.4 | 79 / 113 | 33 / 27 / 20 | 1.87 s | 1.62 s | 95 % |
| S16 all 8 slots | 9–21, 2.5 | 2.5 | 110 / 184 | 28 / 29 / 26 | 2.78 s | 2.37 s | 93 % |
| S08 24 h | 0–24, 5 | 5.0 | 62 / 106 | 55 / 3 / 37 | 3.15–3.25 s | 2.97 s | ~100 % |

- **Candidate selection** takes about 0.21 s for a 4-slot request (6 `slot_candidates` calls) and 0.41 s for S16.
- **The beam** dominates short windows; **the fill** dominates long ones, because it makes about 1.7 million
  `walk_minutes` calls for S08.
- **Editing** takes about 1 ms in process without favourites.
- **Service level** ([SPEC.md §10](SPEC.md#10-performance-and-capacity) is the reference, measured 2026-10-03
  through HTTP, p50):
  - plan S01 0.31 s locally and 0.43 s in the image, P01 (favourites) 0.35 / 0.50 s, the 24-hour S08
    3.2 / 4.0 s; schedule and insert 3–4 ms, 30–55 ms when the echo carries favourites;
  - steady memory per worker ≈ 0.6 GB, up to 1.1–1.3 GB under bursts;
  - `WALK_MAX_CONCURRENT_PLANS` (default 2 per worker) answers 503 `busy` beyond that.

Speedups that keep the output bit-identical are in §20.

## 17. Debugging recipes

Run these from `services/walk_planner`. `PY` is the venv's Python, and `B` is a bundle directory.

```bash
# the dashboard's Russian rendering of a plan (stops, times, hours, messages); straight-line routing
$PY -m walk_planner plan --bundle $B --routing estimate --date 2026-10-03 --start 10:00 --end 14:00 \
    --shape loop --style max --slots sight,coffee,park,food --pretty --timing

# full API JSON + the "debug" block (search area, search_km, every slot / pool candidate with interest)
$PY -m walk_planner plan --bundle $B --routing estimate --date 2026-10-03 --debug > plan.json

# what favourites do to interest: cold rank, cold, interest, taste percentile, most similar seed
$PY -m walk_planner interest --bundle $B --fav 14169121335398956031,16044012576954065712 --theme culture_sights --top 20

# replay edits on a saved plan's variant (its sequence + request echo): move stop 0 to position 3, then add a place
$PY -m walk_planner schedule --bundle $B --from-plan plan.json --variant 0 --edit move:0:3 --pretty
$PY -m walk_planner insert --bundle $B --from-plan plan.json --variant 0 --place 10915586233752676659 --pretty

# behaviour gates: golden outputs (exit 1 on any difference) and the pre-refactor parity check
$PY -m walk_planner golden run --bundle $B -v
PYTHONDONTWRITEBYTECODE=1 $PY golden/tools/parity_check.py --source bundle
```

In Python, `PlanResult.request` is the exact `core.WalkRequest` the solver ran, so the core can be called step by
step:

```python
from walk_planner.bundle import load_bundle
from walk_planner import pipeline, core
lb = load_bundle(B, verify=False)
p = pipeline.normalize_params({"city": "Bucharest", "date": "2026-10-03", "start": "city_center"}, lb.catalog)
res = pipeline.build_plan(p, lb.catalog, taste=lb.taste)
res.context.search_km, len(res.slot_candidates), len(res.extra_candidates), res.interest.mode
req = res.request
plan = core.plan_walk(req)                          # variant 1 again (deterministic)
core._route_minutes(res.variants[0].sequence, req, req.provider)    # strict feasibility: None = closed / long leg
```

| Symptom | Where to look |
|---|---|
| a slot is empty | `slots_no_candidates` (no candidate: radius, filters, hours prefilter) vs `slots_dropped` (had candidates; the beam found no time or no open place) |
| a strange place in a slot | membership step 1 in §5.1 (keyword substring) and the near list (step 8); check `--debug` candidates |
| a stop at a closed time | it is a must-visit or inserted place placed in the non-strict pass (§6.4), or a street-routed time drift; `hours.status` says which |
| no on-the-way stops | `fill_window` off; the slot route already ≥ fill·B; or `_route_minutes(route)` is None (D3, D4) |
| fewer variants than asked | identical routes are dropped (§8); typical when the pools are small |
| over budget | the "nothing fits" fallback (§6.3), must-visits (D5) or street times (§14.4) |

## 18. Tuning: the constants

**Every value below is pinned by the golden outputs.** To change one:

1. Change it and run `golden run -v`.
2. Review the plan differences.
3. Bump `ALGORITHM_VERSION` (MINOR) and run `golden update`.
4. Add a CHANGELOG entry (`golden/README.md`, "Updating").

`plan_id` changes with the version.

**Rules of thumb:**
- The solver weighs **interest × λ against walking minutes × w**.
- The window is enforced by **constraints** (budget, 30-minute wait, 45-minute leg), not by cost.
- Candidate selection decides **what the solver can see**. Most "wrong place" complaints are fixed there, not in
  the solver.

### 18.1 Travel model and window

| Name | Value | Where | Effect |
|---|---|---|---|
| `DEFAULT_WALK_KMH` | 4.5 | `core` | walking speed of the estimate; scales every leg, the reach radius and the near list |
| `DEFAULT_DETOUR` | 1.35 | `core` | straight line → street factor; together with the speed it gives 18 min per straight-line km |
| `EARTH_R_KM` | 6371.0088 | `core:haversine_km` | mean Earth radius (not a tuning knob) |
| `min_walk_min` | 15 | `core:reach_radius_km` (default argument) | minimum walking assumed when the slots overfill the window; the search radius floor (0.83 km one-way, 0.42 km loop/free) |
| loop/free halving | ÷ 2 | `core:reach_radius_km` | a loop must come back; "free" must fit a disc |
| `LIMITS` | window 15..1440, variants 1..5, radius 0.3..50, ≤ 8 slots, dwell 5..480, ≤ 10 must-visits, ≤ 500 personal ids, top_k 1..50, ≤ 150 edit stops, strength 0..1 | `pipeline` | API validation; also published by `/v1/walks/config` |
| `DEFAULTS` | 10:00, 240 min, loop, max, sight·coffee·park·food, 3 variants, 2.5 km, fill on, all hours, top_k 8, strength 0.5 | `pipeline` | values used when a field is absent |

### 18.2 Candidate selection

| Name | Value | Where | Effect |
|---|---|---|---|
| `ACTIVITY_TYPES` | per activity: group, themes, keywords, dwell key, allow/deny types, `ai_deny` | `slots` | what can fill a slot (§5.1, step 1); the base dwell of radius sizing and the prefilter |
| `NON_VENUE_RE` | tour, agency, rental, school, company, service, office, ... | `slots` | drops businesses that are not venues (`ai_deny` activities: sight, park, market, entertainment) |
| `NATURE_TYPES`, `MARKET_TYPES` | type allow lists | `slots` | precision filter of the park and market slots |
| `SIGHT_DENY` | park, store, gift_shop, tour_operator, library, ... | `slots` | precision filter of the sight slot |
| `LEGACY_GROUP` | sights / shopping → things_to_do | `slots` | theme_group gate for catalogs without a `theme` column |
| `MIN_FILTERED` | 3 | `slots` | relax the precision filters when fewer places pass (counted before status and hours) |
| `CLOSED_STATUS` | closed_forever, temporarily_closed | `slots` | never a slot or on-the-way stop |
| `DEFAULT_TOP_K` | 8 | `slots` (`top_k`) | top-K by interest + K near the start per slot (≤ 16 candidates); larger K = slower beam, more choice |
| `NEAR_WEIGHT` | 8.0 (= the max style's λ, whatever the style) | `slots` | walking minutes per unit of interest in the near list |
| `NEAR_MIN_REVIEWS` | 20 | `slots` | review floor for the near list (keeps 2-review courtyards out) |
| `EXTRA_K` | 30 | `slots` | pool size per activity (top 30 + 30 near) |
| `SCENIC_EXTRA_ACTIVITIES` | park, market | `slots` | pool activities for scenic |
| `FALLBACK_EXTRA_ACTIVITY` | sight | `slots` | pool activity when every slot is food or drink |
| `DWELL_CHOICES` | 10, 15, 20, 30, 45, 60, 75, 90, 120, 150, 180, 240 | `slots` | UI choices only (the API accepts 5..480) |

### 18.3 Dwell

| Name | Value | Where | Effect |
|---|---|---|---|
| `DWELL_MINUTES` | food_drink 60, restaurant 75, cafe 30, coffee 30, bar 60, culture_sights 45, museum 60, art_gallery 45, religious_sights 20, church 20, mosque 20, markets_walks 40, market 40, town_square 20, nature_outdoors 40, park 40, garden 40, viewpoint 15, performing_arts 90, leisure_active 60, shopping_souvenirs 30, things_to_do 45 | `core` | base visit length; subtype (`primary_type`) first, then theme |
| `DEFAULT_DWELL` | 40 | `core` | base when neither key is known |
| museum floor | 60 | `core:estimate_dwell_min` (inline) | any `*museum*` type |
| `_QUICK_LOOK` → cap | 15 | `core` | monuments, statues, towers, bridges, squares, ... |
| review-count factors | × 0.5 / 0.75 / 1 / 1.25 / 1.5 at < 20 / < 200 / < 2,000 / < 10,000 / more | `core:estimate_dwell_min` (inline) | size proxy (not for food and drink) |
| `_BRIEF_HINT` | × 0.6 | `core` | brief, quick, small, tiny, pocket, courtyard |
| `_FOOD_DWELL_THEMES` | food_drink, restaurant, cafe, coffee, bar | `core` | keys that skip the quick-look, size and brief rules |
| rounding, clamp | 5-minute steps, 10..180 | `core:estimate_dwell_min` (inline) | banker's rounding (D17) |
| `EXTRA_DWELL_MIN` | 10 | `core` | an on-the-way stop is a 10-minute look, whatever its type |
| `dwell_scale` | max 1.0 / chill 1.3 / scenic 1.0 | `core:STYLE_PRESETS` | multiplier on slot dwell (not on-the-way, user-fixed or must-visits) |

### 18.4 Solver

| Name | Value | Where | Effect |
|---|---|---|---|
| `interest_weight` (λ) | max 8 / chill 16 / scenic 12 | `core:STYLE_PRESETS` | walking minutes one unit of interest is worth; higher = more famous, further places |
| `walk_scale` | max 1 / chill 1 / scenic 0.5 | `core:STYLE_PRESETS` | multiplier on the walk weight |
| `fill` | max 0.95 / chill 0.75 / scenic 0.90 | `core:STYLE_PRESETS` | share of the window the on-the-way stops may fill up to |
| `max_stops` | max — / chill 4 / scenic — | `core:STYLE_PRESETS` | auto mode only: no effect in v1 |
| `MIN_WALK_WEIGHT` | 0.05 | `core` | floor of the adaptive walk weight (before `walk_scale`) |
| need per slot | 15 min | `core:_adaptive_walk_weight` (inline) | walking assumed per slot when judging how roomy the window is |
| walk-weight exponent | 2 | `core:_adaptive_walk_weight` (inline) | w = (need / B)² |
| `SKIP_SLOT_PENALTY` | 10,000 | `core` | cost of an empty slot when the budget applies; makes "more slots" lexicographic |
| beam width | 48; 256 with a budget | `core:_solve_slots` (default argument, inline) | search breadth: quality against time (D7) |
| `max_wait_min` | 30 | `core:WalkRequest` | longest wait at a door while planning |
| assembly wait tolerance | +60 (→ 90) | `core:_assemble_plan` (inline) | waits up to 90 are scheduled and shown, not flagged |
| `max_leg_min` | 45 | `core:WalkRequest` | longest leg into a stop (beam); every leg incl. the return (strict `_route_minutes`) |
| `MUST_VISIT_WALK_MIN` | 10 | `core` | walking reserved per must-visit when sizing the slots' budget (D5) |
| `EXTRA_MIN_INTEREST` | 0.3 | `core` | on-the-way eligibility (checked on the variant-penalised value, D8) |
| `EXTRA_INTEREST_POWER` | 2 | `core` | on-the-way value = interest² (+ 0.01) |
| fill pool | 80 | `core:_fill_extras` (`max_pool`) | on-the-way places considered per variant |
| fill score floor | value + 0.01, cost ≥ 1 | `core:_fill_extras` (inline) | avoids zero values and division by tiny costs |
| `VARIANT_REUSE_FACTOR` | 0.35 | `core` | interest multiplier for places earlier variants used |
| tolerances | 1e-6 budget; 1e-9 hours | `core` | float noise in comparisons |
| `SCENIC_THEMES`, `SCENIC_SUBTYPES` | — | `core` | auto mode only: no effect in v1 |
| `GMAPS_MAX_WAYPOINTS` | 9 | `core` | waypoints per Google "whole route" link |

### 18.5 Interest

| Name | Value | Where | Effect |
|---|---|---|---|
| cold-start weights | 0.7 fame + 0.3 quality; rating pivot 4.0; unknown rating 0.3 | `interest:cold_start` (inline) | the popularity proxy every interest value comes from |
| `TASTE_WEIGHTS` | text 0.26, image 0.50, tag 0.08, axis 0.06, quality 0.06, price 0.04 | `interest` | taste channel blend (= the feed engine's TEXT_DIRECT_WEIGHTS) |
| `CSLS_K`, `CSLS_PENALTY` | 10, 0.5 | `interest` (K used at bundle build) | hubness penalty on the text channel |
| `FAVOURITE_WEIGHT`, `WANT_TO_GO_WEIGHT` | 1.0, 0.55 | `interest` | seed weights in the centroids |
| `DEFAULT_STRENGTH` (β) | 0.5 | `interest`, `pipeline:DEFAULTS` | how much taste reorders interest (0 = cold start) |
| `MAX_SEEDS` | 200 | `interest` | seeds beyond this are ignored |
| `MIN_SEEDS_TO_CLUSTER`, `MAX_PROFILE_CLUSTERS`, `MIN_PROFILE_SILHOUETTE`, `MIN_PROFILE_CLUSTER_SIZE` | 4, 6, 0.10, 2 | `interest` | multi-profile split |
| `AXIS_FILL` | 50 | `interest` | value of a missing vibe axis |
| `QUALITY_SHRINKAGE_PRIOR` | 25 | `interest` | m of quality v2 |

### 18.6 Routing (assembly only; never changes places)

| Name | Value | Where | Effect |
|---|---|---|---|
| `WALK_ROUTING_DEADLINE_S` | 6.0 | `routing:DEFAULT_DEADLINE_S` | routing time budget of one request, all variants together; afterwards legs are estimated |
| `OSRM_RADIUS_M` | 1000 | `routing` | snapping radius; farther points become estimates |
| `ORS_MAX_PER_MIN`, `ORS_MAX_PER_DAY` | 35, 1800 (per process) | `routing` | local ORS limiter |
| leg-cache TTLs | OSRM 30 d, ORS 24 h, legs touching the user's start 1 h, estimates never | `routing` | — |

## 19. Known limitations and defects of v1

These are documented and **deliberately kept** in v1: v1 equals the dashboard algorithm plus the listed fixes, and
the golden outputs pin it. The IDs come from the research report (D2–D17) and the UX test report
(`recommendation_system/ai_location_recommender/WALK_PLANNER_UX_TEST_REPORT.md`, WP-xx).

### 19.1 Fixed or neutralised in v1

| ID | Was | v1 |
|---|---|---|
| BUG 1 / WP-50 | segment `from_order`/`to_order` shifted by one without a start anchor (shape free) | counted over the actual points (`core:_assemble_plan`); `present` also derives from/to from the point list |
| BUG 2 / WP-04 | closed places accepted as must-visit or added places | `candidates:place_candidate`: closed_forever refused, temporarily_closed flagged (or 422 on insert unless allowed) |
| D2 | `free` with a start: the solver counted a first leg that the route does not walk | not reachable: the API forces no start for `free` (still in the core for direct callers) |
| D9 | `Stop.interest` penalised in variants 2+ | the API reports the original interest (`VariantState.interest`); the core value stays penalised |
| D10 / WP-01 | ORS per-leg request storm; silent straight-line fallback | routing chain: one request per plan, cache, breakers, quality flags, `routing_estimate` |
| D11 | empty loop plan made a start→start segment and a router call | no points, no call |
| D12 | `auto` mode ignores waits, must-visits and hours | not reachable: the pipeline always uses slots mode |
| D13 | bad enums silently accepted; one_way/loop without a start silently anchor-less | validated at the API (`normalize_params`); the core is still lenient |
| WP-10 | personalization slow (7–20 s) and scale-breaking | taste port + rank blend (§13): 20–45 ms; needs UX validation |

### 19.2 Open defects (behaviour kept)

| ID | Defect | Where | How it shows up |
|---|---|---|---|
| D3 | A loop's return leg is not capped at 45 min by the beam, but the strict `_route_minutes` caps it. | `core:_solve_slots` vs `core:_route_minutes` | The strict check returns None, so **no on-the-way stops at all**, and must-visits go to the non-strict pass. Synthetic: legs 40 / 40 / 80 min, 220 of 600 min used, 0 on the way; the same request as one_way gets 6. |
| D4 | A must-visit placed in the non-strict pass poisons every later strict check. | `core:best_insertion`, `core:_fill_extras` | **S17** (09:00–19:00, must-visits National Museum of Art, Stavropoleos, Origo): the museum at 09:03 although it opens at 11:00 (`closed_at_arrival`), 0 on-the-way stops, 424 min slack, 1 of 3 variants. Later must-visits ignore hours too, so the order of the ids matters (WP-15). |
| D5 | The must-visit reservation is a flat 10 min of walking, and insertion ignores the window. | `core:plan_walk`, `core:best_insertion` | Synthetic: one slot + one must-visit in a 120-min loop = 131.7 min, `over_budget`, no slot dropped (the must-visit alone needs 69.6). |
| D6 | Must-visit dwell is not style-scaled. | `core:plan_walk` | chill: a slot stop 40 → 52 min, a must-visit stays 40. |
| D7 | The beam keeps the 256 cheapest partial states; cheaper-but-shorter states can evict ones that fill more slots. | `core:_solve_slots` | Synthetic only (2 of 4 slots while 4 fit); real data: 8 of 8 identical to a 20,000-state beam. |
| D8 | The variant penalty (× 0.35) applies before the on-the-way gate (≥ 0.3): a reused place needs interest ≥ 0.857. | `core:plan_variants`, `core:_fill_extras` | **S08** (24 h, radius 5): 55 / 3 / 37 stops; variant 2 returns at 03:24 with 1,236 min unused (WP-14, WP-32). |
| D14 | Without a budget, a state that cannot extend skips the slot for free. | `core:_solve_slots` | Reached in v1 only through the "nothing fits" fallback (§6.3). Synthetic: without a budget [X] with slot 1 dropped; with one [Y, Z]. |
| D15 | `dwell_for` uses the Google type before the slot's key. | `core:dwell_for` | Cafe Chocolat Ateneu (`primary_type` restaurant) is a 75-min coffee stop (S01 variant 2; WP-17). |
| D16 | `[]` means never open; `None` means unknown, i.e. always open. | `core:visit_wait` | Contract; 0 such rows today. |
| D17 | Banker's rounding in dwell (and in clock labels). | `core:estimate_dwell_min`, `present:clock_label` | 22.5 → 20, 112.5 → 110; kept for parity. |

### 19.3 Main UX-report issues and how they show up in v1

| WP | What users saw | Cause in v1 | Example |
|---|---|---|---|
| WP-02 | wrong-type places in slots (barbershop or BBQ as a bar, casino as entertainment, cinema as a sight) | keyword **substring** match without word boundaries (food slots); whole catalog themes (entertainment takes all of `leisure_active`); no type allow lists for sight, bar, coffee | S18: Jeonjuu Korean BBQ and Rio Juice in the bar slot; S10: Scala Cinema as a sight |
| WP-03 | low-rated and chain places | no rating floor; cold start rewards review counts | — |
| WP-05 | duplicates (one building, several records; park parts) | distinctness by `place_id` only; no clustering | — |
| WP-06 | the start place counted as a slot | the near list includes places 0 m from the start | — |
| WP-07 | main museums as 10-min on-the-way stops; small churches as main sights | on-the-way stops are capped at 10 min whatever the type; sight pools = top-K by fame + near list | S01 variant 1: National Museum of Art on the way for 10 min |
| WP-08 | always the same places | deterministic, fame-weighted interest | — |
| WP-12 | meals at odd times (bar at noon) | no meal time windows; slots are packed from the departure | S18: restaurant 11:07, bar 12:25 |
| WP-13 | one activity cannot be chosen twice | API rule (§3.1) | — |
| WP-14 | the window is used badly (slots early, an empty evening) | no free time; only waits ≤ 30; fill to `fill·B`; D8 | S08 |
| WP-15 | a must-visit placed when it is closed | inserted after the slots (§6.4); D4 | S18: Mosto at 09:48 (opens 18:30) with 556 min of slack |
| WP-16 | unknown hours treated as open (night visits) | `visit_wait(None) = 0`; no typical hours by type | 4,665 places without hours |
| WP-17 | unrealistic visit lengths | D15; must-visits use their own theme; chill × 1.3 includes food; on-the-way stops a flat 10 | §12.3 |
| WP-18 | short windows keep the wrong things or overrun | no degradation order; the fallback returns the full plan over budget | S10: 15-min window → 59–66 min plans |
| WP-19 | finish after the window end, "−0 min" | optimised on the estimate, routed on streets; strict `>` comparison; no post-routing repair | — |
| WP-20, 21, 22, 23 | no end reserve, kitchen hours, "leave later", breaks | not modelled; waits are capped at 30 min | S11 (Sunday 05:30–09:00, coffee + sight): coffee dropped with 197 min of slack, because cafés open later |
| WP-24 | "on the way" is a detour | the fill scores value per minute, with no detour cap | — |
| WP-25 | too many on-the-way stops, all churches | no count cap or type diversity; pool up to 80 | S08 variant 1: 52 on-the-way stops |
| WP-26 | edits do not give way: overruns and warnings pile up | schedule keeps the exact order; insert ignores the window; nothing is trimmed | S01 variant 1 + Stavropoleos: 279 min in a 240-min window, all 3 on-the-way stops kept (§10.4) |
| WP-27 | no finish point (A → B) | shapes are loop / one_way / free only | — |
| WP-28 | "free" ignores the user's area | `free` searches around the city centre | — |
| WP-29 | leg and day-distance limits not settable; long must-visit legs | `max_leg_min` fixed at 45 inside the core; the non-strict insertion ignores it | — |
| WP-32 | variants similar or using the window unevenly | penalty by place id only; D8 | S01: 7 / 4 / 9 stops |
| WP-34 | a must-visit does not replace a slot of the same type | must-visits are slot −1, inserted after solving | — |
| WP-35, 36, 37 | no "replace with similar", "re-optimise order" or time anchors | not in the v1 API | — |
| WP-42 | messages name the wrong cause | `slots_dropped` and `slots_no_candidates` do not know why (hours vs time vs radius) | S08 and S11: «Не поместилось в окно: Кофе» when cafés are simply closed |
| WP-43 | no "why this place" | `taste_pct` and `similar_to` are computed but not exposed | — |

The remaining items are not planner behaviour:
- WP-09, place search: `catalog:search` folds diacritics and case and ranks name matches first, but has no
  aliases or fuzzy matching.
- WP-30 and WP-31: transport, and how the start is entered.
- WP-33, WP-38–41 and WP-44–49: dashboard and app UI, and place content. These are inputs for the app design.

## 20. Ideas for v1.1+

The plan of record is §12 of the UX test report ("Предлагаемый порядок работ"):

| Stage | Theme | Main items |
|---|---|---|
| 1 | "stop lying" | candidate quality, closed places, duplicates, the start-as-slot fix, museums not on the way, on-the-way limits, realistic dwell, window overruns, unknown hours, message causes |
| 2 | "time and goal" | meal windows and anchors, repeated slots, spreading over the window, must-visit-first planning, short windows, reserve, A → B, leg limits, breaks, waits, kitchen hours |
| 3 | "clarity and control" | variant labels and diversity, editor replace / re-optimise / undo, place card, search |
| 4 | new features | scenarios and presets, live mode, multi-day plans, transport, districts, weather, filters, sharing |

Where each algorithmic idea would hook into the code:

| Idea | Hook | Notes |
|---|---|---|
| Type allow lists, word-boundary keywords, a global deny list (casino, lodging, services), rating floor (WP-02, 03, 11) | `slots:ACTIVITY_TYPES`, `candidates:slot_candidates` steps 1–3 | keep the relaxation rule, or report "nothing suitable nearby" |
| Exclude places within ~120 m of the start (WP-06) | `candidates:slot_candidates` step 8 | — |
| Duplicate clusters (WP-05) | bundle build + distinctness by cluster in `_solve_slots` / `_fill_extras` | — |
| On-the-way detour cap, count cap, type diversity, no ticketed types (WP-24, 25, 07) | `core:_fill_extras` | e.g. ≤ 5–7 min added walking per insert, about 1 per 45–60 min |
| Eligibility on the un-penalised interest (D8) | `core:plan_variants` / `_fill_extras` | — |
| Consistent return-leg cap (D3) | `core:_solve_slots` final states or `core:_route_minutes` | product decision on the cap |
| Must-visit-first, time-window-aware insertion with idle time; fit the window afterwards (WP-15, D4, D5, WP-34) | `core:plan_walk`, `core:best_insertion` | a pinned stop that cannot be open should not poison strict checks |
| Meal time windows as a soft penalty in the beam cost; free time; time anchors (WP-12, 14, 22, 37) | `core:_solve_slots` cost, `core:_route_minutes` | the biggest change: the solver currently has no notion of target times |
| Trim on-the-way stops after street routing and after edits; a 1-min tolerance (WP-19, 26) | after `core:_assemble_plan`; `pipeline:schedule` | — |
| End point A → B (WP-27) | `back_to` in `core:_solve_slots`, `core:_route_minutes`, `core:_assemble_plan` | a loop is the case A = B |
| Typical hours by type for unknown hours; sunset (WP-16) | catalog / bundle build + `core:visit_wait` callers | — |
| Dominance pruning by (used set, last place) (D7) | `core:_solve_slots` | — |
| Speed (PATCH, output bit-identical): memoise `walk_minutes` per pair, pre-merge hours per candidate (1.7–1.8× measured in research), recompute only the suffix in `_fill_extras`, vectorise the `open_within` prefilter, route variants in parallel | `core`, `candidates` | must keep the float operation order |
| Explanations: reasons for dropped slots and hours conflicts, exposing `taste_pct` / `similar_to` (WP-42, 43) | `core:plan_walk` (return reasons), `present` | — |

Guardrails for every change:
- Plans change, so it is a MINOR release: new golden outputs, a CHANGELOG entry, and new `plan_id`s (§18).
- Keep the optimizer on the estimate, unless making plans depend on the router is a deliberate product decision
  (§14.1).
- Re-run the research parity tools when touching interest: `test/test_walk_interest_parity.py` in the research
  repo.
