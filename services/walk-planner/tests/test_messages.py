"""Unit tests of walk_planner.messages: the message catalog, RU texts byte-for-byte equal to the
dashboard's (strings copied from the golden baseline / dashboard_app.py), EN drafts, error codes, the
declared params (every message / error the golden outputs hold uses exactly them) and the exported
machine-readable catalog docs/messages.json (current)."""

import json
import sys
from pathlib import Path

import pytest

from walk_planner.messages import ERRORS, MESSAGES, Message, error_status, render, render_error
from walk_planner.pipeline import PlannerInputError

ROOT = Path(__file__).resolve().parents[1]

# One plausible params dict per code (enough for every template to render).
SAMPLE_PARAMS = {
    "radius_shrunk": {"radius_km": 2.5, "search_radius_km": 1.3888, "reason": "reach", "dwell_total_min": 190.0,
                      "window_min": 240, "window_start_label": "10:00", "window_end_label": "14:00",
                      "anchor": "start", "loop": True},
    "slots_no_candidates": {"activities": ["coffee", "market"], "slot_indices": [1, 2]},
    "no_candidates": {},
    "no_route": {},
    "fewer_variants": {"built": 1, "requested": 3},
    "must_visit_closed_forever": {"place_ids": ["1"], "names": ["La Mama"]},
    "unknown_place_ids": {"place_ids": ["999"], "field": "must_visit_place_ids"},
    "personalization_unavailable": {"reason": "taste_unavailable"},
    "extras_added": {"count": 3, "style": "max", "window_start_label": "10:00", "window_end_label": "14:00"},
    "slots_dropped": {"activities": ["coffee"], "slot_indices": [1], "window_end_label": "00:00 (+1 день)"},
    "over_budget": {"total_min": 287.3, "window_min": 240, "finish_label": "14:47", "window_end_label": "14:00"},
    "routing_estimate": {"segments_estimated": 7, "segments_total": 8},
    "route_empty": {},
    "stop_hours_conflict": {"reason": "closed_at_arrival", "place_id": "1", "name": "Origo",
                            "arrival_label": "21:47", "departure_label": "22:17", "hours_label": "пн 07:30–20:00",
                            "closed_all_day": False,
                            "hours_day": {"weekday": 0, "open_24h": False, "closed_all_day": False,
                                          "intervals": [{"open": "07:30", "close": "20:00", "close_day_offset": 0}],
                                          "carryover_until": None}},
    "place_temporarily_closed": {"place_id": "1", "name": "Ryan's Pub"},
}


def test_every_message_code_renders_in_both_languages():
    assert set(SAMPLE_PARAMS) == set(MESSAGES)
    for code, d in MESSAGES.items():
        assert d.severity in ("info", "warning", "error") and d.scope in ("request", "variant", "stop")
        for lang in ("ru", "en"):
            text = render(code, SAMPLE_PARAMS[code], lang)
            assert isinstance(text, str) and text.strip(), (code, lang)


def test_every_error_code_renders_and_has_an_http_status():
    expected = {"validation_error": 422, "unknown_city": 422, "invalid_window": 422, "start_required": 422,
                "unknown_activity": 422, "duplicate_activity": 422, "no_slots_or_must_visits": 422,
                "too_many_must_visits": 422, "unknown_place": 404, "place_already_in_route": 409,
                "place_closed_forever": 422, "place_temporarily_closed": 422, "catalog_changed": 409,
                "not_ready": 503,
                # service level (walk_planner.service)
                "busy": 503, "bad_request": 400, "not_found": 404, "method_not_allowed": 405,
                "payload_too_large": 413, "internal_error": 500}
    assert {c: d.http_status for c, d in ERRORS.items()} == expected
    for code in ERRORS:
        for lang in ("ru", "en"):
            assert render_error(code, {"field": "x", "reason": "y", "city": "Paris", "window_min": 5,
                                       "shape": "loop", "activity": "sight", "count": 11, "max": 10,
                                       "place_id": "1"}, lang)
    assert error_status("unknown_place") == 404 and error_status("nope") == 422


