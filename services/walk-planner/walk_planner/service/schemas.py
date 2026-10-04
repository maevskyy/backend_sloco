"""Pydantic v2 models of the walk-planner HTTP API: the authoritative OpenAPI (docs/openapi.json).

Responses are made by ``walk_planner.present`` (``plan_response``, ``edit_response``, ``config_view``, place
cards), exactly as the package's API facade (``walk_planner.cli.api_plan`` ...) and the golden expected
outputs make them. These models describe them field by field with ``extra="forbid"``: the test suite
validates every golden expected response against them, so a field added to ``present`` without a schema
change fails CI. The service returns the ``present`` JSON as is (the models are not on the response path).

Requests: the models document the canonical JSON and check JSON types and formats -- place ids are decimal
strings ``^[0-9]{1,20}$`` (never JSON numbers: a Google CID exceeds 2^53, a JavaScript number would already
have changed it), dates ``YYYY-MM-DD``, times ``HH:MM``, codes for shape / style. The domain rules (window
15 min..24 h, known and unique activities, a start for loop / one_way, at most 10 must-visits, closed places,
...) belong to ``walk_planner.pipeline``, which answers with its own error codes (``unknown_activity``,
``invalid_window``, ``too_many_must_visits`` ...; ``walk_planner.messages.ERRORS``): the service hands it the
request JSON exactly as received, so the HTTP API behaves like the package facade. An activity code is
therefore documented as an enum but checked by the pipeline (an unknown one is ``unknown_activity``, not a
generic validation error).

Error bodies are ``{"error": {"code", "message", "params"}}``; for request-format errors the code is
``validation_error`` and ``params.errors`` lists the problems (``loc``, ``type``, ``msg``).

Examples are real outputs (golden scenario S01 on bundle ``bucharest-20261002-d68a311e``, trimmed to two
stops; error bodies of the mini golden set), checked against the models by the tests.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

__all__ = [
    "PLACE_ID_PATTERN", "PlaceId", "Lang", "Geometry", "ActivityCode", "StyleCode", "ShapeCode", "StopKind",
    "TimePoint", "Message", "ErrorDetail", "ErrorBody", "ValidationProblem", "Versions",
    "SlotIn", "StartIn", "PlanRequest", "RequestEchoIn", "EditStopIn", "ScheduleRequest", "InsertRequest",
    "HoursInterval", "HoursDay", "StopHours", "Stop", "SegmentEnd", "LineString", "Segment", "NavigationLeg",
    "Navigation", "Summary", "EditStopOut", "Variant", "Photo", "PlaceCard", "Personalization", "DebugCandidate",
    "DebugInfo", "StartOut", "SlotOut", "RequestEcho", "PlanResponse", "EditResponse", "InsertResponse",
    "DetailSection", "PlaceDetail", "SearchResult", "SearchResponse", "LatLon", "ActivityInfo", "ChoiceInfo",
    "ConfigDefaults", "ConfigLimits", "ConfigResponse", "AlgorithmInfo", "MetaVersions", "InterestSummary",
    "BundleSummary", "MetaResponse", "HealthLive", "HealthReady",
    "PLAN_REQUEST_EXAMPLES", "SCHEDULE_REQUEST_EXAMPLES", "INSERT_REQUEST_EXAMPLES", "PLAN_RESPONSE_EXAMPLE",
    "ERROR_EXAMPLES",
]

# --------------------------------------------------------------------------- #
# Scalars
# --------------------------------------------------------------------------- #
PLACE_ID_PATTERN = r"^[0-9]{1,20}$"
_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_HHMM = r"^[0-9]{2}:[0-9]{2}$"
_LOCAL = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$"
_PHOTO_KEY = r"^photos_cid/[0-9]+/[0-9]{2}_[a-z]+\.jpg$"

PlaceId = Annotated[str, Field(
    pattern=PLACE_ID_PATTERN, examples=["3676382838556882236"],
    description="Google CID of the place as a decimal STRING (it exceeds 2^53 and int64: never a number)")]
Lang = Literal["ru", "en"]
Geometry = Literal["geojson", "polyline6"]
ActivityCode = Literal["sight", "coffee", "food", "bar", "park", "market", "entertainment", "shopping"]
StyleCode = Literal["max", "chill", "scenic"]
ShapeCode = Literal["loop", "one_way", "free"]
StopKind = Literal["slot", "on_the_way", "pinned"]
_ACTIVITY_CODES = ["sight", "coffee", "food", "bar", "park", "market", "entertainment", "shopping"]
# An activity in a REQUEST: documented as the enum, checked by the pipeline (unknown -> 422 unknown_activity).
ActivityIn = Annotated[str, Field(
    json_schema_extra={"enum": _ACTIVITY_CODES},
    description="activity code (an unknown code is answered with 422 unknown_activity)")]
BusinessStatus = Literal["operational", "temporarily_closed", "closed_forever"]


class _Out(BaseModel):
    """A response object: exactly these keys (validated against real outputs by the tests)."""

    model_config = ConfigDict(extra="forbid")


class _In(BaseModel):
    """A request object: unknown keys are ignored (as the pipeline ignores them)."""

    model_config = ConfigDict(extra="ignore")


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #
class TimePoint(_Out):
    """A moment of the walk: minutes after the departure + the city-local wall clock (rounded to the
    minute with Python ``round``, like the dashboard)."""

    offset_min: float = Field(description="minutes after the window start (the departure)")
    local: str = Field(pattern=_LOCAL, description="city-local clock 'YYYY-MM-DDTHH:MM' (timezone: request.timezone)")


class Message(_Out):
    """Something the planner tells the user: a stable code + its parameters + the text in the requested
    language. Codes are append-only -- treat an unknown one as informational. Request scope: radius_shrunk,
    slots_no_candidates, no_candidates, no_route, fewer_variants, must_visit_closed_forever, unknown_place_ids,
    personalization_unavailable. Variant scope: extras_added, slots_dropped, over_budget, routing_estimate,
    route_empty. Stop scope: stop_hours_conflict, place_temporarily_closed."""

    code: str = Field(examples=["radius_shrunk"])
    severity: Literal["info", "warning", "error"]
    scope: Literal["request", "variant", "stop"]
    params: dict[str, Any] = Field(description="raw values of the message (minutes, km, codes, ids, names, labels)")
    stop_index: Optional[int] = Field(description="the stop a stop-scope message is about (Stop.index), else null")
    text: str = Field(description="rendered in the requested language (ru texts = the dashboard's strings)")


class ErrorDetail(_Out):
    code: str = Field(description=(
        "validation_error | unknown_city | invalid_window | start_required | unknown_activity | duplicate_activity "
        "| no_slots_or_must_visits | too_many_must_visits (422) · unknown_place (404) · place_already_in_route | "
        "catalog_changed (409) · place_closed_forever | place_temporarily_closed (422) · not_ready (503) · "
        "service level: busy (503: every load-guard slot of the worker is taken -- retry after Retry-After), "
        "not_found (404), method_not_allowed (405), payload_too_large (413), bad_request (400), internal_error "
        "(500). The full catalog with params and texts: docs/messages.json"), examples=["unknown_place"])
    message: str = Field(description="human-readable, in the requested language")
    params: dict[str, Any] = Field(description="machine-readable details (validation_error: field / reason, or errors)")


class ErrorBody(_Out):
    """Every non-2xx answer."""

    error: ErrorDetail


class ValidationProblem(_Out):
    """One entry of ``params.errors`` of a request-format ``validation_error``."""

    loc: list[Union[str, int]] = Field(description="where: ['body', 'slots', 0, 'activity'], ['query', 'lat'] ...")
    type: str = Field(description="pydantic error type (missing, string_pattern_mismatch, less_than_equal ...)")
    msg: str
    input: Optional[Union[str, int, float, bool]] = Field(None, description="the offending scalar (strings capped)")
    ctx: Optional[dict[str, Union[str, int, float, bool, None]]] = None


class Versions(_Out):
    """What produced a response (cache keys, debugging)."""

    api: str = Field(examples=["v1"])
    algorithm: str = Field(examples=["1.0.0"], description="walk_planner ALGORITHM_VERSION (semver)")
    catalog: str = Field(examples=["bucharest-20261002-d68a311e"], description="the data bundle id")
    interest: str = Field(examples=["walk_interest_v1"])
    routing: Optional[str] = Field(description="OSRM data_version once known (street routing), else null")


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class SlotIn(_In):
    """One activity slot of the walk, in order."""

    activity: ActivityIn
    dwell_min: Optional[int] = Field(None, ge=5, le=480, description="the user's visit length; null = per place")


class StartIn(_In):
    """The start: coordinates, or a catalog place (then its coordinates)."""

    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    place_id: Optional[PlaceId] = None


class _Options(_In):
    lang: Optional[Lang] = Field(None, description="text language (alternative to ?lang=; default WALK_DEFAULT_LANG)")
    geometry: Optional[Geometry] = Field(
        None, description="segment geometry: GeoJSON LineString or 'polyline6' (alternative to ?geometry=)")


class PlanRequest(_Options):
    """POST /v1/walks/plan. Times are city-local; the window is 15 min..24 h. Omitted fields take the
    defaults of GET /v1/walks/config."""

    city: Optional[str] = Field(None, max_length=100, examples=["Bucharest"],
                                description="a served city (GET /v1/meta); may be omitted when the service has one")
    date: str = Field(pattern=_DATE, examples=["2026-10-03"], description="walk date, city-local")
    start_time: Optional[str] = Field(None, pattern=_HHMM, examples=["10:00"], description="departure (default 10:00)")
    end_time: Optional[str] = Field(None, pattern=_HHMM, examples=["14:00"],
                                    description="window end (default: start + 4 h)")
    end_day_offset: Optional[int] = Field(
        None, ge=0, le=1, description="1 = the window ends the next day (default: inferred, end <= start -> 1)")
    shape: Optional[ShapeCode] = Field(None, description="loop (back to the start) | one_way | free (no start)")
    start: Optional[Union[Literal["city_center"], StartIn]] = Field(
        None, description='"city_center" | {lat, lon} | {place_id}; required for loop / one_way, ignored for free')
    style: Optional[StyleCode] = None
    slots: Optional[list[Union[SlotIn, ActivityIn]]] = Field(
        None, max_length=8, description="ordered, each activity at most once (default sight, coffee, park, food)")
    must_visit_place_ids: Optional[list[PlaceId]] = Field(
        None,
        description="places the walk must include (<= 10 after de-duplication, else 422 too_many_must_visits); "
                    "closed_forever ones are "
                    "left out with a message, unknown ones reported")
    favourite_place_ids: Optional[list[PlaceId]] = Field(
        None, description="the user's favourites: personalise the ranking (<= 500 with want_to_go_place_ids)")
    want_to_go_place_ids: Optional[list[PlaceId]] = Field(None, description="the user's want-to-go list")
    personalization_strength: Optional[float] = Field(None, ge=0, le=1, description="0 = popularity only (0.5)")
    variants: Optional[int] = Field(None, ge=1, le=5, description="different routes to return (3)")
    radius_km: Optional[float] = Field(None, ge=0.3, le=50, description="search radius (2.5); narrowed to reach")
    fill_window: Optional[bool] = Field(None, description="add on-the-way stops to use the window (true)")
    known_hours_only: Optional[bool] = Field(None, description="only places with known opening hours (false)")
    top_k: Optional[int] = Field(None, ge=1, le=50, description="bench / CLI only: candidates per slot (8)")
    debug: Optional[bool] = Field(None, description="add the 'debug' block (alternative to ?debug=)")


class RequestEchoIn(PlanRequest):
    """The ``request`` of an earlier plan / edit response, sent back as is (its derived fields are
    recomputed; extra keys are kept for forward compatibility)."""

    model_config = ConfigDict(extra="allow")

    timezone: Optional[str] = None
    weekday: Optional[int] = None
    window_min: Optional[int] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    search_radius_km: Optional[float] = None
    catalog_version: Optional[str] = Field(
        None, description="bundle the plan was built on: a place gone from a newer bundle -> 409 catalog_changed")


class EditStopIn(_In):
    """One stop of an edited route: the ``sequence`` item of a response, possibly reordered / with new minutes."""

    place_id: PlaceId
    kind: Optional[StopKind] = Field(None, description="slot | on_the_way | pinned (default pinned)")
    slot_index: Optional[int] = Field(None, ge=0, description="the requested slot a slot stop fills")
    activity: Optional[ActivityIn] = Field(None, description="required for on_the_way stops")
    dwell_min: float = Field(..., ge=5, le=480, description="visit minutes (required; send back the value from the plan)")
    dwell_fixed: Optional[bool] = None


class ScheduleRequest(_Options):
    """POST /v1/walks/schedule: re-time the stops EXACTLY in the given order (reorder / remove / restore /
    change minutes on the client). Nothing is dropped or swapped; closed-at-that-time stops are flagged."""

    request: RequestEchoIn = Field(description="the request echo of the plan (or of the last edit) response")
    sequence: list[EditStopIn] = Field(max_length=150, description="the stops in the wanted order")
    variant_index: Optional[StrictInt] = Field(0, ge=0, le=99, description="echoed as variant.index")


class InsertRequest(ScheduleRequest):
    """POST /v1/walks/insert: add one catalog place where it costs the least time, then re-time."""

    place_id: PlaceId
    allow_temporarily_closed: Optional[StrictBool] = Field(
        False, description="a temporarily closed place is refused with 422 unless true (ask the user first)")
    dwell_min: Optional[float] = Field(None, ge=5, le=480, description="visit minutes (default: estimated)")


# --------------------------------------------------------------------------- #
# Plan / edit responses
# --------------------------------------------------------------------------- #
class HoursInterval(_Out):
    open: str = Field(pattern=_HHMM)
    close: str = Field(pattern=_HHMM)
    close_day_offset: int = Field(ge=0, description="1 = closes after midnight")


class HoursDay(_Out):
    """Opening hours of one weekday."""

    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    open_24h: bool
    closed_all_day: bool = Field(description="no opening starts this day and not open 24 h")
    intervals: list[HoursInterval] = Field(description="openings that start this day")
    carryover_until: Optional[str] = Field(description="last night's opening still running at that time, 'HH:MM'")


class StopHours(_Out):
    status: Literal["open", "open_after_wait", "unknown", "closes_during_visit", "closed_at_arrival", "not_checked"]
    day: Optional[HoursDay] = Field(description="the hours on the day of the visit start; null when unknown")


class Stop(_Out):
    """One visited place."""

    index: int = Field(ge=0)
    number: int = Field(ge=1, description="index + 1 (the label on the map)")
    place_id: PlaceId
    name: str
    lat: float
    lon: float
    kind: StopKind = Field(description="pinned (the user's own place) > on_the_way (optional extra) > slot")
    slot_index: Optional[int] = Field(description="the requested slot this stop fills (null for extras / own places)")
    activity: Optional[ActivityCode]
    arrival: TimePoint
    visit_start: TimePoint = Field(description="arrival + wait for opening")
    departure: TimePoint
    dwell_min: float
    dwell_fixed: bool = Field(description="the visit length was set by the user")
    wait_min: float
    hours: StopHours
    business_status: BusinessStatus
    interest: float = Field(description="interest in [0, 1] the planner ranked the place with (personalised if so)")


class SegmentEnd(_Out):
    kind: Literal["start", "stop"]
    stop_index: Optional[int]


class LineString(_Out):
    type: Literal["LineString"]
    coordinates: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(
        description="[[lon, lat], ...]")


class Segment(_Out):
    """One walking leg: start -> stop, stop -> stop, or stop -> start (loop)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    index: int = Field(ge=0)
    from_: SegmentEnd = Field(alias="from")
    to: SegmentEnd
    walk_min: float
    distance_m: int
    depart: TimePoint
    arrive: TimePoint
    quality: Literal["streets", "estimate"] = Field(description="streets = a street router; estimate = straight line")
    provider: str = Field(examples=["osrm"], description="osrm | ors | haversine")
    geometry: Optional[LineString] = Field(None, description="GeoJSON (geometry=geojson)")
    geometry_polyline6: Optional[str] = Field(None, description="encoded polyline, precision 6 (geometry=polyline6)")

    @model_validator(mode="after")
    def _one_geometry(self) -> "Segment":
        if (self.geometry is None) == (self.geometry_polyline6 is None):
            raise ValueError("a segment has exactly one of geometry / geometry_polyline6")
        return self


