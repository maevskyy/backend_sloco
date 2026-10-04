"""City catalog of the Walk Planner: the prepared per-city place table and everything looked up in it.

`CityCatalog` wraps the city's rows exactly as the planner reads them, plus lookups (row, hours,
business status, photos, Google ids), place cards / details for the API and a place search.

Two constructors produce the SAME prepared frame (row order, ``place_id`` as str, float64 numerics,
parsed ``wp_hours``, normalised ``business_status``), so the pipeline plans identically from either:

  * ``CityCatalog.from_frame(df, city)`` — the dashboard path: exactly what the Walk Planner page did
    to ``rec.locations`` (city filter, drop rows without coordinates, parse opening hours);
  * ``CityCatalog.from_bundle(bundle_dir)`` — the service path: ``<bundle>/walk_catalog.parquet``,
    written by the bundle builder from ``to_bundle_frame(df, photo_manifest)``.

Catalog facts (status, hours, names, coordinates) are always re-read from here by ``place_id`` — a
client never supplies them. ``place_id`` is the Google CID as a decimal string (it exceeds 2^53 and
int64), never cast to a number.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from .core import haversine_km
from .interest import cold_start, cold_start_available, cold_start_map
from .messages import PlannerInputError
from .slots import CLOSED_STATUS

__all__ = [
    "CATALOG_FILE", "MANIFEST_FILE", "GOOGLE_MAPS_URL", "CITY_TIMEZONES", "DEFAULT_TIMEZONE", "STATUS_ALIASES",
    "USED_COLUMNS", "DISPLAY_COLUMNS", "NUMERIC_COLUMNS", "BUNDLE_COLUMNS", "MAX_BUNDLE_PHOTOS", "CARD_PHOTOS",
    "DETAIL_PHOTOS", "SECTION_TITLES", "SEARCH_DISTANCE_WEIGHT", "SEARCH_TIERS",
    "parse_hours", "normalize_status", "text_series", "cold_start_scores", "photo_keys_by_place", "photo_url",
    "to_bundle_frame", "CityCatalog", "catalog_frames_equal",
]

CATALOG_FILE = "walk_catalog.parquet"
MANIFEST_FILE = "manifest.json"
GOOGLE_MAPS_URL = "https://www.google.com/maps?cid={}"
# City -> IANA timezone (the walk window and opening hours are city-local wall-clock time).
CITY_TIMEZONES = {
    "Bucharest": "Europe/Bucharest",
    "Berlin": "Europe/Berlin",
    "Tbilisi": "Asia/Tbilisi",
    "Kyiv": "Europe/Kyiv",
}
DEFAULT_TIMEZONE = "UTC"
# Google's enum names for the two statuses the catalog keeps.
STATUS_ALIASES = {"closed_permanently": "closed_forever", "closed_temporarily": "temporarily_closed"}

# Columns the pipeline reads (selection, interest, dwell, hours, status, navigation).
USED_COLUMNS = (
    "place_id", "city", "name", "latitude", "longitude", "theme", "theme_group", "primary_type",
    "ai_place_type_summary", "ai_card_summary", "google_user_rating_count", "bayesian_rating",
    "google_rating", "map_visibility_score", "opening_hours", "business_status", "google_place_id",
)
# Extra columns shown by place cards / the place screen.
DISPLAY_COLUMNS = (
    "address", "price_level", "ai_vibe", "ai_what_to_expect", "ai_food_and_drinks", "ai_price",
    "ai_service", "ai_the_move", "ai_watch_out", "ai_tags_csv", "ai_confidence",
)
NUMERIC_COLUMNS = ("latitude", "longitude", "google_rating", "google_user_rating_count",
                   "bayesian_rating", "map_visibility_score")
# Column order of walk_catalog.parquet (columns missing from the source are left out).
BUNDLE_COLUMNS = ("place_id", "google_place_id", "city", "name", "latitude", "longitude", "theme_group",
                  "theme", "primary_type", "ai_place_type_summary", "ai_card_summary", "google_rating",
                  "google_user_rating_count", "bayesian_rating", "map_visibility_score", "opening_hours",
                  "business_status") + DISPLAY_COLUMNS + ("photos", "photo_count")
MAX_BUNDLE_PHOTOS = 10
CARD_PHOTOS = 4               # the dashboard's stop card: one hero + three thumbnails
DETAIL_PHOTOS = 10

# Place screen sections: the AI text columns, labelled by theme_group where the column's meaning
# depends on it (build_city_catalog.py writes theme-specific content into the same columns).
_SECTION_COLUMNS = ("ai_vibe", "ai_what_to_expect", "ai_food_and_drinks", "ai_price", "ai_service",
                    "ai_the_move", "ai_watch_out")
_SECTION_KEYS = {
    "ai_vibe": "vibe", "ai_what_to_expect": "what_to_expect", "ai_price": "price",
    "ai_the_move": "the_move", "ai_watch_out": "watch_out",
}
_THEMED_SECTION_KEYS = {   # theme_group -> (ai_food_and_drinks key, ai_service key)
    "food_drink": ("food_and_drinks", "service"),
    "sights": ("the_sight", "significance"),
    "shopping": ("the_goods", "what_to_buy"),
    "things_to_do": ("the_experience", "service"),
}
SECTION_TITLES = {
    "vibe": ("Атмосфера", "Vibe"),
    "what_to_expect": ("Чего ожидать", "What to expect"),
    "food_and_drinks": ("Еда и напитки", "Food & drinks"),
    "the_sight": ("Что посмотреть", "The sight"),
    "the_goods": ("Что здесь продают", "The goods"),
    "the_experience": ("Впечатления", "The experience"),
    "price": ("Цены", "Price"),
    "service": ("Сервис", "Service"),
    "significance": ("Значение", "Significance"),
    "what_to_buy": ("Что купить", "What to buy"),
    "the_move": ("Как лучше", "The move"),
    "watch_out": ("Учтите", "Watch out"),
}
# Search: within a match tier, log(reviews) minus this many units per km from `near`.
SEARCH_DISTANCE_WEIGHT = 1.0
SEARCH_TIERS = ("exact", "prefix", "substring", "all_words", "address")


# --------------------------------------------------------------------------- #
# Cell helpers (exact ports of the dashboard's)
# --------------------------------------------------------------------------- #
def parse_hours(v) -> Optional[list]:
    """Catalog `opening_hours` cell -> [[open, close], ...] week minutes, or None (unknown).

    `dashboard_app._walk_parse_hours`: only a JSON list string parses; NaN, "", other text and
    invalid JSON are unknown (= treated as open). The structure itself is not validated."""
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def normalize_status(v) -> str:
    """Business status cell -> "" | "closed_forever" | "temporarily_closed" (aliases mapped)."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip().lower()
    s = STATUS_ALIASES.get(s, s)
    return s if s in CLOSED_STATUS else ""