# --------------------------------------------------------------------------- #
# RU texts == the dashboard's strings (copied from golden/baseline_v0)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("params,expected", [
    (dict(radius_km=2.5, search_radius_km=1.3888, reason="reach", dwell_total_min=190.0, window_min=240,
          window_start_label="10:00", window_end_label="14:00", anchor="start", loop=True),
     "Радиус 2.5 км сужен до 1.4 км: за окно 10:00–14:00 дальше не успеть — ~190 мин уходит на сами места, "
     "и нужно вернуться к старту."),
    (dict(radius_km=50.0, search_radius_km=16.43, reason="reach", dwell_total_min=250.0, window_min=840,
          window_start_label="08:00", window_end_label="22:00", anchor="start", loop=True),
     "Радиус 50 км сужен до 16.4 км: за окно 08:00–22:00 дальше не успеть — ~250 мин уходит на сами места, "
     "и нужно вернуться к старту."),
    (dict(radius_km=2.5, search_radius_km=0.375, reason="overpacked", dwell_total_min=75.0, window_min=15,
          window_start_label="12:00", window_end_label="12:15", anchor="start", loop=True),
     "Радиус 2.5 км сужен до 0.4 км: на все слоты нужно ~75 мин, это больше окна (15 мин), поэтому ищу рядом "
     "со стартом, а слоты, которые не влезут, отпадут."),
    # shape "free" (no start) and one_way: the page's other branches
    (dict(radius_km=2.5, search_radius_km=0.4, reason="overpacked", dwell_total_min=75, window_min=15,
          window_start_label="12:00", window_end_label="12:15", anchor="center", loop=False),
     "Радиус 2.5 км сужен до 0.4 км: на все слоты нужно ~75 мин, это больше окна (15 мин), поэтому ищу в центре "
     "района, а слоты, которые не влезут, отпадут."),
    (dict(radius_km=2.5, search_radius_km=2.0, reason="reach", dwell_total_min=190.0, window_min=240,
          window_start_label="10:00", window_end_label="14:00", anchor="start", loop=False),
     "Радиус 2.5 км сужен до 2.0 км: за окно 10:00–14:00 дальше не успеть — ~190 мин уходит на сами места."),
])
def test_radius_shrunk_ru_is_the_dashboard_string(params, expected):
    assert render("radius_shrunk", params, "ru") == expected


def test_request_texts_ru_are_the_dashboard_strings():
    assert render("fewer_variants", {"built": 1, "requested": 3}) == (
        "Разных маршрутов получилось 1 из 3: подходящих мест мало — расширьте радиус, окно или число "
        "кандидатов на слот.")
    assert render("slots_no_candidates", {"activities": ["coffee", "market"], "slot_indices": [1, 2]}) == (
        "Не нашлось мест, открытых в это время в радиусе, для слотов: Кофе, Рынок / площадь — увеличьте радиус "
        "или окно, или смените точку старта.")
    assert render("no_candidates") == "Кандидатов не найдено — расширьте радиус или окно времени."
    assert render("no_route") == ("Не удалось собрать маршрут: к моменту, когда до них можно дойти, кандидаты "
                                  "закрыты или не помещаются в окно. Сдвиньте время или смените слоты.")
    # the page's caption with its exception text, when given as `detail`; the machine reason is not shown
    assert render("personalization_unavailable", {"reason": "x", "detail": "boom"}) == (
        "⚠︎ персонализация недоступна (boom); ранжирую по популярности.")
    assert render("personalization_unavailable", {"reason": "taste_unavailable"}) == (
        "⚠︎ персонализация недоступна; ранжирую по популярности.")