class NavigationLeg(_Out):
    segment_index: Optional[int]
    google: str
    apple: str


class Navigation(_Out):
    """Hand-off to Google / Apple Maps (walking)."""

    google_parts: list[str] = Field(description="the whole route as Google Maps links (split when > 9 waypoints)")
    legs: list[NavigationLeg]


class Summary(_Out):
    stops_total: int
    slots_requested: int
    slots_filled: int
    on_the_way: int
    pinned: int
    dropped_slot_indices: list[int] = Field(description="requested slots that did not fit the window")
    finish_at: TimePoint
    finish_kind: Literal["return", "finish"] = Field(description="return = back at the start (loop)")
    window_min: int
    total_min: float
    walk_min: float
    dwell_min: float
    wait_min: float
    distance_km: float
    slack_min: float = Field(description="window_min - total_min (negative = over budget)")
    over_budget: bool
    routing: Literal["streets", "estimate", "mixed", "none"]


class EditStopOut(_Out):
    """A stop as the client sends it back to /schedule or /insert."""

    place_id: PlaceId
    kind: StopKind
    slot_index: Optional[int]
    activity: Optional[ActivityCode]
    dwell_min: float
    dwell_fixed: bool


class Variant(_Out):
    index: int
    edited: bool
    summary: Summary
    messages: list[Message]
    stops: list[Stop]
    segments: list[Segment]
    navigation: Navigation
    bbox: Optional[Annotated[list[float], Field(min_length=4, max_length=4)]] = Field(
        description="[minLon, minLat, maxLon, maxLat] of stops, start and geometry; null for an empty route")
    sequence: list[EditStopOut] = Field(description="send back (edited) to /schedule or /insert")