def text_series(df: pd.DataFrame) -> pd.Series:
    """Lowercased searchable text per place (primary_type + ai type + name) — the keyword-slot text
    (`dashboard_app._walk_text_series`)."""
    parts = [df[c].fillna("").astype(str) for c in ("primary_type", "ai_place_type_summary", "name") if c in df.columns]
    if not parts:
        return pd.Series([""] * len(df), index=df.index)
    combined = parts[0]
    for p in parts[1:]:
        combined = combined + " " + p
    return combined.str.lower()


def cold_start_scores(rows: pd.DataFrame) -> Optional[pd.Series]:
    """Cold-start (no favourites) interest per row, index-aligned with `rows`; None when the catalog
    has none of the columns it needs (the dashboard then had an empty interest map).

    The formula lives in ONE place, ``interest.cold_start`` (`dashboard_app._walk_interest_map`'s cold
    start, verbatim): how famous (log review count, normalised by the city's maximum — closed places
    included) + how good (rating above 4.0), with the map_visibility_score / google_rating fallback."""
    if not cold_start_available(rows):
        return None
    return pd.Series(cold_start(rows), index=rows.index)


def _clean_text(v) -> Optional[str]:
    """Non-empty text of a cell, else None (NaN / None / blank)."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    return s or None


def _num(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and not math.isinf(x) else None


def _fold(text) -> str:
    """Search normal form: NFKD, diacritics dropped, casefolded, whitespace collapsed."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.casefold().split())


