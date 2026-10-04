"""Activity slots, styles and shapes of the Walk Planner: stable codes plus the selection constants.

A walk is an ordered list of activity *slots* ("sight -> coffee -> park -> dinner"). Every slot type,
style and shape is identified by a stable code in code and in the API; the Russian / English labels
are display-only. The codes, in the dashboard's order:

  activities  sight, coffee, food, bar, park, market, entertainment, shopping
  styles      max, chill, scenic
  shapes      loop, one_way, free

The selection rules and constants are copied verbatim from the Walk Planner page of the research
dashboard (``dashboard_app.py`` L4109-4172, the pre-refactor single source). The golden baseline pins
them: changing a value here changes plans, so do it only in a release that regenerates the goldens.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .core import STYLE_PRESETS, dwell_for

__all__ = [
    "NON_VENUE_RE", "NATURE_TYPES", "MARKET_TYPES", "SIGHT_DENY", "ActivityType", "ACTIVITY_TYPES", "ACTIVITY_CODES",
    "ACTIVITY_BY_LABEL_RU", "LABEL_RU_BY_ACTIVITY", "DEFAULT_SLOTS", "LEGACY_GROUP", "MIN_FILTERED", "NEAR_WEIGHT",
    "EXTRA_K", "NEAR_MIN_REVIEWS", "DEFAULT_TOP_K", "SCENIC_EXTRA_ACTIVITIES", "FALLBACK_EXTRA_ACTIVITY",
    "DWELL_CHOICES", "CLOSED_STATUS", "DAYS_RU", "DAYS_RU_FULL", "DAYS_EN", "BIN_HINT_RU", "Choice", "STYLES",
    "SHAPES", "STYLE_BY_LABEL_RU", "SHAPE_BY_LABEL_RU", "DEFAULT_STYLE", "DEFAULT_SHAPE",
    "activity", "activity_label", "dwell_choice_label_ru",
]

# --------------------------------------------------------------------------- #
# Precision filters on top of a slot's theme (see ActivityType)
# --------------------------------------------------------------------------- #
# An AI place type that reads like a business, not a venue (tour operator, plant nursery, supplier
# ...). Applied only to `ai_place_type_summary` and only for slots with `ai_deny`.
NON_VENUE_RE = re.compile(
    r"\b(?:tour|tours|organizer|organiser|agency|supplier|rental|school|camp|company|service|"
    r"office|nursery|wholesaler|contractor|association|foundation|ngo|nonprofit)\b", re.I)
NATURE_TYPES = frozenset({
    "park", "garden", "botanical_garden", "state_park", "national_park", "city_park", "lake",
    "swimming_lake", "hiking_area", "zoo", "aquarium", "promenade", "viewpoint", "scenic_spot",
    "nature_preserve", "forest", "beach", "river", "waterfront", "observation_deck", "arboretum",
    "wildlife_park", "ecological_park", "picnic_ground", "island", "pond", "reservoir", "trail",
    "walking_trail", "cycling_path", "greenway", "boardwalk", "pier", "marina"})
MARKET_TYPES = frozenset({
    "farmers_market", "town_square", "traditional_market", "produce_market", "market",
    "flea_market", "food_market", "fish_market", "christmas_market", "plaza", "public_square",
    "bazaar", "night_market", "street_market", "market_hall", "square", "pedestrian_street",
    "shopping_street"})
SIGHT_DENY = frozenset({
    "drinking_water_fountain", "park", "dog_park", "store", "souvenir_store", "gift_shop",
    "travel_agency", "tour_operator", "tour_agency", "walking_tour", "cultural_center",
    "community_center", "school", "university", "library"})


@dataclass(frozen=True)
class ActivityType:
    """One activity slot type.

    group      : `theme_group` the slot lives in (gates keyword matches).
    themes     : catalog `theme` values that fill the slot when the catalog has them (the per-city
                 all-theme catalog). Food slots have themes=(): everything there is theme
                 "food_drink", so they split by keywords.
    keywords   : fallback when the catalog has no such theme: substring match on
                 primary_type + AI place type + name, gated by theme_group.
    dwell_key  : `DWELL_MINUTES` key of the type's base visit length; also the `Candidate.theme`
                 of every candidate the slot produces.
    type_allow / type_deny : primary_type filters applied on top of the theme match (relaxed when
                 they leave fewer than MIN_FILTERED places).
    ai_deny    : also drop rows whose AI place type reads like a business (NON_VENUE_RE).
    """

    code: str
    label_ru: str
    label_en: str
    group: str
    themes: tuple[str, ...]
    keywords: tuple[str, ...]
    dwell_key: str
    type_allow: Optional[frozenset] = None
    type_deny: Optional[frozenset] = None
    ai_deny: bool = False

    @property
    def base_dwell_min(self) -> float:
        """The type's base visit length (minutes) — what radius sizing and the hours prefilter use."""
        return dwell_for(self.dwell_key)