class Photo(_Out):
    key: str = Field(pattern=_PHOTO_KEY, description="photos_cid/<cid>/<NN>_<source>.jpg (opaque)")
    url: Optional[str] = Field(description="PHOTO_BASE_URL + '/' + key; null when the service has no base URL")


class PlaceCard(_Out):
    """What a stop card shows. Catalog facts are always the server's, never the client's."""

    place_id: PlaceId
    name: str
    type_label: Optional[str]
    primary_type: Optional[str]
    theme: Optional[str]
    theme_group: Optional[str]
    rating: Optional[float]
    rating_count: Optional[int]
    summary: Optional[str]
    summary_lang: str = Field(examples=["en"])
    photos: list[Photo]
    google_maps_url: str
    google_place_id: Optional[str]
    address: Optional[str]
    business_status: BusinessStatus
    lat: float
    lon: float
    price_level: Optional[str]


class Personalization(_Out):
    mode: Literal["favourites", "popularity"] = Field(description="popularity = no usable favourites (cold start)")
    strength: float
    favourites_used: list[PlaceId]
    favourites_ignored: list[str] = Field(description="given ids that could not be used (unknown, no data ...)")
    profiles: int = Field(description="taste profiles found in the favourites (0 = none)")


class DebugCandidate(_Out):
    place_id: PlaceId
    kind: Literal["slot", "on_the_way"]
    slot_index: Optional[int]
    activity: Optional[ActivityCode]
    interest: float
    lat: float
    lon: float


