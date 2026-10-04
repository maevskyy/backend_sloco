#!/usr/bin/env python3
"""Capture the GOLDEN BASELINE (v0) of the Walk Planner, before the planner is extracted from the
Streamlit dashboard into a package + microservice.

The baseline is the ground truth the new code must reproduce (except documented bug fixes, see
../baseline_v0/README.md). It is produced by driving the dashboard's OWN pipeline headlessly:

  * ``dashboard_app._walk_build`` (candidates per slot, extras, must-visits, ``plan_variants``) is
    called exactly like ``page_walk_planner`` calls it, with the form values derived from
    ``../scenarios.json`` (API-style codes -> the dashboard's Russian labels);
  * the catalog rows are built exactly like ``page_walk_planner`` builds them from
    ``rec.locations`` (``pd.read_csv`` with default arguments + ``place_id.astype(str)`` is
    identical to ``LocationRecommender.from_artifacts(...).locations`` for every column the planner
    reads, row order included -- verified when this baseline was captured);
  * the route editor is driven through the dashboard's own ``_walk_remove_place`` /
    ``_walk_add_place`` (``st.session_state`` replaced by a dict); drag-and-drop moves / restores
    and the minutes editor are replicated line by line from ``_walk_editor``;
  * what ``_walk_render_result`` would SHOW (metrics, captions, warnings, stop cards) is
    replicated without Streamlit, using the dashboard's own helpers (``_walk_clock``,
    ``_walk_end_label``, ``_walk_hours_label``, ``visit_wait``); navigation links come from
    ``walk_planner.navigation_links`` with the Google place ids computed like the page does.

Hermetic: ``ORS_API_KEY`` is forced to "" before the repo is imported (``_walk_provider`` reads it
per call -> straight-line ``RoutingProvider`` estimate), no ``.env`` is loaded, every socket
connect / DNS lookup raises, and no bytecode is written. Nothing in the repo is modified.

Usage (from anywhere):
    PYTHONDONTWRITEBYTECODE=1 /path/to/venv/bin/python services/walk_planner/golden/tools/capture_baseline.py
        [--scenarios PATH] [--out DIR] [--only S01_default,S05_free]

Writes ``<out>/<scenario_id>.json`` (one digest per scenario) and ``<out>/_meta.json``.
Every float is rounded to 6 decimals; list orders are kept exactly as produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import platform
import socket
import subprocess
import sys
import time
import warnings
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

# --------------------------------------------------------------------------------------------- #
# Hermetic setup -- BEFORE anything from the repo is imported
# --------------------------------------------------------------------------------------------- #
os.environ["ORS_API_KEY"] = ""          # dashboard_app._walk_provider() reads it per call -> estimate provider
sys.dont_write_bytecode = True          # never write __pycache__ into the repo


def _network_disabled(*_args, **_kwargs):
    raise RuntimeError("network access is disabled while capturing the Walk Planner golden baseline")


socket.socket.connect = _network_disabled
socket.socket.connect_ex = _network_disabled
socket.create_connection = _network_disabled
socket.getaddrinfo = _network_disabled

HERE = Path(__file__).resolve()
GOLDEN_DIR = HERE.parents[1]                       # services/walk_planner/golden
REPO = HERE.parents[4]                             # sloco_recommendation_system
CATALOG_CSV = REPO / "recommendation_system" / "ai_location_recommender" / "data" / "locations_bucharest_all.csv"
DEFAULT_SCENARIOS = GOLDEN_DIR / "scenarios.json"
DEFAULT_OUT = GOLDEN_DIR / "baseline_v0"

sys.path.insert(0, str(REPO))
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)                   # streamlit "missing ScriptRunContext" noise

import numpy as np                                 # noqa: E402
import pandas as pd                                # noqa: E402
import streamlit                                   # noqa: E402

import recommendation_system.ai_location_recommender.dashboard_app as d   # noqa: E402
import walk_planner as wp   # noqa: E402  (moved to services/walk_planner; path set by the package init)

# --------------------------------------------------------------------------------------------- #
# API codes <-> dashboard labels
# --------------------------------------------------------------------------------------------- #
ACTIVITY_LABEL = {
    "sight": "Достопримечательность", "coffee": "Кофе", "food": "Еда / ресторан", "bar": "Бар / напитки",
    "park": "Парк / природа", "market": "Рынок / площадь", "entertainment": "Развлечение", "shopping": "Шопинг",
}
STYLE_LABEL = {"max": "Максимум мест", "chill": "Размеренный", "scenic": "Живописный"}
SHAPE_LABEL = {"loop": "Петля", "one_way": "В одну сторону", "free": "По району"}
# page_walk_planner: style = {...}[style_label], shape = {...}[shape_label]
_PAGE_STYLE = {"Максимум мест": "max", "Размеренный": "chill", "Живописный": "scenic"}
_PAGE_SHAPE = {"В одну сторону": "one_way", "Петля": "loop", "По району": "free"}
assert list(ACTIVITY_LABEL.values()) == list(d.WALK_ACTIVITY_TYPES), "activity labels drifted from the dashboard"
assert all(_PAGE_STYLE[STYLE_LABEL[k]] == k for k in STYLE_LABEL)
assert all(_PAGE_SHAPE[SHAPE_LABEL[k]] == k for k in SHAPE_LABEL)

EMPTY_ROUTE_INFO = "В маршруте не осталось мест — перетащите что-нибудь обратно или добавьте место."

# --------------------------------------------------------------------------------------------- #
# Non-invasive capture: Streamlit calls + what _walk_build hands to the planner
# --------------------------------------------------------------------------------------------- #
_CAP: dict = {}


def _st_recorder(kind):
    def _record(body="", *_args, **_kwargs):
        _CAP.setdefault("st_calls", []).append((kind, str(body)))
    return _record


for _kind in ("error", "warning", "info", "caption"):
    setattr(d.st, _kind, _st_recorder(_kind))

_orig_slot_candidates = d._walk_slot_candidates
_orig_plan_variants = d.wp_plan_variants


def _capturing_slot_candidates(*args, **kwargs):
    out = _orig_slot_candidates(*args, **kwargs)
    # call signature in _walk_build: (city_rows, interest, idx, activity, area, search_km, top_k=..., ...)
    _CAP.setdefault("slot_calls", []).append({"activity": args[3], "area": args[4], "search_km": args[5],
                                               "top_k": kwargs.get("top_k"), "out": out})
    return out


def _capturing_plan_variants(req, n=3):
    _CAP["req"], _CAP["n"] = req, n
    return _orig_plan_variants(req, n)


d._walk_slot_candidates = _capturing_slot_candidates
d.wp_plan_variants = _capturing_plan_variants


# --------------------------------------------------------------------------------------------- #
# Catalog (== rec.locations for every column the planner reads) and the page's city rows
# --------------------------------------------------------------------------------------------- #
def load_catalog(path: Path = CATALOG_CSV) -> pd.DataFrame:
    cat = pd.read_csv(path)                              # LocationRecommender.from_artifacts
    cat["place_id"] = cat["place_id"].astype(str)        # LocationRecommender._prepare_locations
    return cat


def build_city_rows(locs: pd.DataFrame, city: str):
    """page_walk_planner, verbatim (minus widgets)."""
    if "city" in locs.columns and locs["city"].notna().any():
        city_rows = locs[locs["city"].astype(str) == city]
    else:
        city_rows = locs
    city_rows = city_rows.dropna(subset=["latitude", "longitude"]).copy()
    if "opening_hours" in city_rows.columns:
        city_rows["wp_hours"] = city_rows["opening_hours"].map(d._walk_parse_hours)
    center = (float(city_rows["latitude"].astype(float).mean()), float(city_rows["longitude"].astype(float).mean()))
    return city_rows, center


# --------------------------------------------------------------------------------------------- #
# Scenario -> the page's form values
# --------------------------------------------------------------------------------------------- #
def _hm(text: str) -> tuple[int, int]:
    h, m = (int(x) for x in text.split(":"))
    assert 0 <= h < 24 and 0 <= m < 60, text
    return h, m


def resolve_inputs(sc: dict, locs: pd.DataFrame, city_rows: pd.DataFrame, center) -> dict:
    day = date.fromisoformat(sc["date"])
    sh, sm = _hm(sc["start_time"])
    assert (sh * 60 + sm) % 15 == 0, "time_input step is 15 min"
    t_start = dtime(sh, sm)
    a = sh * 60 + sm
    eh, em = _hm(sc["end_time"])
    end_clock = eh * 60 + em
    want_next = bool(sc.get("end_day_offset", 0))
    end_opts = list(range(a + 15, a + 24 * 60 + 1, 15))          # page_walk_planner's "Закончить к" options
    matches = [v for v in end_opts if v % 1440 == end_clock and (v >= 1440) == want_next]
    if not matches:
        raise ValueError(f"{sc['id']}: end {sc['end_time']} (+{int(want_next)}) is not a 'Закончить к' option")
    end_abs = matches[0]
    budget_min, next_day = end_abs - a, end_abs >= 24 * 60
    t0 = day.weekday() * 1440 + t_start.hour * 60 + t_start.minute

    shape, style = sc["shape"], sc["style"]
    style_label = STYLE_LABEL[style]
    start = None
    if shape != "free":
        s = sc["start"]
        if s == "city_center":
            start = center
        elif isinstance(s, dict) and "place_id" in s:          # "Выбрать место": the row in rec.locations
            row = locs[locs["place_id"] == str(s["place_id"])]
            start = (float(row.iloc[0]["latitude"]), float(row.iloc[0]["longitude"])) if not row.empty else center
        elif isinstance(s, dict) and {"lat", "lon"} <= set(s):  # "Координаты"
            start = (float(s["lat"]), float(s["lon"]))
        else:
            raise ValueError(f"{sc['id']}: bad start {s!r}")

    slot_labels = [ACTIVITY_LABEL[x["activity"]] for x in sc["slots"]]
    dwell_by_slot = {ACTIVITY_LABEL[x["activity"]]: int(x["dwell_min"]) for x in sc["slots"]
                     if x.get("dwell_min") is not None}
    city_ids = set(city_rows["place_id"].astype(str))
    must_ids = [str(p) for p in sc.get("must_visit_place_ids", [])]
    unknown = [p for p in must_ids if p not in city_ids]
    if unknown:
        raise ValueError(f"{sc['id']}: must-visit ids not in the city catalog: {unknown}")
    return dict(day=day, t_start=t_start, end_abs=end_abs, budget_min=budget_min, next_day=next_day, t0=t0,
                shape=shape, style=style, style_label=style_label, start=start, slot_labels=slot_labels,
                dwell_by_slot=dwell_by_slot, must_ids=must_ids, radius_km=float(sc["radius_km"]),
                top_k=int(sc["top_k"]), n_variants=int(sc["variants"]), fill=bool(sc["fill_window"]),
                known_hours_only=bool(sc["known_hours_only"]))


# --------------------------------------------------------------------------------------------- #
# Digests
# --------------------------------------------------------------------------------------------- #
def plan_digest(plan, hours_map: dict, t0: int) -> dict:
    return {
        "stops": [{
            "order": s.order, "place_id": s.place_id, "name": s.name, "slot": s.slot, "theme": s.theme,
            "extra": s.extra, "pinned": s.pinned, "interest": s.interest, "dwell_min": s.dwell_min,
            "arrival_min": s.arrival_min, "wait_min": s.wait_min, "depart_min": s.depart_min, "hours_ok": s.hours_ok,
            "hours_label_ru": d._walk_hours_label(hours_map.get(s.place_id), t0 + s.arrival_min + s.wait_min),
        } for s in plan.stops],
        "segments": [{"from_order": g.from_order, "to_order": g.to_order, "walk_min": g.walk_min,
                      "distance_km": g.distance_km, "n_geom": len(g.geometry)} for g in plan.segments],
        "total_time_min": plan.total_time_min, "total_walk_min": plan.total_walk_min,
        "total_dwell_min": plan.total_dwell_min, "total_wait_min": plan.total_wait_min,
        "total_distance_km": plan.total_distance_km, "over_budget": plan.over_budget,
        "dropped_slots": list(plan.dropped_slots), "note": plan.note,
    }


def render_digest(res: dict, sel: int) -> dict:
    """What _walk_render_result shows for variant `sel` (text only; map / photos / editor widgets skipped)."""
    start, shape, t_start, t0 = res["start"], res["shape"], res["t_start"], res["t0"]
    end_abs, budget_min, next_day, has_hours = res["end_abs"], res["budget_min"], res["next_day"], res["has_hours"]
    slot_labels, extra_label, style_label = res["slot_labels"], res["extra_label"], res["style_label"]
    t_end = d._walk_end_label(end_abs)

    def clock(minutes):
        return d._walk_clock(t_start, minutes)

    variants = res["variants"]
    v = variants[sel]
    if v["plan"] is None:
        v["plan"] = d.wp_plan_sequence(v["seq"], d._walk_seq_request(res, v["seq"]))
    plan = v["plan"]

    variants_caption = None
    if len(variants) > 1:
        def vsum(i):
            p = variants[i]["plan"] or variants[i]["optimal_plan"]
            n = len(p.stops)
            places = "место" if n % 10 == 1 and n % 100 != 11 else (
                "места" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "мест")
            txt = (f"{n} {places} · {p.total_distance_km:.1f} км · до {clock(p.total_time_min)}"
                   + (" · ✏️ изменён" if variants[i]["manual"] else ""))
            return f"**Вариант {i + 1}**: {txt}" if i == sel else f"Вариант {i + 1}: {txt}"
        variants_caption = "  ·  ".join(vsum(i) for i in range(len(variants)))

    slot_name = {i: lbl for i, lbl in enumerate(slot_labels)}

    def kind_of(x):
        if x.pinned:
            return "📍 ваше место"
        if x.extra:
            return f"{extra_label.get(x.place_id, x.theme)} · по пути, можно пройти мимо"
        return slot_name.get(x.slot, x.theme)

    out = {"manual": v["manual"], "variants_caption": variants_caption, "metric_stops": None,
           "metric_finish_label": None, "metric_finish": None, "metric_delta": None, "metric_walk": None,
           "metric_window": None, "captions": [], "warnings": [], "infos": [], "stop_lines": []}
    if plan.stops:
        slack = budget_min - plan.total_time_min
        n_extra = sum(s.extra for s in plan.stops)
        n_pin = sum(s.pinned for s in plan.stops)
        n_core = len(plan.stops) - n_extra - n_pin
        out["metric_stops"] = (f"{n_core} из {len(slot_labels)}" + (f" + {n_extra}" if n_extra else "")
                               + (f" + 📍{n_pin}" if n_pin else ""))
        out["metric_finish_label"] = "Финиш" if shape != "loop" else "Возвращение"
        out["metric_finish"] = clock(plan.total_time_min)
        out["metric_delta"] = (f"+{slack:.0f} мин запаса" if slack >= 0 else f"-{-slack:.0f} мин, не успеваю")
        wm = int(round(plan.total_walk_min))
        out["metric_walk"] = f"{plan.total_distance_km:.1f} км · {wm // 60}:{wm % 60:02d}"
        out["metric_window"] = f"{t_start:%H:%M}–{t_end.split(' ')[0]}" + ("⁺¹" if next_day else "")
        if n_extra and not v["manual"]:
            out["captions"].append(
                f"Стиль «{style_label}»: по пути добавлено мест — {n_extra}, чтобы занять окно "
                f"{t_start:%H:%M}–{t_end}. Только выбранные слоты — выключите «Добавлять места по пути».")
        if v["dropped"] and not v["manual"]:
            out["warnings"].append("Не поместилось в окно: " + ", ".join(v["dropped"])
                                   + f". Остальное уложено до {t_end}; чтобы вернуть слоты, раздвиньте окно.")
        if plan.over_budget:
            out["warnings"].append(f"Маршрут займёт ~{plan.total_time_min:.0f} мин, а окно {budget_min} мин: "
                                   f"финиш в {clock(plan.total_time_min)}, позже {t_end}.")
        for s in plan.stops:
            if s.hours_ok is False:
                h = res["hours"].get(s.place_id)
                open_on_arrival = d.wp_visit_wait(h, t0 + s.arrival_min, 0.0, 0.0) is not None
                when = (f"закрывается раньше, чем закончится визит {clock(s.arrival_min)}–{clock(s.depart_min)}"
                        if open_on_arrival else f"в {clock(s.arrival_min)} по графику закрыто")
                out["warnings"].append(f"⚠️ «{s.name}» — {when} ({d._walk_hours_label(h, t0 + s.arrival_min)}). "
                                       "Маршрут всё равно построен: оставьте, если знаете, что сегодня работает дольше.")
        for s in plan.stops:   # the stop cards (markdown emphasis ** / _ dropped, one entry per line)
            lines = [f"{s.order + 1}. {s.name} — {kind_of(s)}",
                     f"{clock(s.arrival_min + s.wait_min)}–{clock(s.depart_min)} · осмотр {s.dwell_min:.0f} мин"
                     + (f" · ⏳ приходим в {clock(s.arrival_min)}, ждём открытия {s.wait_min:.0f} мин"
                        if s.wait_min >= 1 else "")]
            if has_hours:
                lines.append(f"🕒 {d._walk_hours_label(res['hours'].get(s.place_id), t0 + s.arrival_min + s.wait_min)}")
            if s.hours_ok is False:
                lines.append("⚠️ по графику в это время может быть закрыто — маршрут построен как есть")
            out["stop_lines"].append(lines)
    else:
        out["infos"].append(EMPTY_ROUTE_INFO)
    return out


def nav_digest(res: dict, plan, locs: pd.DataFrame):
    """_walk_render_result's navigation block (None when the route is empty: the page returns before it)."""
    if not plan.stops:
        return None
    gpid = {}
    if "google_place_id" in locs.columns:
        sub = locs[locs["place_id"].isin({s.place_id for s in plan.stops})]
        gpid = {str(r["place_id"]): str(r["google_place_id"]) for _, r in sub.iterrows()
                if isinstance(r.get("google_place_id"), str) and r["google_place_id"].startswith("ChIJ")}
    return d.wp_navigation_links(plan, start=res["start"], shape=res["shape"], place_ids=gpid)