_WORD_SPLIT = re.compile(r"[\s,;:/|()\[\]\"«»]+")


# --------------------------------------------------------------------------- #
# Photos (photo manifest -> ordered keys)
# --------------------------------------------------------------------------- #
def _photo_key(place_id: str, path) -> Optional[str]:
    """`photos_cid/<cid>/<NN>_<vibe|all>.jpg` key of a manifest file entry (anchored at photos_cid)."""
    text = _clean_text(path)
    if text is None:
        return None
    parts = Path(text.replace("\\", "/")).parts
    if "photos_cid" in parts:
        return "/".join(parts[parts.index("photos_cid"):])
    return f"photos_cid/{place_id}/{parts[-1]}"


def photo_keys_by_place(photo_manifest: Optional[pd.DataFrame], max_photos: Optional[int] = MAX_BUNDLE_PHOTOS
                        ) -> dict[str, list[str]]:
    """place_id -> photo keys in the dashboard's order (`load_photo_manifest`: source vibe, then
    review, then anything else (all); then photo_index; stable), at most `max_photos` per place.

    Accepts the build_city_catalog photo manifest (`place_id, photo_source, local_file, photo_index`)
    or the dashboard's loaded manifest (`resolved_file`). File existence is not checked here."""
    if photo_manifest is None or len(photo_manifest) == 0 or "place_id" not in photo_manifest.columns:
        return {}
    m = photo_manifest.copy()
    m["place_id"] = m["place_id"].astype(str)
    path_col = next((c for c in ("bundle_relative_file", "local_file", "resolved_file", "original_file",
                                 "resized_file") if c in m.columns), None)
    if path_col is None:
        return {}
    rank = m["photo_source"].map({"vibe": 0, "review": 1}).fillna(2) if "photo_source" in m.columns \
        else pd.Series(2, index=m.index)
    if "photo_index" in m.columns:
        idx = pd.to_numeric(m["photo_index"], errors="coerce")
    elif "photo_index_in_place" in m.columns:
        idx = pd.to_numeric(m["photo_index_in_place"], errors="coerce")
    else:
        idx = pd.Series(9999, index=m.index)
    m = m.assign(_rank=rank, _idx=idx.fillna(9999))
    m = m.sort_values(["place_id", "_rank", "_idx"], kind="stable")
    out: dict[str, list[str]] = {}
    for pid, path in zip(m["place_id"], m[path_col]):
        key = _photo_key(pid, path)
        if key is None:
            continue
        keys = out.setdefault(pid, [])
        if key not in keys and (max_photos is None or len(keys) < max_photos):
            keys.append(key)
    return out


def photo_url(key: str, photo_base_url: Optional[str]) -> Optional[str]:
    """PHOTO_BASE_URL + key (None without a base URL)."""
    if not photo_base_url:
        return None
    return photo_base_url.rstrip("/") + "/" + key.lstrip("/")