class DebugInfo(_Out):
    area: Annotated[list[float], Field(min_length=2, max_length=2)] = Field(
        description="[lat, lon] of the search centre")
    search_km: float
    candidates: list[DebugCandidate]


class StartOut(_Out):
    lat: float
    lon: float
    place_id: Optional[PlaceId]


class SlotOut(_Out):
    activity: ActivityCode
    dwell_min: Optional[int]


class RequestEcho(_Out):
    """The normalised request (codes, defaults applied, start resolved) + derived fields. Send it back
    unchanged with /schedule and /insert."""

    city: str
    date: str = Field(pattern=_DATE)
    start_time: str = Field(pattern=_HHMM)
    end_time: str = Field(pattern=_HHMM)
    end_day_offset: int
    shape: ShapeCode
    start: Optional[StartOut] = Field(description="null for shape free")
    style: StyleCode
    slots: list[SlotOut]
    must_visit_place_ids: list[PlaceId]
    favourite_place_ids: list[PlaceId]
    want_to_go_place_ids: list[PlaceId]
    personalization_strength: float
    variants: int
    radius_km: float
    fill_window: bool
    known_hours_only: bool
    top_k: int
    timezone: str = Field(examples=["Europe/Bucharest"])
    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    window_min: int
    window_start: str = Field(pattern=_LOCAL)
    window_end: str = Field(pattern=_LOCAL)
    search_radius_km: float = Field(description="the radius actually searched (<= radius_km)")
    catalog_version: str


class PlanResponse(_Out):
    """POST /v1/walks/plan. ``status`` ok = at least one variant; no_candidates / no_route = no variant (the
    request messages say why) -- still HTTP 200."""

    plan_id: str = Field(pattern=r"^[0-9a-f]{40}$", description="sha1 of the normalised request + versions")
    versions: Versions
    status: Literal["ok", "no_candidates", "no_route"]
    request: RequestEcho
    personalization: Personalization
    messages: list[Message]
    variants: list[Variant]
    places: dict[PlaceId, PlaceCard] = Field(description="card of every stop of every variant, by place_id")
    debug: Optional[DebugInfo] = Field(None, description="only with debug=true")


class EditResponse(_Out):
    """POST /v1/walks/schedule."""

    versions: Versions
    request: RequestEcho
    variant: Variant
    places: dict[PlaceId, PlaceCard]
    messages: list[Message] = Field(description="request-level messages (none today)")