def cand_row(c) -> list:
    return [c.place_id, c.slot, c.interest, c.dwell_min, c.dwell_fixed, c.theme, c.subtype]


def request_digest(req, n) -> dict:
    return {
        "mode": req.mode, "n_slots": req.n_slots, "start": list(req.start) if req.start is not None else None,
        "shape": req.shape, "style": req.style, "time_budget_min": req.time_budget_min,
        "fit_budget": req.fit_budget, "start_week_min": req.start_week_min, "max_wait_min": req.max_wait_min,
        "fill_window": req.fill_window, "walk_weight": req.walk_weight, "max_leg_min": req.max_leg_min,
        "interest_weight": req.interest_weight, "max_stops": req.max_stops,
        "provider": f"{type(req.provider).__module__.rsplit('.', 1)[-1]}.{type(req.provider).__name__}"
                    f"(walk_kmh={req.provider.walk_kmh}, detour={req.provider.detour})",
        "n_candidates": len(req.candidates), "n_must_visit": len(req.must_visit), "n_variants_requested": n,
    }


# --------------------------------------------------------------------------------------------- #
# Route editor (variant 0): independent chains on fresh copies
# --------------------------------------------------------------------------------------------- #
def _fresh_result_copy(res: dict) -> dict:
    r = dict(res)
    r["hours"] = dict(res["hours"])
    r["variants"] = [dict(v, seq=list(v["seq"]), optimal=list(v["optimal"]), removed=list(v["removed"]))
                     for v in res["variants"]]
    r["sel"] = 0
    return r