def test_variant_texts_ru_are_the_dashboard_strings():
    assert render("extras_added", {"count": 3, "style": "max", "window_start_label": "10:00",
                                   "window_end_label": "14:00"}) == (
        "Стиль «Максимум мест»: по пути добавлено мест — 3, чтобы занять окно 10:00–14:00. Только выбранные "
        "слоты — выключите «Добавлять места по пути».")
    assert render("extras_added", {"count": 11, "style": "max", "window_start_label": "22:00",
                                   "window_end_label": "04:00 (+1 день)"}) == (
        "Стиль «Максимум мест»: по пути добавлено мест — 11, чтобы занять окно 22:00–04:00 (+1 день). Только "
        "выбранные слоты — выключите «Добавлять места по пути».")
    assert render("extras_added", {"count": 1, "style": "scenic", "window_start_label": "10:00",
                                   "window_end_label": "14:00"}).startswith("Стиль «Живописный»: по пути добавлено мест — 1,")
    assert render("slots_dropped", {"activities": ["coffee"], "slot_indices": [1],
                                    "window_end_label": "00:00 (+1 день)"}) == (
        "Не поместилось в окно: Кофе. Остальное уложено до 00:00 (+1 день); чтобы вернуть слоты, раздвиньте окно.")
    assert render("over_budget", {"total_min": 287.4, "window_min": 240, "finish_label": "14:47",
                                  "window_end_label": "14:00"}) == (
        "Маршрут займёт ~287 мин, а окно 240 мин: финиш в 14:47, позже 14:00.")
    assert render("route_empty") == "В маршруте не осталось мест — перетащите что-нибудь обратно или добавьте место."


def test_stop_hours_conflict_ru_both_reasons():
    p = dict(SAMPLE_PARAMS["stop_hours_conflict"])
    assert render("stop_hours_conflict", p) == (
        "⚠️ «Origo» — в 21:47 по графику закрыто (пн 07:30–20:00). Маршрут всё равно построен: оставьте, если "
        "знаете, что сегодня работает дольше.")
    p.update(reason="closes_during_visit", arrival_label="19:40", departure_label="20:10")
    assert render("stop_hours_conflict", p) == (
        "⚠️ «Origo» — закрывается раньше, чем закончится визит 19:40–20:10 (пн 07:30–20:00). Маршрут всё равно "
        "построен: оставьте, если знаете, что сегодня работает дольше.")


def test_place_already_in_route_and_no_slots_are_the_dashboard_strings():
    assert render_error("place_already_in_route", {"place_id": "1"}) == "Это место уже в маршруте."
    assert render_error("no_slots_or_must_visits") == ("Добавьте хотя бы один слот активности или место, куда "
                                                       "обязательно зайти.")


# --------------------------------------------------------------------------- #
# EN drafts
# --------------------------------------------------------------------------- #
def test_en_texts_relabel_day_suffixes_and_use_english_labels():
    assert render("slots_dropped", {"activities": ["coffee", "park"], "slot_indices": [1, 2],
                                    "window_end_label": "00:00 (+1 день)"}, "en") == (
        "Didn't fit the window: Coffee, Park / nature. Everything else fits by 00:00 (+1 day); widen the window "
        "to bring them back.")
    assert "день" not in render("radius_shrunk", dict(SAMPLE_PARAMS["radius_shrunk"],
                                                      window_end_label="02:00 (+1 день)"), "en")
    txt = render("stop_hours_conflict", SAMPLE_PARAMS["stop_hours_conflict"], "en")
    assert txt.startswith('"Origo" is scheduled to be closed at 21:47 (Mon 07:30–20:00).')
    assert render("extras_added", dict(SAMPLE_PARAMS["extras_added"], count=1), "en").startswith(
        'Style "Max places": 1 stop added')
    assert render("no_candidates", {}, "en-GB") == "No places found. Widen the radius or the time window."


# --------------------------------------------------------------------------- #
# Message / error objects
# --------------------------------------------------------------------------- #
def test_message_to_dict_shape():
    m = Message("place_temporarily_closed", {"place_id": "1", "name": "X"}, stop_index=2)
    d = m.to_dict("en")
    assert d == {"code": "place_temporarily_closed", "severity": "warning", "scope": "stop",
                 "params": {"place_id": "1", "name": "X"}, "stop_index": 2, "text": m.text("en")}
    assert Message("radius_shrunk", SAMPLE_PARAMS["radius_shrunk"]).to_dict()["stop_index"] is None
    with pytest.raises(KeyError):
        render("no_such_code")


def test_planner_input_error_body():
    err = PlannerInputError("place_already_in_route", {"place_id": "42"})
    assert err.http_status == 409
    assert err.to_dict("ru") == {"code": "place_already_in_route", "message": "Это место уже в маршруте.",
                                 "params": {"place_id": "42"}}
    assert PlannerInputError("unknown_place", {"place_id": "1"}).http_status == 404
    assert PlannerInputError("validation_error", {"field": "radius_km", "reason": "too big"}, http_status=400).http_status == 400
    assert "radius_km" in PlannerInputError("validation_error", {"field": "radius_km", "reason": "r"}).message("en")