class InsertResponse(EditResponse):
    """POST /v1/walks/insert."""

    inserted_index: int = Field(ge=0, description="index of the new stop in variant.stops")


# --------------------------------------------------------------------------- #
# Places
# --------------------------------------------------------------------------- #
class DetailSection(_Out):
    key: str
    column: str
    title_ru: str
    title_en: str
    text: str = Field(description="English text from the catalog")


class PlaceDetail(PlaceCard):
    """GET /v1/walks/places/{place_id}: the card (up to 10 photos) + text sections + the week's hours."""

    sections: list[DetailSection]
    tags: list[str]
    ai_confidence: Optional[str]
    timezone: str
    opening_hours_week: Optional[list[HoursDay]] = Field(description="Monday..Sunday; null when unknown")
    opening_hours_known: bool


class SearchResult(_Out):
    place_id: PlaceId
    name: str
    type_label: Optional[str]
    primary_type: Optional[str]
    theme: Optional[str]
    theme_group: Optional[str]
    rating: Optional[float]
    rating_count: Optional[int]
    address: Optional[str]
    business_status: BusinessStatus
    lat: float
    lon: float
    google_maps_url: str
    photo: Optional[Photo]
    match: Literal["exact", "prefix", "substring", "all_words", "address"]
    distance_m: Optional[int] = Field(None, description="only when lat / lon were given")


class SearchResponse(_Out):
    """GET /v1/walks/places/search: name matches first (exact > prefix > substring > all words > address),
    then popularity (and distance when lat / lon are given)."""

    city: str
    query: str
    count: int
    results: list[SearchResult]
    catalog_version: str


# --------------------------------------------------------------------------- #
# Config / meta / health
# --------------------------------------------------------------------------- #
class LatLon(_Out):
    lat: float
    lon: float


class ActivityInfo(_Out):
    code: ActivityCode
    label_ru: str
    label_en: str
    label: str = Field(description="in the requested language")
    group: str
    base_dwell_min: float


class ChoiceInfo(_Out):
    code: str
    label_ru: str
    label_en: str
    label: str = Field(description="in the requested language")


class ConfigDefaults(_Out):
    start_time: str
    window_min: int
    shape: ShapeCode
    style: StyleCode
    slots: list[ActivityCode]
    variants: int
    radius_km: float
    fill_window: bool
    known_hours_only: bool
    top_k: int
    personalization_strength: float


class ConfigLimits(_Out):
    """[min, max] pairs and maxima the API enforces."""

    window_min: list[int]
    variants: list[int]
    radius_km: list[float]
    max_slots: int
    dwell_min: list[int]
    max_must_visits: int
    max_personal_ids: int
    top_k: list[int]
    max_edit_stops: int
    personalization_strength: list[float]


class ConfigResponse(_Out):
    """GET /v1/walks/config: everything a client needs to build the form for one city."""

    city: str
    timezone: str
    center: LatLon
    bbox: Annotated[list[float], Field(min_length=4, max_length=4)]
    activities: list[ActivityInfo]
    styles: list[ChoiceInfo]
    shapes: list[ChoiceInfo]
    dwell_choices: list[int]
    defaults: ConfigDefaults
    limits: ConfigLimits
    versions: Versions
    lang: Lang
    cities: list[str] = Field(description="every city this service plans for")


class AlgorithmInfo(_Out):
    name: str
    version: str


class MetaVersions(_Out):
    api: str
    algorithm: str
    interest: str
    bundle_schema: int


class InterestSummary(_Out):
    version: Optional[str]
    fingerprint: Optional[str]
    text_model: Optional[str]
    image_model: Optional[str]
    places_with_text: Optional[int]
    places_with_image: Optional[int]
    loaded: bool = Field(description="the taste artifacts are loaded (personalization available)")


class BundleSummary(_Out):
    bundle_id: str
    city: str
    timezone: str
    built_at: Optional[str]
    schema_version: Optional[int]
    rows: Optional[int] = Field(description="catalog rows in the bundle")
    places: int = Field(description="places the planner uses (rows with coordinates)")
    content_sha256: Optional[str]
    catalog_sha256: Optional[str]
    coverage: Optional[dict[str, Any]]
    interest: Optional[InterestSummary]


class MetaResponse(_Out):
    """GET /v1/meta: identity, versions, loaded data, routing state. No secrets."""

    service: str
    environment: str
    version: str = Field(description="ALGORITHM_VERSION")
    api_version: str
    algorithms: list[AlgorithmInfo]
    versions: MetaVersions
    git_sha: Optional[str]
    ready: bool
    state: Literal["starting", "ready", "failed", "stopping"]
    started_at: str
    uptime_s: float
    startup_ms: Optional[float]
    bundles: list[BundleSummary]
    routing: dict[str, Any] = Field(
        description="walk_planner.routing.routing_status(): chain, breakers, cache, counters (this worker)")
    load: dict[str, Any] = Field(
        description="this worker's load guard: max_concurrent (WALK_MAX_CONCURRENT_PLANS), active, peak, admitted, "
                    "rejected_busy")
    runtime: dict[str, Optional[str]]
    settings: dict[str, Any]


class HealthLive(_Out):
    status: Literal["alive"]


class HealthReady(_Out):
    status: Literal["ready"]
    bundles: list[str]


# --------------------------------------------------------------------------- #
# Examples (real outputs; validated against the models by tests/test_service.py)
# --------------------------------------------------------------------------- #
_SLOTS4 = [{"activity": "sight", "dwell_min": None}, {"activity": "coffee", "dwell_min": None},
           {"activity": "park", "dwell_min": None}, {"activity": "food", "dwell_min": None}]