def _rerender(r: dict, v: dict) -> None:
    """What the rerun after an edit does first: re-schedule the edited order (_walk_render_result)."""
    if v["plan"] is None:
        v["plan"] = d.wp_plan_sequence(v["seq"], d._walk_seq_request(r, v["seq"]))


def run_chain(res: dict, ops: list, city_rows: pd.DataFrame, locs: pd.DataFrame) -> dict:
    r = _fresh_result_copy(res)
    v = r["variants"][0]
    saved_state = d.st.session_state
    d.st.session_state = {"wp_result": r}           # _walk_variant() / _walk_add_place read & write it
    resolved = []
    try:
        for op in ops:
            kind = op["op"]
            seq_ids = [c.place_id for c in v["seq"]]
            done = dict(op)
            if kind == "move":                       # drag within «Маршрут» (_walk_editor / sort_items)
                n = len(v["seq"])
                f = op["from"] + n if op["from"] < 0 else op["from"]
                t = op["to"] + n if op["to"] < 0 else op["to"]
                done.update({"from": f, "to": t})
                applied = 0 <= f < n and 0 <= t < n
                if applied:
                    done["place_id"] = v["seq"][f].place_id
                    new_seq = list(v["seq"])
                    new_seq.insert(t, new_seq.pop(f))
                    new_removed = list(v["removed"])
                    applied = ([c.place_id for c in new_seq] != seq_ids
                               or [c.place_id for c in new_removed] != [c.place_id for c in v["removed"]])
                    if applied:
                        v.update(seq=new_seq, removed=new_removed, manual=True, plan=None, ver=v["ver"] + 1)
            elif kind == "remove":                   # «➖ Убрать место» -> the dashboard's own callback
                i = op["index"]
                done["place_id"] = v["seq"][i].place_id if 0 <= i < len(v["seq"]) else None
                ver = v["ver"]
                d._walk_remove_place(i)
                applied = v["ver"] != ver
            elif kind == "restore":                  # drag from «Не заходить» back into «Маршрут» at `to`
                ri, t = op["removed_index"], op["to"]
                applied = 0 <= ri < len(v["removed"]) and 0 <= t <= len(v["seq"])
                if applied:
                    item = v["removed"][ri]
                    done["place_id"] = item.place_id
                    new_seq = v["seq"][:t] + [item] + v["seq"][t:]
                    new_removed = v["removed"][:ri] + v["removed"][ri + 1:]
                    v.update(seq=new_seq, removed=new_removed, manual=True, plan=None, ver=v["ver"] + 1)
            elif kind == "add":                      # «➕ Добавить место из базы» -> the dashboard's own callback
                pid = str(op["place_id"])
                in_route = {c.place_id for c in v["seq"]}
                row = city_rows[city_rows["place_id"].astype(str) == pid]
                cand = None
                if not row.empty and pid not in in_route:
                    rr = row.iloc[0]
                    cand = d._walk_row_candidate(rr, rr.get("wp_hours") if "wp_hours" in row.columns else None)
                done["name"] = str(row.iloc[0]["name"]) if not row.empty else None
                if cand is None:                     # the button is disabled ("Это место уже в маршруте.")
                    applied = False
                    done["skipped"] = "already in route" if pid in in_route else "not in city catalog"
                else:
                    ver = v["ver"]
                    d._walk_add_place(cand)
                    applied = v["ver"] != ver
            elif kind == "set_dwell":                # «⏱ Сколько побыть на каждом месте» (st.data_editor)
                i, minutes = op["index"], op["dwell_min"]
                done["place_id"] = v["seq"][i].place_id
                shown = [int(round(c.dwell_min)) for c in v["seq"]]
                edited = list(shown)
                edited[i] = minutes
                new_min = [int(x) if pd.notna(x) else int(round(c.dwell_min)) for x, c in zip(edited, v["seq"])]
                applied = new_min != [int(round(c.dwell_min)) for c in v["seq"]]
                if applied:
                    v["seq"] = [d._dc_replace(c, dwell_min=float(m), dwell_fixed=True) for c, m in zip(v["seq"], new_min)]
                    v.update(manual=True, plan=None, ver=v["ver"] + 1)
            else:
                raise ValueError(f"unknown edit op {kind!r}")
            done["applied"] = bool(applied)
            resolved.append(done)
            _rerender(r, v)
    finally:
        d.st.session_state = saved_state
    plan = v["plan"]
    return {"ops": resolved, "manual": v["manual"], "removed": [c.place_id for c in v["removed"]],
            "plan": plan_digest(plan, r["hours"], r["t0"]), "render": render_digest(r, 0),
            "navigation": nav_digest(r, plan, locs)}


