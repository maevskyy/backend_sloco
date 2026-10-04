"""Message catalog of the Walk Planner: stable codes + parameters, rendered to Russian or English.

Everything the planner tells the user is a ``Message(code, params, stop_index)``; the API returns the
code, its severity / scope, the raw params and a convenience ``text`` in the requested language.
Codes are append-only (clients may localise them themselves).

The Russian templates of the codes the dashboard already showed reproduce its strings BYTE-FOR-BYTE
(dashboard_app.py L4646-4700, L4817-4835, L4973-4974; pinned by the golden baseline), so the page can
render through this catalog without any visible change. English texts are drafts
(understand/features.md §4.2). Params hold raw values (minutes, km, codes, ids, names) plus the
pre-formatted clock labels the Russian texts embed (``*_label``: "HH:MM", "HH:MM (+1 день)",
"HH:MM (+N)") — English rendering re-labels those day suffixes.

Two catalogs: ``MESSAGES`` (request / variant / stop scope; returned inside 200 responses) and
``ERRORS`` (HTTP error bodies ``{"error": {"code", "message", "params"}}``: the planner's codes and the
service-level ones -- bad_request, not_found, method_not_allowed, payload_too_large, internal_error, busy).
``place_temporarily_closed`` is in both: a warning on a kept stop, and the 422 that asks the client to
confirm an insert.

Every entry declares its parameters (``params``: always present; ``optional``: present in some
responses) and ``examples`` (sample params, one per text variant). They are the contract the app can
rely on; ``tools/export_messages.py`` publishes the whole catalog as ``docs/messages.json`` (tests keep
it current and check that the planner only emits declared params).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .slots import SHAPES, STYLES, activity_label

__all__ = ["MessageDef", "MESSAGES", "ERRORS", "LANGS", "render", "render_error", "error_status", "PlannerInputError",
           "Message"]

Template = Callable[[dict], str]                   # params -> text (typing alias, module-local)


@dataclass(frozen=True)
class MessageDef:
    """One catalog entry: severity (info | warning | error), scope (request | variant | stop |
    error), the RU and EN renderers, the HTTP status of error codes, the declared parameters
    (``params`` always present, ``optional`` sometimes) and sample params (``examples``: one per
    text variant; docs/messages.json and the tests render them)."""

    code: str
    severity: str
    scope: str
    ru: Template
    en: Template
    http_status: Optional[int] = None
    params: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    examples: tuple[dict, ...] = ()


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
_DAY_SUFFIX_RU = re.compile(r" \(\+1 день\)$")


def _en_time(label: Any) -> str:
    """A RU clock label in English: 'HH:MM (+1 день)' -> 'HH:MM (+1 day)' ('(+N)' is neutral)."""
    return _DAY_SUFFIX_RU.sub(" (+1 day)", str(label))


def _labels(codes, lang: str) -> str:
    return ", ".join(activity_label(c, lang) for c in (codes or []))


def _style_label(code: Any, lang: str) -> str:
    c = STYLES.get(str(code))
    return (c.label_en if lang == "en" else c.label_ru) if c else str(code)


def _shape_label(code: Any, lang: str) -> str:
    c = SHAPES.get(str(code))
    return (c.label_en if lang == "en" else c.label_ru) if c else str(code)


def _hours_en(p: dict) -> str:
    """English hours text for stop_hours_conflict: from the structured `hours_day` param when given."""
    day = p.get("hours_day")
    if not isinstance(day, dict):
        return str(p.get("hours_label_en") or p.get("hours_label") or "")
    from .slots import DAYS_EN
    wd = DAYS_EN[int(day.get("weekday", 0)) % 7]
    parts = []
    if day.get("open_24h"):
        parts.append("open 24 h")
    if day.get("carryover_until"):
        parts.append(f"until {day['carryover_until']}")
    for iv in day.get("intervals") or []:
        parts.append(f"{iv['open']}–{iv['close']}")
    return f"{wd} " + (", ".join(parts) if parts else "closed")


# --------------------------------------------------------------------------- #
# Request-level texts
# --------------------------------------------------------------------------- #
def _ru_radius_shrunk(p: dict) -> str:
    head = f"Радиус {float(p['radius_km']):g} км сужен до {float(p['search_radius_km']):.1f} км: "
    if p.get("reason") == "overpacked":
        return (head + f"на все слоты нужно ~{p['dwell_total_min']:.0f} мин, это больше окна ({p['window_min']} мин), "
                "поэтому ищу " + ("рядом со стартом" if p.get("anchor") == "start" else "в центре района")
                + ", а слоты, которые не влезут, отпадут.")
    return (head + f"за окно {p['window_start_label']}–{p['window_end_label']} дальше не успеть — "
            f"~{p['dwell_total_min']:.0f} мин уходит на сами места"
            + (", и нужно вернуться к старту." if p.get("loop") else "."))


def _en_radius_shrunk(p: dict) -> str:
    head = f"Search radius narrowed from {float(p['radius_km']):g} to {float(p['search_radius_km']):.1f} km: "
    if p.get("reason") == "overpacked":
        where = "near the start" if p.get("anchor") == "start" else "around the area centre"
        return (head + f"your activities need ~{p['dwell_total_min']:.0f} min, more than the {p['window_min']}-min "
                f"window, so I'm searching {where}; activities that don't fit will be dropped.")
    return (head + f"you can't get further within {p['window_start_label']}–{_en_time(p['window_end_label'])}; "
            f"~{p['dwell_total_min']:.0f} min go to the visits" + (", and you need to walk back." if p.get("loop") else "."))


def _ru_personalization_unavailable(p: dict) -> str:
    # `reason` is a machine code (not shown); `detail` (optional) is the page's "({exc})" part.
    detail = p.get("detail")
    return ("⚠︎ персонализация недоступна" + (f" ({detail})" if detail else "")
            + "; ранжирую по популярности.")


def _ru_unknown_place_ids(p: dict) -> str:
    return "Не нашли в каталоге города, пропущено: " + ", ".join(str(x) for x in p.get("place_ids") or []) + "."


def _en_unknown_place_ids(p: dict) -> str:
    return "Not in the city catalog, skipped: " + ", ".join(str(x) for x in p.get("place_ids") or []) + "."


# --------------------------------------------------------------------------- #
# Stop-level texts
# --------------------------------------------------------------------------- #
def _ru_stop_hours_conflict(p: dict) -> str:
    when = (f"закрывается раньше, чем закончится визит {p['arrival_label']}–{p['departure_label']}"
            if p.get("reason") == "closes_during_visit" else f"в {p['arrival_label']} по графику закрыто")
    return (f"⚠️ «{p['name']}» — {when} ({p['hours_label']}). "
            "Маршрут всё равно построен: оставьте, если знаете, что сегодня работает дольше.")


def _en_stop_hours_conflict(p: dict) -> str:
    if p.get("reason") == "closes_during_visit":
        head = (f"\"{p['name']}\" closes before your visit ends ({_en_time(p['arrival_label'])}–"
                f"{_en_time(p['departure_label'])}; {_hours_en(p)}).")
    else:
        head = f"\"{p['name']}\" is scheduled to be closed at {_en_time(p['arrival_label'])} ({_hours_en(p)})."
    return head + " The route was built anyway; keep it if you know it's open longer today."


_HOURS_DAY_EXAMPLE = {"weekday": 5, "open_24h": False, "closed_all_day": False,
                      "intervals": [{"open": "08:00", "close": "13:00", "close_day_offset": 0}],
                      "carryover_until": None}

_MESSAGE_DEFS = [
    # ---- request scope (top-level "messages")
    MessageDef("radius_shrunk", "info", "request", _ru_radius_shrunk, _en_radius_shrunk,
               params=("radius_km", "search_radius_km", "reason", "dwell_total_min", "window_min",
                       "window_start_label", "window_end_label", "anchor", "loop"),
               examples=({"radius_km": 2.5, "search_radius_km": 1.3888888888888888, "reason": "reach",
                          "dwell_total_min": 190.0, "window_min": 240, "window_start_label": "10:00",
                          "window_end_label": "14:00", "anchor": "start", "loop": True},
                         {"radius_km": 2.5, "search_radius_km": 0.41666666666666663, "reason": "overpacked",
                          "dwell_total_min": 75.0, "window_min": 15, "window_start_label": "12:00",
                          "window_end_label": "12:15", "anchor": "start", "loop": True})),
    MessageDef("slots_no_candidates", "warning", "request",
               lambda p: ("Не нашлось мест, открытых в это время в радиусе, для слотов: " + _labels(p.get("activities"), "ru")
                          + " — увеличьте радиус или окно, или смените точку старта."),
               lambda p: ("No places open at that time within the radius for: " + _labels(p.get("activities"), "en")
                          + ". Widen the radius or window, or change the start."),
               params=("activities", "slot_indices"),
               examples=({"activities": ["coffee", "market"], "slot_indices": [1, 2]},)),
    MessageDef("no_candidates", "error", "request",
               lambda p: "Кандидатов не найдено — расширьте радиус или окно времени.",
               lambda p: "No places found. Widen the radius or the time window.",
               examples=({},)),
    MessageDef("no_route", "error", "request",
               lambda p: ("Не удалось собрать маршрут: к моменту, когда до них можно дойти, кандидаты закрыты "
                          "или не помещаются в окно. Сдвиньте время или смените слоты."),
               lambda p: ("Couldn't build a route: by the time you'd reach them, the places are closed or don't fit "
                          "the window. Shift the time or change the activities."),
               examples=({},)),
    MessageDef("fewer_variants", "info", "request",
               lambda p: (f"Разных маршрутов получилось {p['built']} из {p['requested']}: подходящих мест "
                          "мало — расширьте радиус, окно или число кандидатов на слот."),
               lambda p: (f"Only {p['built']} of {p['requested']} different routes: not enough suitable places. "
                          "Widen the radius or the window."),
               params=("built", "requested"),
               examples=({"built": 1, "requested": 3},)),
    MessageDef("must_visit_closed_forever", "warning", "request",
               lambda p: ("Закрыто навсегда по данным Google — не добавлено в маршрут: "
                          + ", ".join(str(x) for x in p.get("names") or []) + "."),
               lambda p: ("Permanently closed according to Google, left out of the route: "
                          + ", ".join(str(x) for x in p.get("names") or []) + "."),
               params=("place_ids", "names"),
               examples=({"place_ids": ["14018219728270213257"], "names": ["La Mama"]},)),
    MessageDef("unknown_place_ids", "warning", "request", _ru_unknown_place_ids, _en_unknown_place_ids,
               params=("place_ids", "field"),
               examples=({"place_ids": ["3333333333333333333"], "field": "must_visit_place_ids"},)),
    MessageDef("personalization_unavailable", "warning", "request", _ru_personalization_unavailable,
               lambda p: "Personalization is unavailable right now; places are ranked by popularity.",
               params=("reason",), optional=("detail",),
               examples=({"reason": "taste_unavailable"},
                         {"reason": "taste_unavailable", "detail": "любимые места не найдены в данных этого города"})),
    # ---- variant scope (variants[i].messages)
    MessageDef("extras_added", "info", "variant",
               lambda p: (f"Стиль «{_style_label(p.get('style'), 'ru')}»: по пути добавлено мест — {p['count']}, "
                          f"чтобы занять окно {p['window_start_label']}–{p['window_end_label']}. "
                          "Только выбранные слоты — выключите «Добавлять места по пути»."),
               lambda p: (f"Style \"{_style_label(p.get('style'), 'en')}\": {p['count']} "
                          f"{'stop' if p['count'] == 1 else 'stops'} added on the way to fill "
                          f"{p['window_start_label']}–{_en_time(p['window_end_label'])}. To keep only your activities, "
                          "turn off \"Add stops on the way\"."),
               params=("count", "style", "window_start_label", "window_end_label"),
               examples=({"count": 4, "style": "max", "window_start_label": "10:00", "window_end_label": "14:00"},)),
    MessageDef("slots_dropped", "warning", "variant",
               lambda p: ("Не поместилось в окно: " + _labels(p.get("activities"), "ru")
                          + f". Остальное уложено до {p['window_end_label']}; чтобы вернуть слоты, раздвиньте окно."),
               lambda p: ("Didn't fit the window: " + _labels(p.get("activities"), "en")
                          + f". Everything else fits by {_en_time(p['window_end_label'])}; widen the window to bring "
                            "them back."),
               params=("activities", "slot_indices", "window_end_label"),
               examples=({"activities": ["coffee"], "slot_indices": [1], "window_end_label": "00:00 (+1 день)"},)),
    MessageDef("over_budget", "warning", "variant",
               lambda p: (f"Маршрут займёт ~{p['total_min']:.0f} мин, а окно {p['window_min']} мин: "
                          f"финиш в {p['finish_label']}, позже {p['window_end_label']}."),
               lambda p: (f"The route takes ~{p['total_min']:.0f} min but the window is {p['window_min']} min: you "
                          f"finish at {_en_time(p['finish_label'])}, after {_en_time(p['window_end_label'])}."),
               params=("total_min", "window_min", "finish_label", "window_end_label", "finish_at"),
               examples=({"total_min": 277.1415856355617, "window_min": 240, "finish_label": "14:37",
                          "window_end_label": "14:00",
                          "finish_at": {"offset_min": 277.1415856355617, "local": "2026-10-03T14:37"}},)),
    MessageDef("routing_estimate", "info", "variant",
               lambda p: (f"Пешие отрезки посчитаны по прямой (оценка): {p['segments_estimated']} из "
                          f"{p['segments_total']} — по улицам путь может быть длиннее."),
               lambda p: (f"Walking legs are straight-line estimates: {p['segments_estimated']} of "
                          f"{p['segments_total']} — the street route may be longer."),
               params=("segments_estimated", "segments_total"),
               examples=({"segments_estimated": 9, "segments_total": 9},)),
    MessageDef("route_empty", "info", "variant",
               lambda p: "В маршруте не осталось мест — перетащите что-нибудь обратно или добавьте место.",
               lambda p: "No stops left. Drag a place back or add one.",
               examples=({},)),
    # ---- stop scope (variants[i].messages with stop_index)
    MessageDef("stop_hours_conflict", "warning", "stop", _ru_stop_hours_conflict, _en_stop_hours_conflict,
               params=("reason", "place_id", "name", "arrival_label", "departure_label", "hours_label",
                       "closed_all_day", "hours_day"),
               examples=({"reason": "closed_at_arrival", "place_id": "10146158162049940249",
                          "name": "St. Nicholas in-a-Day Church", "arrival_label": "13:02", "departure_label": "13:12",
                          "hours_label": "сб 08:00–13:00", "closed_all_day": False, "hours_day": _HOURS_DAY_EXAMPLE},
                         {"reason": "closes_during_visit", "place_id": "10146158162049940249",
                          "name": "St. Nicholas in-a-Day Church", "arrival_label": "12:40", "departure_label": "13:10",
                          "hours_label": "сб 08:00–13:00", "closed_all_day": False,
                          "hours_day": _HOURS_DAY_EXAMPLE})),
    MessageDef("place_temporarily_closed", "warning", "stop",
               lambda p: (f"⛔ «{p['name']}» — временно закрыто по данным Google. "
                          "Маршрут всё равно построен: проверьте, прежде чем идти."),
               lambda p: (f"⛔ \"{p['name']}\" is temporarily closed according to Google. The route was built anyway: "
                          "check before you go."),
               params=("place_id", "name"),
               examples=({"place_id": "10366085341954844986", "name": "Ryan's Pub"},)),
]
MESSAGES: dict[str, MessageDef] = {m.code: m for m in _MESSAGE_DEFS}


def _ru_validation(p: dict) -> str:
    field_ = p.get("field")
    reason = p.get("reason")
    return "Некорректный запрос" + (f": {field_}" if field_ else "") + (f" — {reason}" if reason else "") + "."


def _en_validation(p: dict) -> str:
    field_ = p.get("field")
    reason = p.get("reason")
    return "Invalid request" + (f": {field_}" if field_ else "") + (f" — {reason}" if reason else "") + "."


_ERROR_DEFS = [
    # ---- the planner's errors (walk_planner.pipeline / catalog raise them as PlannerInputError)
    MessageDef("validation_error", "error", "error", _ru_validation, _en_validation, 422,
               optional=("field", "reason", "value", "place_id", "available", "errors"),
               examples=({"field": "start_time", "reason": "expected a time HH:MM", "value": "9:00"},
                         {"field": "body.variants", "reason": "Input should be less than or equal to 5",
                          "errors": [{"loc": ["body", "variants"], "type": "less_than_equal",
                                      "msg": "Input should be less than or equal to 5", "input": 7,
                                      "ctx": {"le": 5}}]})),
    MessageDef("unknown_city", "error", "error",
               lambda p: f"Город «{p.get('city')}» не поддерживается.",
               lambda p: f"City \"{p.get('city')}\" is not supported.", 422,
               params=("city", "available"),
               examples=({"city": "Atlantis", "available": ["Bucharest"]},)),
    MessageDef("invalid_window", "error", "error",
               lambda p: f"Окно прогулки должно быть от 15 мин до 24 ч (сейчас {p.get('window_min')} мин).",
               lambda p: f"The walk window must be between 15 min and 24 h (got {p.get('window_min')} min).", 422,
               params=("window_min", "min", "max"),
               examples=({"window_min": 5, "min": 15, "max": 1440},)),
    MessageDef("start_required", "error", "error",
               lambda p: f"Для формы «{_shape_label(p.get('shape'), 'ru')}» нужна точка старта.",
               lambda p: f"Shape \"{_shape_label(p.get('shape'), 'en')}\" needs a start point.", 422,
               params=("shape",),
               examples=({"shape": "loop"},)),
    MessageDef("unknown_activity", "error", "error",
               lambda p: f"Неизвестный тип активности: {p.get('activity')}.",
               lambda p: f"Unknown activity: {p.get('activity')}.", 422,
               params=("activity", "slot_index", "allowed"),
               examples=({"activity": "museum", "slot_index": 0,
                          "allowed": ["sight", "coffee", "food", "bar", "park", "market", "entertainment",
                                      "shopping"]},)),
    MessageDef("duplicate_activity", "error", "error",
               lambda p: f"Каждый тип активности можно выбрать только один раз: {activity_label(p.get('activity'), 'ru')}.",
               lambda p: f"Each activity can be chosen only once: {activity_label(p.get('activity'), 'en')}.", 422,
               params=("activity", "slot_index"),
               examples=({"activity": "sight", "slot_index": 1},)),
    MessageDef("no_slots_or_must_visits", "error", "error",
               lambda p: "Добавьте хотя бы один слот активности или место, куда обязательно зайти.",
               lambda p: "Add at least one activity or a place you must visit.", 422,
               examples=({},)),
    MessageDef("too_many_must_visits", "error", "error",
               lambda p: f"Слишком много обязательных мест: {p.get('count')}, можно не больше {p.get('max')}.",
               lambda p: f"Too many must-visit places: {p.get('count')} (at most {p.get('max')}).", 422,
               params=("count", "max"),
               examples=({"count": 11, "max": 10},)),
    MessageDef("unknown_place", "error", "error",
               lambda p: f"Место {p.get('place_id')} не найдено в каталоге.",
               lambda p: f"Place {p.get('place_id')} is not in the catalog.", 404,
               params=("place_id", "field"),
               examples=({"place_id": "3333333333333333333", "field": "place_id"},)),
    MessageDef("place_already_in_route", "error", "error",
               lambda p: "Это место уже в маршруте.",
               lambda p: "This place is already in the route.", 409,
               params=("place_id",),
               examples=({"place_id": "18000000000000395950"},)),
    MessageDef("place_closed_forever", "error", "error",
               lambda p: "⛔ Закрыто навсегда по данным Google — добавить нельзя.",
               lambda p: "⛔ Permanently closed according to Google — it can't be added.", 422,
               params=("place_id", "name"),
               examples=({"place_id": "9100000000006179011", "name": "Craft Corner"},)),
    MessageDef("place_temporarily_closed", "error", "error",
               lambda p: "⛔ Временно закрыто по данным Google — всё равно добавить?",
               lambda p: "⛔ Temporarily closed according to Google — add it anyway?", 422,
               params=("place_id", "name"),
               examples=({"place_id": "10366085341954844986", "name": "Ryan's Pub"},)),
    MessageDef("catalog_changed", "error", "error",
               lambda p: "Данные о местах обновились — постройте маршрут заново.",
               lambda p: "The place data was updated — please build the route again.", 409,
               params=("place_id", "catalog_version", "plan_catalog_version"),
               examples=({"place_id": "3333333333333333333", "catalog_version": "bucharest-20261101-0a1b2c3d",
                          "plan_catalog_version": "bucharest-20261003-9f8e7d6c"},)),
    MessageDef("not_ready", "error", "error",
               lambda p: "Сервис ещё загружается — повторите через минуту.",
               lambda p: "The service is still starting — try again in a minute.", 503,
               params=("state",),
               examples=({"state": "starting"},)),
    # ---- service-level errors (walk_planner.service: HTTP layer, overload, crashes)
    MessageDef("busy", "error", "error",
               lambda p: "Сервис сейчас занят другими маршрутами — повторите через пару секунд.",
               lambda p: "The service is busy with other routes — please retry in a couple of seconds.", 503,
               params=("max_concurrent", "retry_after_s"),
               examples=({"max_concurrent": 2, "retry_after_s": 2},)),
    MessageDef("bad_request", "error", "error",
               lambda p: "Некорректный запрос.",
               lambda p: "Bad request.", 400,
               examples=({},)),
    MessageDef("not_found", "error", "error",
               lambda p: "Такого адреса в API нет.",
               lambda p: "No such API path.", 404,
               params=("path",),
               examples=({"path": "/v1/nothing-here"},)),
    MessageDef("method_not_allowed", "error", "error",
               lambda p: "Этот HTTP-метод здесь не поддерживается.",
               lambda p: "Method not allowed here.", 405,
               params=("path",),
               examples=({"path": "/v1/walks/plan"},)),
    MessageDef("payload_too_large", "error", "error",
               lambda p: "Слишком большой запрос.",
               lambda p: "The request body is too large.", 413,
               params=("max_bytes",),
               examples=({"max_bytes": 1048576},)),
    MessageDef("internal_error", "error", "error",
               lambda p: "Внутренняя ошибка сервиса — повторите позже.",
               lambda p: "Internal service error — please try again later.", 500,
               params=("request_id",),
               examples=({"request_id": "0f3c5d1e2b7a4c9d8e6f1a2b3c4d5e6f"},)),
]
ERRORS: dict[str, MessageDef] = {m.code: m for m in _ERROR_DEFS}

LANGS = ("ru", "en")


def _lang(lang: Optional[str]) -> str:
    return "en" if str(lang or "ru").lower().startswith("en") else "ru"


def render(code: str, params: Optional[dict] = None, lang: str = "ru") -> str:
    """Text of a message code (MESSAGES first, then ERRORS) in `lang` ("ru" | "en")."""
    d = MESSAGES.get(code) or ERRORS.get(code)
    if d is None:
        raise KeyError(f"unknown message code {code!r}")
    fn = d.en if _lang(lang) == "en" else d.ru
    return fn(dict(params or {}))


def render_error(code: str, params: Optional[dict] = None, lang: str = "ru") -> str:
    """Text of an ERROR code (the HTTP error body's `message`)."""
    d = ERRORS[code]
    return (d.en if _lang(lang) == "en" else d.ru)(dict(params or {}))


def error_status(code: str) -> int:
    """HTTP status of an error code (422 when unknown)."""
    d = ERRORS.get(code)
    return int(d.http_status) if d is not None and d.http_status else 422


class PlannerInputError(ValueError):
    """A request the planner cannot serve: stable error `code` (an ``ERRORS`` code), `params` and the
    HTTP status (422 validation, 404 unknown place, 409 conflict ...). Raised by the pipeline's
    validation / editing operations and by ``CityCatalog.search``; defined here, next to the error
    catalog, so every module can raise it (also importable from ``walk_planner.pipeline``)."""

    def __init__(self, code: str, params: Optional[dict] = None, http_status: Optional[int] = None):
        self.code = code
        self.params = dict(params or {})
        self.http_status = int(http_status) if http_status is not None else error_status(code)
        super().__init__(f"{code}: {self.params}" if self.params else code)

    def message(self, lang: str = "ru") -> str:
        return render_error(self.code, self.params, lang)

    def to_dict(self, lang: str = "ru") -> dict:
        """{"code", "message", "params"} — the inner object of the HTTP error body."""
        return {"code": self.code, "message": self.message(lang), "params": dict(self.params)}


@dataclass
class Message:
    """A user-facing message: code + params (+ stop_index for stop-scope messages)."""

    code: str
    params: dict = field(default_factory=dict)
    stop_index: Optional[int] = None

    @property
    def definition(self) -> MessageDef:
        return MESSAGES[self.code]

    @property
    def severity(self) -> str:
        return self.definition.severity

    @property
    def scope(self) -> str:
        return self.definition.scope

    def text(self, lang: str = "ru") -> str:
        return render(self.code, self.params, lang)

    def to_dict(self, lang: str = "ru") -> dict:
        """API Message: {code, severity, scope, params, stop_index, text}."""
        return {"code": self.code, "severity": self.severity, "scope": self.scope, "params": dict(self.params),
                "stop_index": self.stop_index, "text": self.text(lang)}