# golden S01_default: Saturday 10:00-14:00 loop from the city centre, the default slots
_EX_PLAN_REQUEST = {"city": "Bucharest", "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00",
                    "end_day_offset": 0, "shape": "loop", "start": "city_center", "style": "max", "slots": _SLOTS4,
                    "must_visit_place_ids": [], "variants": 3, "radius_km": 2.5, "fill_window": True,
                    "known_hours_only": False}
PLAN_REQUEST_EXAMPLES = {
    "default_loop": {"summary": "Loop from the city centre, four slots (golden S01)", "value": _EX_PLAN_REQUEST},
    "favourites": {"summary": "The same with the user's favourites (golden P01)", "value": {
        **_EX_PLAN_REQUEST, "favourite_place_ids": ["14169121335398956031", "6077420982585694938",
                                                    "16044012576954065712"],
        "want_to_go_place_ids": [], "personalization_strength": 0.5}},
    "start_at_place": {"summary": "Loop starting at a catalog place (golden S20)", "value": {
        **_EX_PLAN_REQUEST, "start": {"place_id": "10915586233752676659"}}},
    "must_visit_only": {"summary": "One way through three must-visit places, no slots (golden S17)", "value": {
        **_EX_PLAN_REQUEST, "start_time": "09:00", "end_time": "19:00", "shape": "one_way", "slots": [],
        "must_visit_place_ids": ["16044012576954065712", "10915586233752676659", "14169121335398956031"]}},
    "free_english_polyline": {"summary": "Around the area, no start; English texts, polyline geometry", "value": {
        "city": "Bucharest", "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00", "shape": "free",
        "style": "chill", "slots": ["sight", "coffee", "food"], "lang": "en", "geometry": "polyline6"}},
}
_EX_ECHO = {"city": "Bucharest", "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00", "end_day_offset": 0,
            "shape": "loop", "start": {"lat": 44.4402865461307, "lon": 26.097708464099995, "place_id": None},
            "style": "max", "slots": _SLOTS4, "must_visit_place_ids": [], "favourite_place_ids": [],
            "want_to_go_place_ids": [], "personalization_strength": 0.5, "variants": 3, "radius_km": 2.5,
            "fill_window": True, "known_hours_only": False, "top_k": 8, "timezone": "Europe/Bucharest", "weekday": 5,
            "window_min": 240, "window_start": "2026-10-03T10:00", "window_end": "2026-10-03T14:00",
            "search_radius_km": 1.3888888888888888, "catalog_version": "bucharest-20261002-d68a311e"}
# golden S01 edit chain A (the last stop moved first) / chain C (add a place), sequences trimmed to 3 stops
SCHEDULE_REQUEST_EXAMPLES = {
    "move_stop": {"summary": "A reordered route (golden S01, chain A; trimmed)", "value": {
        "request": _EX_ECHO, "variant_index": 0, "sequence": [
            {"place_id": "16044012576954065712", "kind": "on_the_way", "slot_index": None, "activity": "sight",
             "dwell_min": 10.0, "dwell_fixed": False},
            {"place_id": "3676382838556882236", "kind": "slot", "slot_index": 0, "activity": "sight",
             "dwell_min": 60.0, "dwell_fixed": False},
            {"place_id": "14916341928181875966", "kind": "slot", "slot_index": 1, "activity": "coffee",
             "dwell_min": 30.0, "dwell_fixed": False}]}},
}
INSERT_REQUEST_EXAMPLES = {
    "add_place": {"summary": "Add a place where it fits best (golden S01, chain C; trimmed)", "value": {
        "request": _EX_ECHO, "variant_index": 0, "place_id": "10915586233752676659",
        "allow_temporarily_closed": False, "sequence": [
            {"place_id": "3676382838556882236", "kind": "slot", "slot_index": 0, "activity": "sight",
             "dwell_min": 60.0, "dwell_fixed": False},
            {"place_id": "14916341928181875966", "kind": "slot", "slot_index": 1, "activity": "coffee",
             "dwell_min": 30.0, "dwell_fixed": False},
            {"place_id": "84644100838256775", "kind": "slot", "slot_index": 2, "activity": "park",
             "dwell_min": 10.0, "dwell_fixed": False}]}},
}


def _tp(offset: float, local: str) -> dict:
    return {"offset_min": offset, "local": local}


def _hours(open_: str, close: str) -> dict:
    return {"status": "open", "day": {"weekday": 5, "open_24h": False, "closed_all_day": False,
                                      "intervals": [{"open": open_, "close": close, "close_day_offset": 0}],
                                      "carryover_until": None}}