# --------------------------------------------------------------------------------------------- #
# One scenario
# --------------------------------------------------------------------------------------------- #
def capture_scenario(sc: dict, locs: pd.DataFrame, city_cache: dict) -> dict:
    city = sc["city"]
    if city not in city_cache:
        city_cache[city] = build_city_rows(locs, city)
    _, center = city_cache[city]
    city_rows = build_city_rows(locs, city)[0]      # fresh frame per scenario, like every page rerun
    x = resolve_inputs(sc, locs, city_rows, center)

    _CAP.clear()
    res = d._walk_build(None, None, city_rows, center, x["start"], x["shape"], x["style"], x["style_label"], x["fill"],
                        x["known_hours_only"], x["slot_labels"], [], x["radius_km"], x["top_k"], x["t_start"],
                        x["end_abs"], x["budget_min"], x["next_day"], x["t0"], True, x["must_ids"], x["n_variants"],
                        x["dwell_by_slot"])
    st_calls = _CAP.get("st_calls", [])
    errors = [t for k, t in st_calls if k == "error"]
    unexpected = [c for c in st_calls if c[0] != "error"]
    if unexpected or (res is None) != bool(errors) or len(errors) > 1:
        raise RuntimeError(f"{sc['id']}: unexpected Streamlit calls {st_calls!r}")

    calls = _CAP.get("slot_calls", [])
    req = _CAP.get("req")
    n_slot_calls = len(x["slot_labels"])
    slot_out = [c for call in calls[:n_slot_calls] for c in call["out"]]
    extra_calls = calls[n_slot_calls:]
    if req is not None:
        assert [id(c) for c in req.candidates] == [id(c) for c in slot_out] + [id(c) for call in extra_calls for c in call["out"]]
        assert not any(c.extra for c in slot_out) and all(c.extra for call in extra_calls for c in call["out"])
    if res is not None:
        assert [id(c) for c in res["all_cands"]] == [id(c) for c in slot_out]

    digest = {
        "scenario": sc,
        "inputs_resolved": {
            "start": list(x["start"]) if x["start"] is not None else None, "center": list(center),
            # exact values (repr strings): a 6-decimal start/centre shifts walk minutes in the 4th decimal
            "start_exact": [repr(float(v)) for v in x["start"]] if x["start"] is not None else None,
            "center_exact": [repr(float(v)) for v in center],
            "t_start": f"{x['t_start']:%H:%M}", "t0": x["t0"], "budget_min": x["budget_min"], "end_abs": x["end_abs"],
            "next_day": x["next_day"], "end_label_ru": d._walk_end_label(x["end_abs"]), "slot_labels": x["slot_labels"],
            "style_label": x["style_label"], "dwell_by_slot": x["dwell_by_slot"], "must_ids": x["must_ids"],
            "seed_ids": [],
        },
        "error": errors[0] if errors else None,
        "notes": [{"kind": k, "text": t} for k, t in res["notes"]] if res is not None else [],
        "search_km": res["search_km"] if res is not None else (calls[0]["search_km"] if calls else None),
        "area": list(res["area"]) if res is not None else (list(calls[0]["area"]) if calls else None),
        "request": request_digest(req, _CAP.get("n")) if req is not None else None,
        "candidates": {
            "slot": [cand_row(c) for c in slot_out],
            "extra": [[c.place_id, c.slot, c.interest, c.dwell_min, call["activity"]]
                      for call in extra_calls for c in call["out"]],
            "must": [cand_row(c) + [c.pinned] for c in req.must_visit] if req is not None else [],
        },
        "variants": [],
        "edits": {},
    }
    if res is None:
        return digest
    res["sel"] = 0
    for i, v in enumerate(res["variants"]):
        digest["variants"].append({
            "dropped": list(v["dropped"]),
            "plan": plan_digest(v["plan"], res["hours"], res["t0"]),
            "render": render_digest(res, i),
            "navigation": nav_digest(res, v["plan"], locs),
        })
    for chain in sc.get("edits", []):
        digest["edits"][chain["chain"]] = run_chain(res, chain["ops"], city_rows, locs)
    return digest


