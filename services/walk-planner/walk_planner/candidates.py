"""Candidate selection of the Walk Planner: which places the route solver may choose from.

Three kinds of candidates feed ``core.plan_variants``:

  * slot candidates   — per requested activity slot, the top-K places by interest in the search
    radius plus the K best "near the start" (interest traded against walking minutes), after the
    type / AI precision filters (relaxed when too strict), the business-status filter and an
    opening-hours prefilter over the whole window (``slot_candidates``);
  * extra candidates  — optional "on the way" stops that fill a roomy window: more of the requested
    non-food types (parks and squares for style "scenic"), top-30 each (``extra_candidates``);
  * place candidates  — a place the user named (must-visit at build time, "add place" in the
    editor), built from its catalog row, with the business-status policy applied
    (``place_candidate``): ``closed_forever`` is never routable; ``temporarily_closed`` is allowed
    but flagged (and needs ``allow_temporarily_closed`` for "add").

This is an exact extraction of the dashboard's ``_walk_slot_candidates`` / ``_walk_row_candidate`` /
``_walk_place_dwell`` and the extras loop of ``_walk_build`` (dashboard_app.py L4256-4333,
L4611-4624, L4676-4688): the pandas operations are kept as they were (default ``sort_values`` kind,
``nsmallest(keep="first")``, concat + first-occurrence de-duplication, the Python-loop haversine),
because candidate order breaks ties in the solver. Only the business-status policy of place
candidates is new (v1 bug fix).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import pandas as pd

from .core import DEFAULT_DETOUR, DEFAULT_WALK_KMH, Candidate, dwell_for, estimate_dwell_min, haversine_km, open_within
from .slots import (
    ACTIVITY_TYPES,
    CLOSED_STATUS,
    EXTRA_K,
    FALLBACK_EXTRA_ACTIVITY,
    LEGACY_GROUP,
    MIN_FILTERED,
    NEAR_MIN_REVIEWS,
    NEAR_WEIGHT,
    NON_VENUE_RE,
    SCENIC_EXTRA_ACTIVITIES,
)

if TYPE_CHECKING:  # pragma: no cover
    from .catalog import CityCatalog

__all__ = [
    "place_dwell", "row_candidate", "distances_from", "slot_candidates", "extra_activities", "ExtraCandidates",
    "extra_candidates", "PlaceCandidate", "place_candidate",
]


# --------------------------------------------------------------------------- #
# One catalog row -> visit length / a user-chosen stop
# --------------------------------------------------------------------------- #
def place_dwell(row, theme) -> float:
    """Visit length for this catalog row (type, size by review count, 'brief' in the description) —
    `dashboard_app._walk_place_dwell`."""
    return estimate_dwell_min(theme, str(row.get("primary_type") or "") or None, row.get("google_user_rating_count"),
                              str(row.get("ai_place_type_summary") or ""), str(row.get("ai_card_summary") or ""))


def row_candidate(row, hours, pinned: bool = True) -> Candidate:
    """A catalog row as a route stop the user chose (must-visit / added by hand) —
    `dashboard_app._walk_row_candidate`: no slot (-1), the place's OWN theme (else theme_group) for
    the visit length (not style-scaled), interest 0, pinned."""
    theme = str(row.get("theme") or row.get("theme_group") or "")
    ptype = str(row.get("primary_type") or "")
    return Candidate(place_id=str(row["place_id"]), name=str(row.get("name") or row["place_id"]),
                     lat=float(row["latitude"]), lon=float(row["longitude"]), slot=-1, theme=theme,
                     subtype=ptype or None, interest=0.0, dwell_min=place_dwell(row, theme),
                     open_hours=hours, pinned=pinned)


def distances_from(rows: pd.DataFrame, point: tuple[float, float]) -> pd.Series:
    """Great-circle km from `point` (lat, lon) to every row, index-aligned (the page's Python loop)."""
    return pd.Series([haversine_km(point[0], point[1], float(la), float(lo))
                      for la, lo in zip(rows["latitude"], rows["longitude"])], index=rows.index)


# --------------------------------------------------------------------------- #
# Slot candidates
# --------------------------------------------------------------------------- #
def slot_candidates(rows: pd.DataFrame, interest: dict, slot_idx: int, activity: str,
                    start: Optional[tuple[float, float]], radius_km: Optional[float], top_k: int = 8,
                    window: Optional[tuple[float, float]] = None, known_hours_only: bool = False,
                    dwell_override: Optional[float] = None, *, text: Optional[pd.Series] = None,
                    dist: Optional[pd.Series] = None) -> list[Candidate]:
    """Candidates of one activity slot (`dashboard_app._walk_slot_candidates`, verbatim).

    rows      : the prepared city rows (``CityCatalog.rows``).
    interest  : place_id -> interest in [0, 1] (missing = 0).
    slot_idx  : the `Candidate.slot` to stamp (the slot's index; -1 for extras of an unrequested type).
    activity  : activity code (``slots.ACTIVITY_TYPES``).
    start     : centre of the search area (lat, lon); radius_km limits it (truthy only).
    window    : (from, to) week minutes: places closed for the whole window are dropped (the solver
                then checks each exact visit time); `known_hours_only` also drops unknown hours.
    dwell_override : the user's visit length for this slot (fixed, not re-estimated or rescaled).
    text / dist : optional precomputed ``CityCatalog.text_series()`` / ``distances_from(rows, start)``
                (same values; saves recomputing them per slot).

    Membership: the slot's catalog themes when the city has any of them, else keyword substrings
    of primary_type + AI type + name gated by theme_group. Precision filters (type allow/deny, AI
    non-venue) are dropped together when they leave fewer than MIN_FILTERED places (counted
    before the status / hours filters). Never a ``closed_forever`` / ``temporarily_closed`` place.
    Result: the top-K by interest, then (with a start) the top-K by ``walk minutes − NEAR_WEIGHT ·
    interest`` among places with >= NEAR_MIN_REVIEWS reviews; first occurrence kept."""
    spec = ACTIVITY_TYPES[activity]
    group, themes, keywords, dwell_theme = spec.group, spec.themes, spec.keywords, spec.dwell_key
    has_theme = "theme" in rows.columns and themes and rows["theme"].astype(str).isin(themes).any()
    if has_theme:
        # all-theme catalog: the slot IS the theme, no keyword guessing
        mask = rows["theme"].astype(str).isin(themes)
    else:
        if text is None:
            from .catalog import text_series
            text = text_series(rows)
        mask = text.apply(lambda t: any(k in t for k in keywords))
        # theme gate: apply only when the catalog actually has this theme_group, so a food/drink
        # venue can't fill a sight/park/market slot on an incidental keyword.
        if "theme_group" in rows.columns:
            tg = rows["theme_group"].astype(str)
            for g in (group, LEGACY_GROUP.get(group, "")):
                if g and (tg == g).any():
                    mask = mask & (tg == g)
                    break
    d = None
    if start is not None:
        d = dist if dist is not None else distances_from(rows, start)
        if radius_km:
            mask = mask & (d <= float(radius_km))
    # precision filters on top of the theme (Google type + AI place type); relaxed if too strict
    strict = mask.copy()
    ptype = rows["primary_type"].fillna("").astype(str).str.lower() if "primary_type" in rows.columns else None
    if ptype is not None and spec.type_allow:
        strict = strict & ptype.isin(spec.type_allow)
    if ptype is not None and spec.type_deny:
        strict = strict & ~ptype.isin(spec.type_deny)
    if spec.ai_deny and "ai_place_type_summary" in rows.columns:
        strict = strict & ~rows["ai_place_type_summary"].fillna("").astype(str).str.contains(NON_VENUE_RE)
    if int(strict.sum()) >= MIN_FILTERED:
        mask = strict
    # availability (hard): never a place Google lists as closed; nothing shut for the whole window
    if "business_status" in rows.columns:
        mask = mask & ~rows["business_status"].fillna("").astype(str).isin(CLOSED_STATUS)
    if "wp_hours" in rows.columns:
        hours = rows["wp_hours"]
        if known_hours_only:
            mask = mask & hours.map(lambda h: h is not None)
        if window is not None:
            dwell = float(dwell_override) if dwell_override else dwell_for(dwell_theme)
            mask = mask & hours.map(lambda h: open_within(h, window[0], window[1], dwell))
    sub = rows[mask].copy()
    if sub.empty:
        return []
    sub["wp_interest"] = sub["place_id"].map(interest).fillna(0.0)
    top = sub.sort_values("wp_interest", ascending=False).head(int(top_k))
    if d is not None:
        # Plus the best places NEAR the start, trading interest for walking minutes the way the
        # route solver does. Top-by-interest alone, over a large area, hands the solver only
        # famous places scattered across the city; with both lists it can choose.
        pool = sub
        if "google_user_rating_count" in sub.columns:
            n_rev = pd.to_numeric(sub["google_user_rating_count"], errors="coerce").fillna(0)
            pool = sub[n_rev >= NEAR_MIN_REVIEWS]
        walk_min = d[pool.index] * DEFAULT_DETOUR / DEFAULT_WALK_KMH * 60.0
        near = pool.assign(_c=walk_min - NEAR_WEIGHT * pool["wp_interest"]).nsmallest(int(top_k), "_c")
        top = pd.concat([top, near.drop(columns="_c")])
        top = top[~top["place_id"].duplicated()]
    out = []
    for _, r in top.iterrows():
        out.append(Candidate(
            place_id=str(r["place_id"]), name=str(r.get("name", r["place_id"])),
            lat=float(r["latitude"]), lon=float(r["longitude"]),
            slot=slot_idx, theme=dwell_theme, subtype=str(r.get("primary_type") or "") or None,
            interest=float(r["wp_interest"]), open_hours=r.get("wp_hours"),
            dwell_min=float(dwell_override) if dwell_override else place_dwell(r, dwell_theme),
            dwell_fixed=bool(dwell_override),
        ))
    return out


# --------------------------------------------------------------------------- #
# Extra ("on the way") candidates
# --------------------------------------------------------------------------- #
def extra_activities(slot_codes: list[str], style: str) -> list[str]:
    """Activity types the optional stops come from: parks & squares for "scenic"; otherwise more of
    the requested non-food types (no extra meals), or sights when every slot is food / drink.
    De-duplicated in order."""
    types = (list(SCENIC_EXTRA_ACTIVITIES) if style == "scenic" else
             [a for a in slot_codes if ACTIVITY_TYPES[a].group != "food_drink"] or [FALLBACK_EXTRA_ACTIVITY])
    return list(dict.fromkeys(types))


@dataclass
class ExtraCandidates:
    """Optional stops of one build: candidates (``extra=True``) in pool order, the activity whose
    pool produced each one (parallel list), and place_id -> the FIRST such activity (the label
    the route shows; the dashboard's ``extra_label``)."""

    candidates: list[Candidate]
    activities: list[str]
    activity_of: dict[str, str]


def extra_candidates(rows: pd.DataFrame, interest: dict, slot_codes: list[str], style: str, fill: bool,
                     area: tuple[float, float], search_km: float, window: Optional[tuple[float, float]],
                     known_hours_only: bool = False, *, text: Optional[pd.Series] = None,
                     dist: Optional[pd.Series] = None) -> ExtraCandidates:
    """The "on the way" pools (the extras loop of `dashboard_app._walk_build`): per extra activity,
    `slot_candidates` with top_k=EXTRA_K and no dwell override, flagged ``extra``; their slot is the
    activity's index among the requested slots, or -1. Nothing when `fill` is off."""
    cands: list[Candidate] = []
    acts: list[str] = []
    label: dict[str, str] = {}
    for act in (extra_activities(slot_codes, style) if fill else ()):
        idx = slot_codes.index(act) if act in slot_codes else -1
        for c in slot_candidates(rows, interest, idx, act, area, search_km, top_k=EXTRA_K, window=window,
                                 known_hours_only=known_hours_only, text=text, dist=dist):
            c.extra = True
            cands.append(c)
            acts.append(act)
            label.setdefault(c.place_id, act)
    return ExtraCandidates(candidates=cands, activities=acts, activity_of=label)


# --------------------------------------------------------------------------- #
# A place the user named (must-visit / add) + business-status policy
# --------------------------------------------------------------------------- #
@dataclass
class PlaceCandidate:
    """Outcome of turning a user-named place into a route stop.

    candidate       : the pinned Candidate, or None when rejected.
    business_status : "" | "closed_forever" | "temporarily_closed" (catalog fact).
    reason          : error code when rejected — "unknown_place", "place_closed_forever" or
                      "place_temporarily_closed" (only without `allow_temporarily_closed`).
    """

    place_id: str
    candidate: Optional[Candidate]
    business_status: str = ""
    name: Optional[str] = None
    reason: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.candidate is not None

    @property
    def flagged(self) -> bool:
        """Accepted although Google lists it as temporarily closed (the stop carries a warning)."""
        return self.accepted and self.business_status == "temporarily_closed"


def place_candidate(catalog: "CityCatalog", place_id, allow_temporarily_closed: bool = True,
                    pinned: bool = True) -> PlaceCandidate:
    """A catalog place as a pinned route stop (must-visit at build time, "add place" in edits).

    Status policy (v1): ``closed_forever`` -> rejected ("place_closed_forever"); never routable.
    ``temporarily_closed`` -> accepted and flagged when `allow_temporarily_closed` (must-visits
    always allow it), else rejected ("place_temporarily_closed") so the client can confirm with the
    user. Unknown place -> rejected ("unknown_place"). The candidate itself is
    `row_candidate(row, hours)` — the dashboard's, unchanged."""
    pid = str(place_id)
    if not catalog.has(pid):
        return PlaceCandidate(place_id=pid, candidate=None, reason="unknown_place")
    row = catalog.row(pid)
    status = catalog.status_of(pid)
    name = str(row.get("name") or pid)
    if status == "closed_forever":
        return PlaceCandidate(place_id=pid, candidate=None, business_status=status, name=name,
                              reason="place_closed_forever")
    if status == "temporarily_closed" and not allow_temporarily_closed:
        return PlaceCandidate(place_id=pid, candidate=None, business_status=status, name=name,
                              reason="place_temporarily_closed")
    cand = row_candidate(row, catalog.hours_of(pid), pinned=pinned)
    return PlaceCandidate(place_id=pid, candidate=cand, business_status=status, name=name)