# golden S01_default (bundle bucharest-20261002-d68a311e), variant 1 trimmed to its first two stops
PLAN_RESPONSE_EXAMPLE = {
    "plan_id": "dd4b84b018a6b423626abeeeaf4a36408fea3a60",
    "versions": {"api": "v1", "algorithm": "1.0.0", "catalog": "bucharest-20261002-d68a311e",
                 "interest": "walk_interest_v1", "routing": None},
    "status": "ok",
    "request": _EX_ECHO,
    "personalization": {"mode": "popularity", "strength": 0.5, "favourites_used": [], "favourites_ignored": [],
                        "profiles": 0},
    "messages": [{
        "code": "radius_shrunk", "severity": "info", "scope": "request",
        "params": {"radius_km": 2.5, "search_radius_km": 1.3888888888888888, "reason": "reach",
                   "dwell_total_min": 190.0, "window_min": 240, "window_start_label": "10:00",
                   "window_end_label": "14:00", "anchor": "start", "loop": True},
        "stop_index": None,
        "text": "Радиус 2.5 км сужен до 1.4 км: за окно 10:00–14:00 дальше не успеть — ~190 мин уходит на сами "
                "места, и нужно вернуться к старту."}],
    "variants": [{
        "index": 0, "edited": False,
        "summary": {"stops_total": 7, "slots_requested": 4, "slots_filled": 4, "on_the_way": 3, "pinned": 0,
                    "dropped_slot_indices": [], "finish_at": _tp(224.7484965094723, "2026-10-03T13:45"),
                    "finish_kind": "return", "window_min": 240, "total_min": 224.7484965094723,
                    "walk_min": 19.748496509472282, "dwell_min": 205.0, "wait_min": 0.0,
                    "distance_km": 1.481137238210421, "slack_min": 15.25150349052771, "over_budget": False,
                    "routing": "estimate"},
        "messages": [
            {"code": "extras_added", "severity": "info", "scope": "variant",
             "params": {"count": 3, "style": "max", "window_start_label": "10:00", "window_end_label": "14:00"},
             "stop_index": None,
             "text": "Стиль «Максимум мест»: по пути добавлено мест — 3, чтобы занять окно 10:00–14:00. Только "
                     "выбранные слоты — выключите «Добавлять места по пути»."},
            {"code": "routing_estimate", "severity": "info", "scope": "variant",
             "params": {"segments_estimated": 8, "segments_total": 8}, "stop_index": None,
             "text": "Пешие отрезки посчитаны по прямой (оценка): 8 из 8 — по улицам путь может быть длиннее."}],
        "stops": [
            {"index": 0, "number": 1, "place_id": "3676382838556882236", "name": "\"Theodor Aman\" Museum",
             "lat": 44.4402492, "lon": 26.098125699999997, "kind": "slot", "slot_index": 0, "activity": "sight",
             "arrival": _tp(0.6009142608414245, "2026-10-03T10:01"),
             "visit_start": _tp(0.6009142608414245, "2026-10-03T10:01"),
             "departure": _tp(60.60091426084143, "2026-10-03T11:01"), "dwell_min": 60.0, "dwell_fixed": False,
             "wait_min": 0.0, "hours": _hours("10:00", "18:00"), "business_status": "operational",
             "interest": 0.6138572339920014},
            {"index": 1, "number": 2, "place_id": "14916341928181875966", "name": "boteca13", "lat": 44.4395933,
             "lon": 26.098287499999998, "kind": "slot", "slot_index": 1, "activity": "coffee",
             "arrival": _tp(61.933912349904055, "2026-10-03T11:02"),
             "visit_start": _tp(61.933912349904055, "2026-10-03T11:02"),
             "departure": _tp(91.93391234990406, "2026-10-03T11:32"), "dwell_min": 30.0, "dwell_fixed": False,
             "wait_min": 0.0, "hours": _hours("10:00", "17:00"), "business_status": "operational",
             "interest": 0.6618953904261289}],
        "segments": [
            {"index": 0, "from": {"kind": "start", "stop_index": None}, "to": {"kind": "stop", "stop_index": 0},
             "walk_min": 0.6009142608414245, "distance_m": 45, "depart": _tp(0.0, "2026-10-03T10:00"),
             "arrive": _tp(0.6009142608414245, "2026-10-03T10:01"), "quality": "estimate", "provider": "haversine",
             "geometry": {"type": "LineString", "coordinates": [[26.097708464099995, 44.4402865461307],
                                                                [26.098125699999997, 44.4402492]]}},
            {"index": 1, "from": {"kind": "stop", "stop_index": 0}, "to": {"kind": "stop", "stop_index": 1},
             "walk_min": 1.3329980890626303, "distance_m": 100, "depart": _tp(60.60091426084143, "2026-10-03T11:01"),
             "arrive": _tp(61.933912349904055, "2026-10-03T11:02"), "quality": "estimate", "provider": "haversine",
             "geometry": {"type": "LineString", "coordinates": [[26.098125699999997, 44.4402492],
                                                                [26.098287499999998, 44.4395933]]}}],
        "navigation": {
            "google_parts": [
                "https://www.google.com/maps/dir/?api=1&travelmode=walking&origin=44.440287,26.097708"
                "&destination=44.440287,26.097708&waypoints=44.440249,26.098126%7C44.439593,26.098287%7C"
                "44.438490,26.097598%7C44.436745,26.099463%7C44.437099,26.099274%7C44.438130,26.096582%7C"
                "44.439367,26.095874&waypoint_place_ids=ChIJ5YmJ9UX_sUARPHWGnrYiBTM%7CChIJp13bnX7_sUAR_sSLGfR9Ac8"
                "%7CChIJzzxBU3f_sUARh_DLely3LAE%7CChIJi2Am5Ub_sUARGbvqexdlzow%7CChIJsZsycWb5sUARhy2gVQUPWVk%7C"
                "ChIJiaBUzUX_sUARzU41OE-UzmA%7CChIJb0jBokX_sUARMCM_FW_Ip94"],
            "legs": [
                {"segment_index": 0,
                 "google": "https://www.google.com/maps/dir/?api=1&travelmode=walking&origin=44.440287,26.097708"
                           "&destination=44.440249,26.098126&destination_place_id=ChIJ5YmJ9UX_sUARPHWGnrYiBTM",
                 "apple": "https://maps.apple.com/?saddr=44.440287,26.097708&daddr=44.440249,26.098126&dirflg=w"},
                {"segment_index": 1,
                 "google": "https://www.google.com/maps/dir/?api=1&travelmode=walking&origin=44.440249,26.098126"
                           "&destination=44.439593,26.098287&destination_place_id=ChIJp13bnX7_sUAR_sSLGfR9Ac8"
                           "&origin_place_id=ChIJ5YmJ9UX_sUARPHWGnrYiBTM",
                 "apple": "https://maps.apple.com/?saddr=44.440249,26.098126&daddr=44.439593,26.098287&dirflg=w"}]},
        "bbox": [26.095874, 44.4367447, 26.0994633, 44.4402865461307],
        "sequence": [
            {"place_id": "3676382838556882236", "kind": "slot", "slot_index": 0, "activity": "sight",
             "dwell_min": 60.0, "dwell_fixed": False},
            {"place_id": "14916341928181875966", "kind": "slot", "slot_index": 1, "activity": "coffee",
             "dwell_min": 30.0, "dwell_fixed": False}]}],
    "places": {
        "3676382838556882236": {
            "place_id": "3676382838556882236", "name": "\"Theodor Aman\" Museum",
            "type_label": "historic house museum and art museum", "primary_type": "museum", "theme": "culture_sights",
            "theme_group": "sights", "rating": 4.7, "rating_count": 710,
            "summary": "A compact 19th-century artist's home and studio filled with Theodor Aman's paintings, "
                       "engravings, carved furniture, stained glass, and preserved interiors.",
            "summary_lang": "en", "photos": [{"key": "photos_cid/3676382838556882236/00_all.jpg", "url": None}],
            "google_maps_url": "https://www.google.com/maps?cid=3676382838556882236",
            "google_place_id": "ChIJ5YmJ9UX_sUARPHWGnrYiBTM",
            "address": "Strada C. A. Rosetti 8, 010283 București, Romania", "business_status": "operational",
            "lat": 44.4402492, "lon": 26.098125699999997, "price_level": None},
        "14916341928181875966": {
            "place_id": "14916341928181875966", "name": "boteca13", "type_label": "specialty coffee cafe",
            "primary_type": "cafe", "theme": "food_drink", "theme_group": "food_drink", "rating": 5.0,
            "rating_count": 406,
            "summary": "A compact specialty coffee spot in central Bucharest with standout flat whites, cold brew "
                       "tonic, and a warm, neighborly feel.",
            "summary_lang": "en", "photos": [{"key": "photos_cid/14916341928181875966/00_vibe.jpg", "url": None}],
            "google_maps_url": "https://www.google.com/maps?cid=14916341928181875966",
            "google_place_id": "ChIJp13bnX7_sUAR_sSLGfR9Ac8",
            "address": "Strada Boteanu 3, 010027 București, Romania", "business_status": "operational",
            "lat": 44.4395933, "lon": 26.098287499999998, "price_level": None}},
}
# error bodies of the mini golden set (M08 plan; M04 edit chains) + the generic shapes
ERROR_EXAMPLES = {
    "duplicate_activity": {"summary": "422: an activity twice", "value": {"error": {
        "code": "duplicate_activity",
        "message": "Каждый тип активности можно выбрать только один раз: Достопримечательность.",
        "params": {"activity": "sight", "slot_index": 1}}}},
    "validation_error": {"summary": "422: request format", "value": {"error": {
        "code": "validation_error", "message": "Invalid request: body.variants — Input should be less than or "
                                               "equal to 5.",
        "params": {"errors": [{"loc": ["body", "variants"], "type": "less_than_equal",
                               "msg": "Input should be less than or equal to 5", "input": 7, "ctx": {"le": 5}}]}}}},
    "unknown_place": {"summary": "404: not in the catalog", "value": {"error": {
        "code": "unknown_place", "message": "Место 3333333333333333333 не найдено в каталоге.",
        "params": {"place_id": "3333333333333333333", "field": "place_id"}}}},
    "place_already_in_route": {"summary": "409: already a stop", "value": {"error": {
        "code": "place_already_in_route", "message": "Это место уже в маршруте.",
        "params": {"place_id": "18000000000000395950"}}}},
    "place_closed_forever": {"summary": "422: permanently closed", "value": {"error": {
        "code": "place_closed_forever", "message": "⛔ Закрыто навсегда по данным Google — добавить нельзя.",
        "params": {"place_id": "9100000000006179011", "name": "Craft Corner"}}}},
    "not_ready": {"summary": "503: still loading", "value": {"error": {
        "code": "not_ready", "message": "The service is still starting — try again in a minute.",
        "params": {"state": "starting"}}}},
    "busy": {"summary": "503: every load-guard slot of the worker is taken (Retry-After: 2)", "value": {"error": {
        "code": "busy", "message": "Сервис сейчас занят другими маршрутами — повторите через пару секунд.",
        "params": {"max_concurrent": 2, "retry_after_s": 2}}}},
    "bad_request": {"summary": "400: an unreadable request", "value": {"error": {
        "code": "bad_request", "message": "Некорректный запрос.", "params": {}}}},
    "payload_too_large": {"summary": "413: body over WALK_MAX_BODY_BYTES", "value": {"error": {
        "code": "payload_too_large", "message": "Слишком большой запрос.", "params": {"max_bytes": 1048576}}}},
    "internal_error": {"summary": "500: unexpected failure (details only in the service log)", "value": {"error": {
        "code": "internal_error", "message": "Внутренняя ошибка сервиса — повторите позже.",
        "params": {"request_id": "0f3c5d1e2b7a4c9d8e6f1a2b3c4d5e6f"}}}},
}