# --------------------------------------------------------------------------------------------- #
# Output: floats rounded to 6 decimals; one line per stop / segment / candidate for readable diffs
# --------------------------------------------------------------------------------------------- #
def norm(x):
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        if math.isnan(x) or math.isinf(x):
            raise ValueError("non-finite float in digest")
        r = round(x, 6)
        return 0.0 if r == 0 else r
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    raise TypeError(f"cannot serialise {type(x)}")


def _flat(v) -> bool:
    return not isinstance(v, (dict, list)) or (isinstance(v, list) and all(not isinstance(i, (dict, list)) for i in v))


def dumps(obj, indent: int = 0) -> str:
    pad = " " * (indent + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        if all(_flat(v) for v in obj.values()) and indent > 0:
            return json.dumps(obj, ensure_ascii=False)
        body = ",\n".join(f"{pad}{json.dumps(k, ensure_ascii=False)}: {dumps(v, indent + 1)}" for k, v in obj.items())
        return "{\n" + body + "\n" + " " * indent + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(not isinstance(i, dict) and _flat(i) for i in obj):
            if all(not isinstance(i, list) for i in obj):
                return json.dumps(obj, ensure_ascii=False)
        body = ",\n".join(f"{pad}{dumps(v, indent + 1)}" for v in obj)
        return "[\n" + body + "\n" + " " * indent + "]"
    return json.dumps(obj, ensure_ascii=False)


def write_json(path: Path, obj) -> None:
    text = dumps(norm(obj)) + "\n"
    assert json.loads(text) == norm(obj)
    path.write_text(text, encoding="utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    except Exception as exc:  # pragma: no cover
        return f"unavailable ({exc})"


META_NOTES = [
    "Produced by services/walk_planner/golden/tools/capture_baseline.py from services/walk_planner/golden/scenarios.json.",
    "Pipeline = dashboard_app._walk_build (called like page_walk_planner: rec=None, ctx=None, seed_ids=[] -> cold-start "
    "popularity interest, has_hours=True) + the page's render/navigation logic; route edits via _walk_remove_place / "
    "_walk_add_place and a line-by-line replica of _walk_editor's drag-and-drop and minutes editor.",
    "Catalog rows = pd.read_csv(locations_bucharest_all.csv) + place_id.astype(str); identical to "
    "LocationRecommender.from_artifacts(...).locations for every column the planner reads, row order included.",
    "Routing = straight-line estimate (walk_planner.RoutingProvider, 4.5 km/h x 1.35 detour): ORS_API_KEY forced to '', "
    "no .env loaded, sockets disabled during the capture.",
    "Edit chains are independent, each on a fresh copy of variant index 0; negative indices count from the end and are "
    "resolved against the sequence at the time of the op (resolved values + applied flag are in each digest's edits.*.ops).",
    "Floats rounded to 6 decimals (Python round, -0.0 -> 0.0); render strings embed the dashboard's own rounding "
    "(_walk_clock rounds minutes with round(), metrics use :.0f/:.1f -- both round-half-even). Compare floats with an "
    "absolute tolerance of ~2e-6, strings exactly.",
    "Feed the new code inputs_resolved.start_exact / center_exact (exact repr strings), not the 6-decimal start/center: "
    "a rounded centre shifts walk minutes in the 4th decimal. center = mean latitude/longitude of the city rows.",
    "candidates.slot / candidates.extra rows with TIED interest are ordered (and cut at top-K) by pandas' default "
    "quicksort in _walk_slot_candidates + catalog row order: with a stable sort (14/24 scenarios) or a shuffled catalog "
    "(10-14/24 over 3 seeds) the digests differ ONLY there (all affected rows have interest < 0.3 = EXTRA_MIN_INTEREST); "
    "plans, renders, navigation and edits are unchanged. Compare tied-interest candidate rows as sets.",
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    args = ap.parse_args(argv)

    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    ids = [s["id"] for s in scenarios]
    assert len(set(ids)) == len(ids), "duplicate scenario ids"
    only = {s for s in args.only.split(",") if s}
    if only - set(ids):
        raise SystemExit(f"unknown scenario ids: {sorted(only - set(ids))}")
    args.out.mkdir(parents=True, exist_ok=True)

    t_all = time.perf_counter()
    locs = load_catalog()
    city_cache: dict = {}
    n_chains = 0
    for sc in scenarios:
        if only and sc["id"] not in only:
            continue
        if sc.get("baseline_v0") is False:       # new in v1 (P01 / P02): the pre-refactor dashboard cannot produce it
            print(f"{sc['id']:24s}   skipped (\"baseline_v0\": false)", flush=True)
            continue
        t = time.perf_counter()
        digest = capture_scenario(sc, locs, city_cache)
        write_json(args.out / f"{sc['id']}.json", digest)
        n_chains += len(digest["edits"])
        stops = [len(v["plan"]["stops"]) for v in digest["variants"]]
        print(f"{sc['id']:24s} {time.perf_counter() - t:6.2f}s  variants={len(stops)} stops={stops} "
              f"edits={len(digest['edits'])} error={digest['error']!r}", flush=True)

    if not only:
        meta = {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_head": _git("rev-parse", "HEAD"),
            "git_tracked_changes": _git("status", "--porcelain", "--untracked-files=no") or "none",
            "catalog_sha256": _sha256(CATALOG_CSV),
            "catalog_path": str(CATALOG_CSV.relative_to(REPO)),
            "catalog_rows": int(len(locs)),
            "scenarios_sha256": _sha256(args.scenarios),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "streamlit": streamlit.__version__,
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "routing": "estimate",
            "routing_provider": "walk_planner.RoutingProvider(walk_kmh=4.5, detour=1.35); ORS_API_KEY=''; network disabled",
            "n_scenarios": len([s for s in scenarios if s.get("baseline_v0") is not False]),
            "n_edit_chains": n_chains,
            "scenario_ids": [s["id"] for s in scenarios if s.get("baseline_v0") is not False],
            "notes": META_NOTES,
        }
        write_json(args.out / "_meta.json", meta)
    print(f"done in {time.perf_counter() - t_all:.1f}s -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