# Dashboard order (= the order of the "Слоты активностей" options).
ACTIVITY_TYPES: dict[str, ActivityType] = {a.code: a for a in (
    ActivityType("sight", "Достопримечательность", "Sight", group="sights",
                 themes=("culture_sights", "religious_sights"),
                 keywords=("museum", "monument", "landmark", "gallery", "historic", "cathedral", "church",
                           "tower", "bridge", "castle", "palace", "memorial", "attraction", "sight"),
                 dwell_key="culture_sights", type_deny=SIGHT_DENY, ai_deny=True),
    ActivityType("coffee", "Кофе", "Coffee", group="food_drink", themes=(),
                 keywords=("coffee", "cafe", "café", "espresso", "roaster"), dwell_key="coffee"),
    ActivityType("food", "Еда / ресторан", "Food / restaurant", group="food_drink", themes=(),
                 keywords=("restaurant", "bistro", "trattoria", "dining", "eatery", "grill", "kitchen",
                           "pizzeria"),
                 dwell_key="restaurant"),
    ActivityType("bar", "Бар / напитки", "Bar / drinks", group="food_drink", themes=(),
                 keywords=("bar", "pub", "cocktail", "wine", "brewery", "lounge"), dwell_key="bar"),
    ActivityType("park", "Парк / природа", "Park / nature", group="sights", themes=("nature_outdoors",),
                 keywords=("park", "garden", "nature", "lake", "promenade", "viewpoint", "forest", "riverside"),
                 dwell_key="nature_outdoors", type_allow=NATURE_TYPES, ai_deny=True),
    ActivityType("market", "Рынок / площадь", "Market / square", group="sights", themes=("markets_walks",),
                 keywords=("market", "bazaar", "square", "plaza"),
                 dwell_key="markets_walks", type_allow=MARKET_TYPES, ai_deny=True),
    ActivityType("entertainment", "Развлечение", "Entertainment", group="things_to_do",
                 themes=("performing_arts", "leisure_active"),
                 keywords=("theater", "theatre", "cinema", "entertainment", "club", "show"),
                 dwell_key="performing_arts", ai_deny=True),
    ActivityType("shopping", "Шопинг", "Shopping", group="shopping", themes=("shopping_souvenirs",),
                 keywords=("shop", "store", "boutique", "market", "mall", "bookstore", "souvenir"),
                 dwell_key="shopping_souvenirs"),
)}
ACTIVITY_CODES: tuple[str, ...] = tuple(ACTIVITY_TYPES)
ACTIVITY_BY_LABEL_RU: dict[str, str] = {a.label_ru: a.code for a in ACTIVITY_TYPES.values()}
LABEL_RU_BY_ACTIVITY: dict[str, str] = {a.code: a.label_ru for a in ACTIVITY_TYPES.values()}
# The page's default slot selection.
DEFAULT_SLOTS: tuple[str, ...] = ("sight", "coffee", "park", "food")