def test_service_level_error_texts():
    assert render_error("busy", {"max_concurrent": 2, "retry_after_s": 2}) == (
        "Сервис сейчас занят другими маршрутами — повторите через пару секунд.")
    assert render_error("busy", {}, "en").startswith("The service is busy")
    assert render_error("internal_error", {"request_id": "x"}) == "Внутренняя ошибка сервиса — повторите позже."
    assert render_error("not_found", {"path": "/x"}, "en") == "No such API path."
    assert PlannerInputError("busy", {}).http_status == 503


# --------------------------------------------------------------------------- #
# declared params + examples (the contract of docs/messages.json)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["message", "error"])
def test_every_code_declares_params_and_examples_that_render(kind):
    catalog, draw = (MESSAGES, render) if kind == "message" else (ERRORS, render_error)
    for code, d in catalog.items():
        assert d.examples, f"{code}: no example"
        assert not set(d.params) & set(d.optional), code
        assert len(set(d.params)) == len(d.params) and len(set(d.optional)) == len(d.optional), code
        for ex in d.examples:
            keys = set(ex)
            assert set(d.params) <= keys <= set(d.params) | set(d.optional), (code, sorted(keys))
            for lang in ("ru", "en"):
                text = draw(code, ex, lang)
                assert isinstance(text, str) and text.strip(), (code, lang)
        if kind == "error":
            assert d.http_status in (400, 404, 405, 409, 413, 422, 500, 503), code
        else:
            assert d.http_status is None, code


def _golden_files():
    files = sorted((ROOT / "tests" / "fixtures" / "mini" / "expected").glob("*/*.json"))
    files += sorted((ROOT / "golden" / "expected").glob("*/*.json"))
    return [f for f in files if f.name != "index.json"]


def _coded(obj, out):
    """Every {"code", "params", ...} object (messages, error bodies' "error") inside a golden document."""
    if isinstance(obj, dict):
        if isinstance(obj.get("code"), str) and isinstance(obj.get("params"), dict):
            out.append(obj)
        for v in obj.values():
            _coded(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _coded(v, out)
    return out


def test_the_planner_emits_exactly_the_declared_params():
    """Every message and error body of the golden outputs (mini set + the real expected set when present)
    carries all of its code's ``params`` and nothing beyond ``params`` + ``optional``."""
    files = _golden_files()
    assert files
    seen = set()
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        for item in _coded(doc, []):
            code = item["code"]
            d = MESSAGES.get(code) if "severity" in item and item.get("scope") != "error" else ERRORS.get(code)
            assert d is not None, (f.name, code)
            keys = set(item["params"])
            assert set(d.params) <= keys <= set(d.params) | set(d.optional), (f.name, code, sorted(keys))
            seen.add(code)
    assert {"radius_shrunk", "extras_added", "routing_estimate", "unknown_place"} <= seen


def test_docs_messages_json_is_current():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import export_messages
    finally:
        sys.path.remove(str(ROOT / "tools"))
    committed = ROOT / "docs" / "messages.json"
    assert committed.is_file(), "run python tools/export_messages.py"
    assert export_messages.render_text(export_messages.build_catalog()) == committed.read_text(encoding="utf-8"), \
        "docs/messages.json is stale: run python tools/export_messages.py"
    doc = json.loads(committed.read_text(encoding="utf-8"))
    assert [e["code"] for e in doc["messages"]] == sorted(MESSAGES)
    assert [e["code"] for e in doc["errors"]] == sorted(ERRORS)
    busy = next(e for e in doc["errors"] if e["code"] == "busy")
    assert busy["http_status"] == 503 and busy["params"] == ["max_concurrent", "retry_after_s"]
    radius = next(e for e in doc["messages"] if e["code"] == "radius_shrunk")
    assert radius["scope"] == "request" and radius["http_status"] is None and len(radius["examples"]) == 2
    assert radius["ru"] == render("radius_shrunk", radius["examples"][0]["params"], "ru")