# --------------------------------------------------------------------------- #
# Bundle frame (written by the bundle builder as walk_catalog.parquet)
# --------------------------------------------------------------------------- #
def to_bundle_frame(df: pd.DataFrame, photo_manifest_df: Optional[pd.DataFrame] = None,
                    max_photos: int = MAX_BUNDLE_PHOTOS) -> pd.DataFrame:
    """The walk_catalog table of a data bundle, from a raw catalog frame (e.g. ``pd.read_csv`` of
    ``locations_<city>_all.csv``) and its photo manifest.

    Rows stay in the source order (ties in candidate ranking are broken by row order). Columns:
    BUNDLE_COLUMNS that exist in the source, with `place_id` as str, numerics float64,
    `opening_hours` the original JSON text or None, `business_status` normalised ("" when open),
    text columns str or None, `photos` = list of photo keys in dashboard order and `photo_count`.
    `CityCatalog.from_bundle` on this table equals `CityCatalog.from_frame` on `df`."""
    out = pd.DataFrame(index=pd.RangeIndex(len(df)))
    src = df.reset_index(drop=True)
    for col in BUNDLE_COLUMNS:
        if col in ("photos", "photo_count"):
            continue
        if col not in src.columns:
            continue
        s = src[col]
        if col == "place_id":
            out[col] = pd.Series([str(v) for v in s], dtype=object)
        elif col in NUMERIC_COLUMNS:
            out[col] = pd.to_numeric(s, errors="coerce").astype("float64")
        elif col == "business_status":
            out[col] = pd.Series([normalize_status(v) for v in s], dtype=object)
        elif col == "opening_hours":
            out[col] = pd.Series([v if isinstance(v, str) and v.strip() else None for v in s], dtype=object)
        else:
            out[col] = pd.Series([None if (v is None or (isinstance(v, float) and v != v)) else str(v) for v in s],
                                 dtype=object)
    keys = photo_keys_by_place(photo_manifest_df, max_photos=max_photos)
    pids = out["place_id"] if "place_id" in out.columns else pd.Series([""] * len(out))
    out["photos"] = pd.Series([list(keys.get(str(p), [])) for p in pids], dtype=object)
    out["photo_count"] = pd.Series([len(x) for x in out["photos"]], dtype="int32")
    return out


# --------------------------------------------------------------------------- #
# The catalog
# --------------------------------------------------------------------------- #
def _prepare_rows(df: pd.DataFrame, city: Optional[str]) -> tuple[pd.DataFrame, Optional[str]]:
    """The Walk Planner page's city rows (`page_walk_planner` L4484-4492), plus place_id as str and
    a normalised business_status: filter to the city when the frame has a city column, drop rows
    without coordinates, parse opening hours into `wp_hours`. Row order (and index) preserved."""
    if not {"latitude", "longitude", "place_id"}.issubset(df.columns):
        raise ValueError("the catalog needs latitude / longitude / place_id columns")
    if "city" in df.columns and df["city"].notna().any():
        cities = sorted(str(c) for c in df["city"].dropna().unique())
        if city is None:
            city = cities[0]                       # the page's default selection (first city)
        elif city not in cities:
            folded = {c.casefold(): c for c in cities}
            if str(city).casefold() not in folded:
                raise ValueError(f"city {city!r} is not in the catalog (has: {', '.join(cities)})")
            city = folded[str(city).casefold()]
        rows = df[df["city"].astype(str) == city]
    else:
        rows = df
    rows = rows.dropna(subset=["latitude", "longitude"]).copy()
    rows["place_id"] = rows["place_id"].astype(str)
    if "business_status" in rows.columns:
        rows["business_status"] = [normalize_status(v) for v in rows["business_status"]]
    if "opening_hours" in rows.columns:
        rows["wp_hours"] = rows["opening_hours"].map(parse_hours)
    return rows, city