# Without a `theme` column the sight/park/market/shopping slots fall back to the theme_group the
# legacy combined preset does have (things_to_do).
LEGACY_GROUP = {"sights": "things_to_do", "shopping": "things_to_do"}
# If the type/AI filters leave fewer than this many places in the radius, relax them (theme only)
# rather than returning an empty slot. Counted BEFORE the status and hours filters.
MIN_FILTERED = 3
# Minutes of walking one unit of interest is worth when picking the "near the start" candidates
# (the solver's "max" style value, whatever style is chosen).
NEAR_WEIGHT = float(STYLE_PRESETS["max"]["interest_weight"])
# Optional "on the way" stops per type (top-K by interest + top-K near the start).
EXTRA_K = 30
# The "near the start" half of a slot's pool only takes places with at least this many Google
# reviews — otherwise a 2-review courtyard next door beats a real park 5 minutes further.
NEAR_MIN_REVIEWS = 20
# Default candidates per slot ("Кандидатов на слот"; bench / CLI only).
DEFAULT_TOP_K = 8
# Extras when the style is "scenic"; otherwise the requested non-food types, or this one.
SCENIC_EXTRA_ACTIVITIES: tuple[str, ...] = ("park", "market")
FALLBACK_EXTRA_ACTIVITY = "sight"
# «⏱ Время на месте»: per-slot visit length options (None = estimated per place).
DWELL_CHOICES: list[Optional[int]] = [None, 10, 15, 20, 30, 45, 60, 75, 90, 120, 150, 180, 240]
# Google business status that must never become a slot / on-the-way stop.
CLOSED_STATUS = frozenset({"closed_forever", "temporarily_closed"})
# Weekday names (Monday = 0, like `date.weekday()` and the week-minute clock).
DAYS_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DAYS_RU_FULL = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
DAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Editor bin hint of the dashboard (UI only).
BIN_HINT_RU = "⤵ перетащите сюда место, чтобы убрать его из маршрута"


@dataclass(frozen=True)
class Choice:
    """A style or shape: stable code + display labels."""

    code: str
    label_ru: str
    label_en: str


# Dashboard order of the segmented controls.
STYLES: dict[str, Choice] = {c.code: c for c in (
    Choice("max", "Максимум мест", "Max places"),
    Choice("chill", "Размеренный", "Relaxed"),
    Choice("scenic", "Живописный", "Scenic"),
)}
SHAPES: dict[str, Choice] = {c.code: c for c in (
    Choice("one_way", "В одну сторону", "One way"),
    Choice("loop", "Петля", "Loop"),
    Choice("free", "По району", "Around the area"),
)}
STYLE_BY_LABEL_RU: dict[str, str] = {c.label_ru: c.code for c in STYLES.values()}
SHAPE_BY_LABEL_RU: dict[str, str] = {c.label_ru: c.code for c in SHAPES.values()}
DEFAULT_STYLE = "max"
DEFAULT_SHAPE = "loop"

assert set(STYLES) == set(STYLE_PRESETS), "styles drifted from core.STYLE_PRESETS"


def activity(code_or_label: str) -> ActivityType:
    """The ActivityType for a code (``"park"``) or a dashboard RU label (``"Парк / природа"``)."""
    if code_or_label in ACTIVITY_TYPES:
        return ACTIVITY_TYPES[code_or_label]
    if code_or_label in ACTIVITY_BY_LABEL_RU:
        return ACTIVITY_TYPES[ACTIVITY_BY_LABEL_RU[code_or_label]]
    raise KeyError(f"unknown activity {code_or_label!r}")


def activity_label(code: Optional[str], lang: str = "ru") -> str:
    """Display label of an activity code (the code itself, as text, when unknown — any value)."""
    a = ACTIVITY_TYPES.get(code) if isinstance(code, str) else None
    if a is None:
        return str(code or "")
    return a.label_en if lang == "en" else a.label_ru


def dwell_choice_label_ru(minutes: Optional[int]) -> str:
    """Option label of the «⏱ Сколько времени на месте» selectbox ('авто', '45 мин', '1 ч 30 мин')."""
    if minutes is None:
        return "авто"
    m = int(minutes)
    return f"{m} мин" if m < 60 else f"{m // 60} ч" + (f" {m % 60} мин" if m % 60 else "")