class CityCatalog:
    """The places of one city, prepared for the planner (see the module docstring).

    Attributes: ``rows`` (the prepared frame; the dashboard's ``city_rows``), ``city``, ``timezone``,
    ``center`` (mean lat/lon over all rows — closed places included — the default start and the
    area of shape "free"), ``bbox`` ([minLon, minLat, maxLon, maxLat]), ``has_hours`` (the catalog
    has opening hours), ``version`` (bundle id, or ``frame:<sha8>`` of the used columns),
    ``manifest`` (bundle manifest or None)."""

    def __init__(self, rows: pd.DataFrame, *, city: Optional[str] = None, timezone: Optional[str] = None,
                 version: Optional[str] = None, manifest: Optional[dict] = None,
                 photos: Optional[dict[str, list[str]]] = None, source: str = "frame"):
        if rows.empty:
            raise ValueError("no places with coordinates for this city")
        self.rows = rows
        self.city = city
        self.timezone = timezone or CITY_TIMEZONES.get(str(city), DEFAULT_TIMEZONE)
        self.manifest = manifest
        self.source = source
        self._version = version
        self.has_hours = "wp_hours" in rows.columns
        # page_walk_planner L4540: mean over the city's rows
        self.center = (float(rows["latitude"].astype(float).mean()), float(rows["longitude"].astype(float).mean()))
        lat = rows["latitude"].astype(float)
        lon = rows["longitude"].astype(float)
        self.bbox = [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]
        self._ids = [str(p) for p in rows["place_id"]]
        self._pos: dict[str, int] = {}
        for i, pid in enumerate(self._ids):
            self._pos.setdefault(pid, i)            # first occurrence (place_id is unique in practice)
        self._photos = photos if photos is not None else self._photos_from_rows(rows)
        # Lazy caches. Each is computed into a local and published by ONE attribute assignment, so a
        # concurrent reader sees either nothing (and computes it too) or the complete value.
        self._cold: Optional[dict[str, float]] = None
        self._cold_arr: Optional[np.ndarray] = None
        self._text: Optional[pd.Series] = None
        self._search: Optional[tuple] = None       # (folded names, folded addresses, statuses, review counts)
        self._search_lock = threading.Lock()

    # ----------------------------------------------------------------- constructors
    @classmethod
    def from_frame(cls, df: pd.DataFrame, city: Optional[str] = None, version: Optional[str] = None,
                   photo_manifest: Optional[pd.DataFrame] = None) -> "CityCatalog":
        """Dashboard path: the page's preparation of ``rec.locations`` (any frame with the catalog
        columns). `city` None picks the first city, like the page's default. `photo_manifest`
        (optional) supplies photo keys for cards."""
        rows, city = _prepare_rows(df, city)
        photos = photo_keys_by_place(photo_manifest) if photo_manifest is not None else None
        return cls(rows, city=city, version=version, photos=photos, source="frame")

    @classmethod
    def from_bundle(cls, bundle_dir) -> "CityCatalog":
        """Service path: ``<bundle_dir>/walk_catalog.parquet`` (+ ``manifest.json`` when present:
        city, timezone, bundle_id). `bundle_dir` may also be the parquet file itself."""
        path = Path(bundle_dir)
        parquet = path if path.suffix == ".parquet" else path / CATALOG_FILE
        manifest_path = parquet.parent / MANIFEST_FILE
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
        df = pd.read_parquet(parquet)
        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        photos = None
        if "photos" in df.columns:
            photos = {str(pid): [str(k) for k in (keys if keys is not None else [])]
                      for pid, keys in zip(df["place_id"], df["photos"])}
            df = df.drop(columns=["photos"])
        city = (manifest or {}).get("city")
        rows, city = _prepare_rows(df, city)
        version = (manifest or {}).get("bundle_id") or "bundle:" + _file_sha256(parquet)[:8]
        return cls(rows, city=city, timezone=(manifest or {}).get("timezone"), version=version,
                   manifest=manifest, photos=photos, source="bundle")

    def __getstate__(self) -> dict:              # picklable / deep-copyable (no lock inside)
        state = dict(self.__dict__)
        state.pop("_search_lock", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._search_lock = threading.Lock()

    @staticmethod
    def _photos_from_rows(rows: pd.DataFrame) -> dict[str, list[str]]:
        if "photos" not in rows.columns:
            return {}
        return {str(pid): [str(k) for k in keys] for pid, keys in zip(rows["place_id"], rows["photos"])
                if keys is not None and not (isinstance(keys, float) and keys != keys)}

    # ----------------------------------------------------------------- identity
    @property
    def version(self) -> str:
        """Bundle id, or ``frame:<sha8>`` of the used columns (row order included)."""
        if self._version is None:
            cols = [c for c in USED_COLUMNS if c in self.rows.columns]
            h = hashlib.sha256()
            for col in cols:
                h.update(col.encode())
                h.update(pd.util.hash_pandas_object(self.rows[col].astype(str), index=False).values.tobytes())
            self._version = "frame:" + h.hexdigest()[:8]
        return self._version

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return f"CityCatalog(city={self.city!r}, rows={len(self.rows)}, version={self.version!r})"

    @property
    def place_ids(self) -> list[str]:
        """All place ids, in row order."""
        return list(self._ids)

    def warm(self) -> "CityCatalog":
        """Compute the lazy caches now (version, cold-start interest, keyword text, search index) —
        call once before serving so the first requests do not pay for them."""
        self.version
        self.cold_interest()
        self.cold_interest_array()
        self.text_series()
        self._search_index()
        return self

    # ----------------------------------------------------------------- lookups
    def has(self, place_id) -> bool:
        return str(place_id) in self._pos

    def position(self, place_id) -> int:
        """Row position of a place (KeyError when unknown)."""
        return self._pos[str(place_id)]

    def row(self, place_id) -> pd.Series:
        """The prepared catalog row of a place (KeyError when unknown)."""
        return self.rows.iloc[self._pos[str(place_id)]]

    def name_of(self, place_id) -> str:
        r = self.row(place_id)
        return str(r.get("name") or r["place_id"])

    def coord_of(self, place_id) -> tuple[float, float]:
        r = self.row(place_id)
        return float(r["latitude"]), float(r["longitude"])

    def hours_of(self, place_id) -> Optional[list]:
        """Parsed opening hours ([[open, close], ...] week minutes) or None (unknown / no hours column)."""
        if not self.has_hours or not self.has(place_id):
            return None
        return self.rows["wp_hours"].iloc[self._pos[str(place_id)]]

    def status_of(self, place_id) -> str:
        """"" (operational) | "closed_forever" | "temporarily_closed" ("" when unknown)."""
        if "business_status" not in self.rows.columns or not self.has(place_id):
            return ""
        return normalize_status(self.rows["business_status"].iloc[self._pos[str(place_id)]])

    def photos(self, place_id, limit: Optional[int] = None) -> list[str]:
        """Photo keys ``photos_cid/<cid>/<NN>_<vibe|all>.jpg``, vibe -> review -> all, then photo_index."""
        keys = self._photos.get(str(place_id), [])
        return list(keys if limit is None else keys[:limit])

    def google_maps_url(self, place_id) -> str:
        """`https://www.google.com/maps?cid=<place_id>` (the place id IS the Google CID)."""
        return GOOGLE_MAPS_URL.format(place_id)

    def google_place_id(self, place_id) -> Optional[str]:
        """Google ``ChIJ…`` place id (named pins in navigation links), or None."""
        if "google_place_id" not in self.rows.columns or not self.has(place_id):
            return None
        v = self.rows["google_place_id"].iloc[self._pos[str(place_id)]]
        return v if isinstance(v, str) and v.startswith("ChIJ") else None

    def text_series(self) -> pd.Series:
        """The keyword-slot text of every row (cached; see `text_series`)."""
        text = self._text
        if text is None:
            text = self._text = text_series(self.rows)
        return text

    # ----------------------------------------------------------------- interest
    def cold_interest(self) -> dict[str, float]:
        """place_id -> cold-start interest over ALL prepared rows (the dashboard's no-favourites map:
        ``interest.cold_start_map``; empty when the catalog has no popularity column, as on the page)."""
        cold = self._cold
        if cold is None:
            cold = cold_start_map(self.rows) if cold_start_available(self.rows) else {}
            self._cold = cold
        return cold

    def cold_interest_array(self) -> np.ndarray:
        """Cold-start interest per row (float64, row order; 0 where the catalog has no signal) —
        ``interest.cold_start``."""
        arr = self._cold_arr
        if arr is None:
            arr = self._cold_arr = cold_start(self.rows)
        return arr

    # ----------------------------------------------------------------- cards
    def place_card(self, place_id, photo_base_url: Optional[str] = None, photo_limit: int = CARD_PHOTOS) -> dict:
        """API PlaceCard of a place (KeyError when unknown). Photo URLs only with a base URL."""
        r = self.row(place_id)
        pid = str(r["place_id"])
        status = normalize_status(r.get("business_status"))
        rating = _num(r.get("google_rating"))
        count = _num(r.get("google_user_rating_count"))
        return {
            "place_id": pid,
            "name": str(r.get("name") or pid),
            "type_label": _clean_text(r.get("ai_place_type_summary")) or _clean_text(r.get("primary_type")),
            "primary_type": _clean_text(r.get("primary_type")),
            "theme": _clean_text(r.get("theme")),
            "theme_group": _clean_text(r.get("theme_group")),
            "rating": rating if rating is not None and rating > 0 else None,
            "rating_count": int(count) if count is not None else None,
            "summary": _clean_text(r.get("ai_card_summary")),
            "summary_lang": "en",
            "photos": [{"key": k, "url": photo_url(k, photo_base_url)} for k in self.photos(pid, photo_limit)],
            "google_maps_url": self.google_maps_url(pid),
            "google_place_id": _clean_text(r.get("google_place_id")),
            "address": _clean_text(r.get("address")),
            "business_status": status or "operational",
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "price_level": _clean_text(r.get("price_level")),
        }

    def place_detail(self, place_id, photo_base_url: Optional[str] = None, photo_limit: int = DETAIL_PHOTOS) -> dict:
        """Place screen: the PlaceCard (up to 10 photos) + AI text sections labelled by theme_group,
        tags, AI confidence, timezone and the week's opening hours (structured, per weekday)."""
        from .present import hours_on_day   # formatters live in present (no import cycle at load)

        r = self.row(place_id)
        card = self.place_card(place_id, photo_base_url=photo_base_url, photo_limit=photo_limit)
        group = card["theme_group"] or ""
        fd_key, svc_key = _THEMED_SECTION_KEYS.get(group, ("food_and_drinks", "service"))
        sections = []
        for col in _SECTION_COLUMNS:
            text = _clean_text(r.get(col))
            if text is None:
                continue
            key = fd_key if col == "ai_food_and_drinks" else svc_key if col == "ai_service" else _SECTION_KEYS[col]
            ru, en = SECTION_TITLES[key]
            sections.append({"key": key, "column": col, "title_ru": ru, "title_en": en, "text": text})
        tags_text = _clean_text(r.get("ai_tags_csv"))
        hours = self.hours_of(place_id)
        week = None if hours is None else [hours_on_day(hours, d * 1440) for d in range(7)]
        return {**card, "sections": sections,
                "tags": [t.strip() for t in tags_text.split(",") if t.strip()] if tags_text else [],
                "ai_confidence": _clean_text(r.get("ai_confidence")),
                "timezone": self.timezone, "opening_hours_week": week,
                "opening_hours_known": hours is not None}

    # ----------------------------------------------------------------- search
    def _search_index(self) -> tuple[list[str], list[str], list[str], np.ndarray]:
        """(folded names, folded addresses, statuses, review counts) per row — built once, under a
        lock, into locals and published as ONE tuple: concurrent first searches (the service's
        threadpool) wait for it instead of reading a half-built index."""
        index = self._search
        if index is not None:
            return index
        with self._search_lock:
            if self._search is None:
                n = len(self.rows)
                names = self.rows["name"] if "name" in self.rows.columns else pd.Series([""] * n)
                addrs = self.rows["address"] if "address" in self.rows.columns else pd.Series([""] * n)
                fold_names = [_fold(_clean_text(v) or "") for v in names]
                fold_addr = [_fold(_clean_text(v) or "") for v in addrs]
                statuses = [normalize_status(v) for v in self.rows["business_status"]] \
                    if "business_status" in self.rows.columns else [""] * n
                counts = pd.to_numeric(self.rows["google_user_rating_count"], errors="coerce").fillna(0.0) \
                    .clip(lower=0).to_numpy(dtype="float64") if "google_user_rating_count" in self.rows.columns \
                    else np.zeros(n)
                self._search = (fold_names, fold_addr, statuses, counts)
            return self._search

    def search(self, q: str, near: Optional[tuple[float, float]] = None, limit: int = 20,
               include_closed_forever: bool = False, photo_base_url: Optional[str] = None) -> list[dict]:
        """Places matching `q` by name (diacritics / case insensitive), best first.

        Tiers: exact name > name prefix > name substring > every query word in the name > address
        match. Within a tier: log(1 + reviews), minus SEARCH_DISTANCE_WEIGHT per km from `near`
        (lat, lon) when given; then name, place_id. ``closed_forever`` places are left out unless
        `include_closed_forever`; ``temporarily_closed`` ones are included (their
        `business_status` says so). A `near` that is not a finite (lat, lon) within ±90 / ±180, or a
        `limit` that is not an integer, raises PlannerInputError ``validation_error`` (422)."""
        near = _search_point(near)
        limit = _search_limit(limit)
        qn = _fold(q)
        if not qn:
            return []
        words = [w for w in _WORD_SPLIT.split(qn) if w]
        names, addrs, statuses, counts = self._search_index()
        hits = []
        for i, (nm, ad) in enumerate(zip(names, addrs)):
            if nm == qn:
                tier = 0
            elif nm.startswith(qn):
                tier = 1
            elif qn in nm:
                tier = 2
            elif words and all(w in nm for w in words):
                tier = 3
            elif ad and (qn in ad or (words and all(w in ad for w in words))):
                tier = 4
            else:
                continue
            if statuses[i] == "closed_forever" and not include_closed_forever:
                continue
            hits.append((tier, i))
        lat = self.rows["latitude"].to_numpy(dtype="float64")
        lon = self.rows["longitude"].to_numpy(dtype="float64")
        scored = []
        for tier, i in hits:
            dist_km = haversine_km(near[0], near[1], lat[i], lon[i]) if near is not None else None
            score = math.log1p(float(counts[i])) - (SEARCH_DISTANCE_WEIGHT * dist_km if dist_km is not None else 0.0)
            scored.append((tier, -score, names[i], self._ids[i], i, dist_km))
        scored.sort(key=lambda x: x[:4])
        out = []
        for tier, _neg, _nm, pid, i, dist_km in scored[:limit]:
            card = self.place_card(pid, photo_base_url=photo_base_url, photo_limit=1)
            item = {k: card[k] for k in ("place_id", "name", "type_label", "primary_type", "theme", "theme_group",
                                         "rating", "rating_count", "address", "business_status", "lat", "lon",
                                         "google_maps_url")}
            item["photo"] = card["photos"][0] if card["photos"] else None
            item["match"] = SEARCH_TIERS[tier]
            if dist_km is not None:
                item["distance_m"] = int(round(dist_km * 1000.0))
            out.append(item)
        return out


def _search_point(near) -> Optional[tuple[float, float]]:
    """`near` of a search as (lat, lon) floats; None stays None."""
    if near is None:
        return None
    try:
        lat, lon = (float(v) for v in near)
    except (TypeError, ValueError, OverflowError):
        lat = lon = math.nan
    if not (math.isfinite(lat) and math.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise PlannerInputError("validation_error", {"field": "near", "reason": "expected a finite (lat, lon)"})
    return lat, lon


def _search_limit(limit) -> int:
    """`limit` of a search as an int >= 0 (an integral float is accepted; booleans, NaN / ±Infinity are not)."""
    if isinstance(limit, float) and math.isfinite(limit) and limit.is_integer():
        limit = int(limit)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise PlannerInputError("validation_error", {"field": "limit", "reason": "expected an integer"})
    return max(0, limit)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def catalog_frames_equal(a: CityCatalog, b: CityCatalog, columns: Optional[Iterable[str]] = None) -> list[str]:
    """Columns (of `columns`, default: the used columns + wp_hours) on which two catalogs' prepared
    rows differ in value — row order included, index ignored. Empty list = equivalent."""
    cols = list(columns) if columns is not None else [c for c in USED_COLUMNS + ("wp_hours",)]
    bad = []
    for col in cols:
        ina, inb = col in a.rows.columns, col in b.rows.columns
        if ina != inb:
            bad.append(col)
            continue
        if not ina:
            continue
        va, vb = list(a.rows[col]), list(b.rows[col])
        if len(va) != len(vb) or any(not _same_cell(x, y) for x, y in zip(va, vb)):
            bad.append(col)
    return bad


def _same_cell(x: Any, y: Any) -> bool:
    xm = x is None or (isinstance(x, float) and x != x)
    ym = y is None or (isinstance(y, float) and y != y)
    if xm or ym:
        return xm and ym
    if isinstance(x, (float, np.floating)) or isinstance(y, (float, np.floating)):
        return float(x) == float(y)
    return x == y
