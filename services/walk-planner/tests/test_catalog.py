"""Unit tests of walk_planner.catalog: the page's catalog preparation, lookups, photos, the bundle
table round trip (from_frame(csv) == from_bundle(to_bundle_frame(csv))), cold-start interest, place
cards / details and search. Offline; synthetic CSV + (when present) the real Bucharest catalog."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from walk_planner.catalog import (
    BUNDLE_COLUMNS,
    USED_COLUMNS,
    CityCatalog,
    catalog_frames_equal,
    cold_start_scores,
    normalize_status,
    parse_hours,
    photo_keys_by_place,
    text_series,
    to_bundle_frame,
)

BIG = 18444330390184695581            # a real CID: > int64 max, > 2^53
# The research repo root (services/walk_planner/tests -> parents[3]); None when this package is vendored fewer
# than 3 levels below "/" (e.g. /src/tests): there is then no real data, and those tests skip.
_UP = Path(__file__).resolve().parents
REPO = _UP[3] if len(_UP) > 3 else None
REAL_CSV = (REPO / "recommendation_system" / "ai_location_recommender" / "data" / "locations_bucharest_all.csv"
            if REPO is not None else None)
COLS = ["place_id", "name", "city", "latitude", "longitude", "theme", "theme_group", "primary_type",
        "ai_place_type_summary", "ai_card_summary", "google_user_rating_count", "bayesian_rating", "google_rating",
        "map_visibility_score", "opening_hours", "business_status", "google_place_id", "address", "price_level",
        "ai_vibe", "ai_what_to_expect", "ai_food_and_drinks", "ai_price", "ai_service", "ai_the_move",
        "ai_watch_out", "ai_tags_csv", "ai_confidence"]
N = None
ROWS = [
    (BIG, "Muzeul Național de Artă", "Testville", 44.4392, 26.0961, "culture_sights", "sights", "art_museum",
     "national art museum", "A large museum in the royal palace.", 10000, 4.6, 4.7, 90.0,
     "[[600, 1080], [2040, 2520]]", N, "ChIJaaa", "Calea Victoriei 49", N, "Grand halls", "Allow two hours",
     "European and Romanian art", "Tickets 30 lei", "Major national collection", "Start upstairs", "Closed Mondays",
     "art, history,museum", "high"),
    (1001, "Cișmigiu", "Testville", 44.4375, 26.0911, "nature_outdoors", "sights", "park", "historic urban park",
     "Leafy central park with a lake.", 40000, 4.7, 4.6, 95.0, N, N, "ChIJbbb", "Bulevardul Regina Elisabeta",
     N, N, N, N, N, N, N, N, "park", "medium"),
    (1002, "Origo", "Testville", 44.4335, 26.1006, "food_drink", "food_drink", "cafe", "specialty coffee shop",
     "Small coffee bar.", 4846, 4.6, 4.7, 80.0, "[[450, 1200], [9180, 10200]]", N, "ChIJccc", "Strada Lipscani 9",
     "moderate", "Buzzing", N, "Espresso and filter", "Mid-range", "Fast", N, N, "coffee", "high"),
    (1003, "La Mama", "Testville", 44.4423, 26.0972, "food_drink", "food_drink", "romanian_restaurant",
     "traditional Romanian restaurant", "Classic dishes.", 6383, 4.2, 4.3, 70.0, N, "closed_forever", "ChIJddd",
     "Strada Episcopiei 9", N, N, N, N, N, N, N, N, N, "low"),
    (1004, "Ryan's Pub", "Testville", 44.4438, 26.0949, "food_drink", "food_drink", "pub", "irish pub",
     "Pints and darts.", 1902, 4.4, 4.5, 60.0, N, "CLOSED_TEMPORARILY", "ChIJeee", "Strada Batistei 1", N,
     N, N, N, N, N, N, N, N, "medium"),
    (1005, "Cărturești Carusel", "Testville", 44.4316, 26.1019, "shopping_souvenirs", "shopping", "book_store",
     "landmark bookstore", "A spiral bookstore.", 30000, N, 4.8, 97.0, "[[600, 1320]]", N, "ChIJfff",
     "Strada Lipscani 55", N, "Bright", "Browse", "Books and gifts", N, "Design books", N, N, "books", "high"),
    (1006, "Ateneul Român", "Testville", 44.4413, 26.0973, "culture_sights", "sights", "concert_hall",
     "concert hall", "Neoclassical concert hall.", N, 4.9, N, 99.0, N, N, "not-a-chij", "Strada Benjamin Franklin 1",
     N, N, N, N, N, N, N, N, N, "high"),
    (1007, "Lost place", "Testville", N, 26.10, "culture_sights", "sights", "monument", "monument", "x", 5,
     4.0, 4.0, 1.0, N, N, N, N, N, N, N, N, N, N, N, N, N, N),
    (1008, "Origo Bistro", "Testville", 44.4300, 26.1100, "food_drink", "food_drink", "restaurant", "bistro",
     "Bistro.", 120, 4.1, 4.2, 30.0, N, N, "ChIJggg", "Strada Doamnei 5", N, N, N, N, N, N, N, N, N, "medium"),
    (2001, "Origo", "Othercity", 41.7151, 44.8271, "food_drink", "food_drink", "cafe", "coffee", "x", 50, 4.5,
     4.5, 10.0, N, N, "ChIJhhh", "Rustaveli 1", N, N, N, N, N, N, N, N, N, "high"),
]


@pytest.fixture()
def csv_frame(tmp_path):
    """The synthetic catalog the way the dashboard gets it: through a CSV and pd.read_csv."""
    path = tmp_path / "locations.csv"
    pd.DataFrame(ROWS, columns=COLS).to_csv(path, index=False)
    return pd.read_csv(path)


@pytest.fixture()
def cat(csv_frame):
    return CityCatalog.from_frame(csv_frame, city="Testville")


def manifest_frame():
    root = "/Users/someone/build/recommendation_system/ai_location_recommender/data/visual_photo_profiles/photos_cid"
    return pd.DataFrame([
        (1002, "all", f"{root}/1002/05_all.jpg", 5),
        (1002, "vibe", f"{root}/1002/07_vibe.jpg", 7),
        (1002, "vibe", f"{root}/1002/02_vibe.jpg", 2),
        (1002, "review", f"{root}/1002/01_review.jpg", 1),
        (1002, "all", f"{root}/1002/00_all.jpg", 0),
        (1002, "all", f"{root}/1002/00_all.jpg", 0),        # duplicate entry
        (BIG, "all", f"{root}/{BIG}/03_all.jpg", 3),
        (1005, "vibe", "C:/other/machine/01_vibe.jpg", 1),   # no photos_cid anchor
    ], columns=["place_id", "photo_source", "local_file", "photo_index"])


# --------------------------------------------------------------------------- #
# Cell helpers
# --------------------------------------------------------------------------- #
def test_parse_hours_is_the_pages_parser():
    assert parse_hours("[[600, 1080], [2040, 2520]]") == [[600, 1080], [2040, 2520]]
    assert parse_hours("  [[0, 1440]]") == [[0, 1440]]
    assert parse_hours("[]") == []                          # known, never open
    for v in (None, float("nan"), "", "closed", "[not json", 5):
        assert parse_hours(v) is None                       # unknown -> treated as open


def test_normalize_status_maps_aliases_and_drops_open():
    assert normalize_status("closed_forever") == "closed_forever"
    assert normalize_status(" CLOSED_PERMANENTLY ") == "closed_forever"
    assert normalize_status("closed_temporarily") == "temporarily_closed"
    assert normalize_status("temporarily_closed") == "temporarily_closed"
    for v in (None, float("nan"), "", "open", "OPERATIONAL", "close"):
        assert normalize_status(v) == ""


def test_text_series_concatenates_lowercased_and_tolerates_missing():
    df = pd.DataFrame({"primary_type": ["Cafe", None], "name": ["Origo", "Bar X"]})
    assert list(text_series(df)) == ["cafe origo", " bar x"]
    assert list(text_series(pd.DataFrame({"x": [1, 2]}))) == ["", ""]


# --------------------------------------------------------------------------- #
# from_frame: the page's preparation
# --------------------------------------------------------------------------- #
def test_from_frame_prepares_rows_like_the_page(cat, csv_frame):
    rows = cat.rows
    assert cat.city == "Testville" and cat.timezone == "UTC" and cat.source == "frame"
    # city filter + rows without coordinates dropped, order kept
    assert list(rows["place_id"]) == [str(BIG), "1001", "1002", "1003", "1004", "1005", "1006", "1008"]
    assert str(BIG) in cat.place_ids and cat.has(BIG) and cat.has(str(BIG))   # CID exact, as a string
    assert rows["wp_hours"].iloc[0] == [[600, 1080], [2040, 2520]] and rows["wp_hours"].iloc[1] is None
    assert list(rows["business_status"]) == ["", "", "", "closed_forever", "temporarily_closed", "", "", ""]
    city = csv_frame[(csv_frame["city"] == "Testville") & csv_frame["latitude"].notna()]
    assert cat.center == (float(city["latitude"].mean()), float(city["longitude"].mean()))   # closed included
    assert cat.bbox == [26.0911, 44.43, 26.11, 44.4438]
    assert cat.has_hours


def test_from_frame_city_selection(csv_frame):
    assert CityCatalog.from_frame(csv_frame).city == "Othercity"          # the page's first (sorted) city
    assert CityCatalog.from_frame(csv_frame, city="testville").city == "Testville"
    with pytest.raises(ValueError):
        CityCatalog.from_frame(csv_frame, city="Paris")
    no_city = csv_frame.drop(columns=["city"])
    c = CityCatalog.from_frame(no_city)
    assert c.city is None and len(c) == len(no_city) - 1
    with pytest.raises(ValueError):
        CityCatalog.from_frame(csv_frame.drop(columns=["latitude"]))


def test_lookups(cat):
    assert cat.name_of(1002) == "Origo" and cat.coord_of("1002") == (44.4335, 26.1006)
    assert cat.row("1003")["name"] == "La Mama" and cat.position("1003") == 3
    assert cat.hours_of("1002") == [[450, 1200], [9180, 10200]] and cat.hours_of("1001") is None
    assert cat.hours_of("nope") is None
    assert cat.status_of("1003") == "closed_forever" and cat.status_of("1004") == "temporarily_closed"
    assert cat.status_of("1002") == "" and cat.status_of("nope") == ""
    assert cat.google_place_id("1002") == "ChIJccc" and cat.google_place_id("1006") is None
    assert cat.google_maps_url(BIG) == f"https://www.google.com/maps?cid={BIG}"
    with pytest.raises(KeyError):
        cat.row("nope")


def test_frame_version_is_stable_and_content_sensitive(csv_frame):
    a = CityCatalog.from_frame(csv_frame, city="Testville").version
    assert a.startswith("frame:") and a == CityCatalog.from_frame(csv_frame, city="Testville").version
    changed = csv_frame.copy()
    changed.loc[2, "opening_hours"] = "[[0, 1440]]"
    assert CityCatalog.from_frame(changed, city="Testville").version != a
    assert CityCatalog.from_frame(csv_frame, city="Testville", version="v1").version == "v1"


# --------------------------------------------------------------------------- #
# Photos
# --------------------------------------------------------------------------- #
def test_photo_keys_follow_the_dashboard_order():
    keys = photo_keys_by_place(manifest_frame())
    assert keys["1002"] == ["photos_cid/1002/02_vibe.jpg", "photos_cid/1002/07_vibe.jpg",
                            "photos_cid/1002/01_review.jpg", "photos_cid/1002/00_all.jpg",
                            "photos_cid/1002/05_all.jpg"]                    # vibe, review, all; then index
    assert keys[str(BIG)] == [f"photos_cid/{BIG}/03_all.jpg"]
    assert keys["1005"] == ["photos_cid/1005/01_vibe.jpg"]                   # re-anchored by place id
    assert photo_keys_by_place(manifest_frame(), max_photos=2)["1002"] == keys["1002"][:2]
    assert photo_keys_by_place(None) == {} and photo_keys_by_place(pd.DataFrame()) == {}


def test_frame_catalog_with_photo_manifest(csv_frame):
    c = CityCatalog.from_frame(csv_frame, city="Testville", photo_manifest=manifest_frame())
    assert c.photos("1002", 2) == ["photos_cid/1002/02_vibe.jpg", "photos_cid/1002/07_vibe.jpg"]
    assert c.photos("1001") == []


# --------------------------------------------------------------------------- #
# Bundle table round trip
# --------------------------------------------------------------------------- #
def test_to_bundle_frame_schema(csv_frame):
    b = to_bundle_frame(csv_frame, manifest_frame())
    assert list(b.columns) == [c for c in BUNDLE_COLUMNS if c in b.columns]
    assert set(BUNDLE_COLUMNS) - set(b.columns) == set()
    assert len(b) == len(csv_frame)                                      # all rows, source order
    assert list(b["place_id"]) == [str(x) for x in csv_frame["place_id"]]
    for col in ("latitude", "longitude", "google_rating", "google_user_rating_count", "bayesian_rating",
                "map_visibility_score"):
        assert b[col].dtype == np.float64
    assert b["opening_hours"].iloc[0] == "[[600, 1080], [2040, 2520]]" and b["opening_hours"].iloc[1] is None
    assert list(b["business_status"][:5]) == ["", "", "", "closed_forever", "temporarily_closed"]
    assert b["price_level"].iloc[0] is None and b["address"].iloc[0] == "Calea Victoriei 49"
    assert b["photos"].iloc[2][:2] == ["photos_cid/1002/02_vibe.jpg", "photos_cid/1002/07_vibe.jpg"]
    assert list(b["photo_count"][:3]) == [1, 0, 5] and b["photo_count"].dtype == np.int32


def _write_bundle(frame, directory: Path, manifest=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(directory / "walk_catalog.parquet", index=False)
    if manifest is not None:
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def test_from_bundle_equals_from_frame(csv_frame, tmp_path):
    frame_cat = CityCatalog.from_frame(csv_frame, city="Testville", photo_manifest=manifest_frame())
    d = _write_bundle(to_bundle_frame(csv_frame[csv_frame["city"] == "Testville"], manifest_frame()),
                      tmp_path / "b1", {"bundle_id": "testville-20261002-abcdef12", "city": "Testville",
                                        "timezone": "Europe/Bucharest"})
    bundle_cat = CityCatalog.from_bundle(d)
    assert catalog_frames_equal(frame_cat, bundle_cat) == []
    assert bundle_cat.cold_interest() == frame_cat.cold_interest()
    assert np.array_equal(bundle_cat.cold_interest_array(), frame_cat.cold_interest_array())
    assert bundle_cat.center == frame_cat.center and bundle_cat.bbox == frame_cat.bbox
    assert list(bundle_cat.text_series()) == list(frame_cat.text_series())
    assert bundle_cat.version == "testville-20261002-abcdef12" and bundle_cat.timezone == "Europe/Bucharest"
    assert bundle_cat.source == "bundle" and bundle_cat.manifest["city"] == "Testville"
    for pid in frame_cat.place_ids:
        assert bundle_cat.place_card(pid) == frame_cat.place_card(pid)
        assert bundle_cat.hours_of(pid) == frame_cat.hours_of(pid)
        assert bundle_cat.status_of(pid) == frame_cat.status_of(pid)
    # the parquet file itself also works, and without a manifest the version is the file hash
    plain = CityCatalog.from_bundle(_write_bundle(to_bundle_frame(csv_frame), tmp_path / "b2") / "walk_catalog.parquet")
    assert plain.version.startswith("bundle:") and plain.city == "Othercity"


@pytest.mark.skipif(REAL_CSV is None or not REAL_CSV.exists(), reason="real Bucharest catalog not available")
def test_real_catalog_bundle_roundtrip(tmp_path):
    df = pd.read_csv(REAL_CSV)
    frame_cat = CityCatalog.from_frame(df, city="Bucharest")
    bundle_cat = CityCatalog.from_bundle(_write_bundle(to_bundle_frame(df), tmp_path / "real"))
    assert len(frame_cat) == len(bundle_cat) == 12961
    assert catalog_frames_equal(frame_cat, bundle_cat) == []
    assert bundle_cat.cold_interest() == frame_cat.cold_interest()
    assert bundle_cat.center == frame_cat.center
    # the baseline's centre (numpy 2.4); other numpy builds differ in the last bits of the mean
    assert frame_cat.center == pytest.approx((44.4402865461307, 26.097708464099995), abs=1e-9)
    for col in ("latitude", "longitude", "google_user_rating_count", "bayesian_rating"):
        assert bundle_cat.rows[col].dtype == frame_cat.rows[col].dtype == np.float64


# --------------------------------------------------------------------------- #
# Cold-start interest
# --------------------------------------------------------------------------- #
def test_cold_interest_formula(cat):
    cold = cat.cold_interest()
    assert set(cold) == set(cat.place_ids)
    top = math.log1p(40000)                                  # the city maximum (Cișmigiu)
    assert cold["1001"] == pytest.approx(0.7 * 1.0 + 0.3 * 0.7, abs=1e-12)
    assert cold[str(BIG)] == pytest.approx(0.7 * math.log1p(10000) / top + 0.3 * 0.6, abs=1e-12)
    assert cold["1005"] == pytest.approx(0.7 * math.log1p(30000) / top + 0.3 * 0.3, abs=1e-12)   # rating NaN
    assert cold["1006"] == pytest.approx(0.7 * 0.0 + 0.3 * 0.9, abs=1e-12)                       # reviews NaN
    assert cold["1003"] == pytest.approx(0.7 * math.log1p(6383) / top + 0.3 * 0.2, abs=1e-12)    # closed counted
    assert list(cat.cold_interest_array()) == [cold[p] for p in cat.place_ids]


def test_cold_interest_fallbacks():
    df = pd.DataFrame({"place_id": ["a", "b", "c"], "map_visibility_score": [10.0, 30.0, None],
                       "google_rating": [4.0, 5.0, 3.0]})
    assert list(cold_start_scores(df)) == [0.0, 1.0, 0.0]
    assert cold_start_scores(pd.DataFrame({"place_id": ["a"]})) is None
    c = CityCatalog(pd.DataFrame({"place_id": ["a"], "latitude": [1.0], "longitude": [2.0]}))
    assert c.cold_interest() == {} and list(c.cold_interest_array()) == [0.0]


def test_cold_interest_matches_the_interest_module(cat):
    interest = pytest.importorskip("walk_planner.interest")
    if not hasattr(interest, "cold_start_map"):
        pytest.skip("interest.cold_start_map not available")
    assert interest.cold_start_map(cat.rows) == cat.cold_interest()


def _dashboard_cold_start(city_rows):
    """dashboard_app._walk_interest_map's cold start, verbatim (the pre-refactor reference)."""
    if "google_user_rating_count" in city_rows.columns:
        n = pd.to_numeric(city_rows["google_user_rating_count"], errors="coerce").fillna(0.0).clip(lower=0)
        fame = np.log1p(n) / max(float(np.log1p(n.max())), 1e-9)
        rating_col = "bayesian_rating" if "bayesian_rating" in city_rows.columns else "google_rating"
        rating = pd.to_numeric(city_rows[rating_col], errors="coerce") if rating_col in city_rows.columns \
            else pd.Series(np.nan, index=city_rows.index)
        quality = (rating - 4.0).clip(0.0, 1.0).fillna(0.3)
        score = 0.7 * fame + 0.3 * quality
        return {str(pid): float(x) for pid, x in zip(city_rows["place_id"], score)}
    for col in ("map_visibility_score", "google_rating"):
        if col in city_rows.columns:
            v = pd.to_numeric(city_rows[col], errors="coerce")
            lo, span = float(v.min()), (float(v.max()) - float(v.min())) or 1.0
            return {str(pid): ((float(x) - lo) / span if pd.notna(x) else 0.0)
                    for pid, x in zip(city_rows["place_id"], v)}
    return {}


def test_cold_start_has_one_implementation_and_is_the_dashboards(cat, monkeypatch):
    from walk_planner import catalog as catalog_mod
    from walk_planner import interest
    assert catalog_mod.cold_start is interest.cold_start and catalog_mod.cold_start_map is interest.cold_start_map
    frames = [cat.rows,
              pd.DataFrame({"place_id": ["a", "b", "c"], "map_visibility_score": [10.0, 30.0, None],
                            "google_rating": [4.0, 5.0, 3.0]}),
              pd.DataFrame({"place_id": ["a", "b"], "google_rating": [4.1, None]}),
              pd.DataFrame({"place_id": ["a", "b"], "google_user_rating_count": [None, 5], "google_rating": [4.5, 3.9]}),
              pd.DataFrame({"place_id": ["a"]})]
    for rows in frames:
        ref = _dashboard_cold_start(rows)
        c = CityCatalog(rows.assign(latitude=44.4, longitude=26.1))
        assert c.cold_interest() == ref                                  # byte-identical values, same keys
        scores = cold_start_scores(rows)
        assert (scores is None) == (ref == {})
        if scores is not None:
            assert list(scores.index) == list(rows.index) and [float(x) for x in scores] == list(ref.values())
            assert list(c.cold_interest_array()) == list(ref.values())
    calls = []

    def spy(rows):
        calls.append(len(rows))
        return interest.cold_start(rows)

    monkeypatch.setattr(catalog_mod, "cold_start", spy)              # the catalog computes nothing itself
    CityCatalog(cat.rows).cold_interest_array()
    cold_start_scores(cat.rows)
    assert calls == [len(cat.rows)] * 2


# --------------------------------------------------------------------------- #
# Cards, details
# --------------------------------------------------------------------------- #
def test_place_card(csv_frame):
    c = CityCatalog.from_frame(csv_frame, city="Testville", photo_manifest=manifest_frame())
    card = c.place_card("1002", photo_base_url="https://cdn.example/photos/")
    assert card == {
        "place_id": "1002", "name": "Origo", "type_label": "specialty coffee shop", "primary_type": "cafe",
        "theme": "food_drink", "theme_group": "food_drink", "rating": 4.7, "rating_count": 4846,
        "summary": "Small coffee bar.", "summary_lang": "en",
        "photos": [{"key": k, "url": "https://cdn.example/photos/" + k} for k in c.photos("1002", 4)],
        "google_maps_url": "https://www.google.com/maps?cid=1002", "google_place_id": "ChIJccc",
        "address": "Strada Lipscani 9", "business_status": "operational", "lat": 44.4335, "lon": 26.1006,
        "price_level": "moderate"}
    assert len(card["photos"]) == 4
    assert c.place_card("1002")["photos"][0]["url"] is None                  # no PHOTO_BASE_URL
    ateneu = c.place_card("1006")
    assert ateneu["rating"] is None and ateneu["rating_count"] is None and ateneu["google_place_id"] == "not-a-chij"
    assert c.place_card("1004")["business_status"] == "temporarily_closed"
    assert c.place_card("1003")["business_status"] == "closed_forever"


def test_place_detail_sections_by_theme_group_and_week_hours(cat):
    museum = cat.place_detail(BIG)
    keys = [s["key"] for s in museum["sections"]]
    assert keys == ["vibe", "what_to_expect", "the_sight", "price", "significance", "the_move", "watch_out"]
    assert museum["sections"][2]["column"] == "ai_food_and_drinks" and museum["sections"][2]["title_en"] == "The sight"
    assert museum["tags"] == ["art", "history", "museum"] and museum["ai_confidence"] == "high"
    week = museum["opening_hours_week"]
    assert [d["weekday"] for d in week] == list(range(7))
    assert week[0]["intervals"] == [{"open": "10:00", "close": "18:00", "close_day_offset": 0}]
    assert week[2]["closed_all_day"] is True and museum["opening_hours_known"] is True
    shop = cat.place_detail("1005")
    assert [s["key"] for s in shop["sections"]] == ["vibe", "what_to_expect", "the_goods", "what_to_buy"]
    cafe = cat.place_detail("1002")
    assert [s["key"] for s in cafe["sections"]] == ["vibe", "food_and_drinks", "price", "service"]
    # Sunday 09:00 -> Monday 02:00: Monday shows last night's opening running until 02:00
    assert cafe["opening_hours_week"][0]["carryover_until"] == "02:00"
    assert cafe["opening_hours_week"][6]["intervals"] == [{"open": "09:00", "close": "02:00", "close_day_offset": 1}]
    park = cat.place_detail("1001")
    assert park["opening_hours_week"] is None and park["opening_hours_known"] is False and park["sections"] == []


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def test_search_tiers_diacritics_and_status(cat):
    assert [r["place_id"] for r in cat.search("origo")] == ["1002", "1008"]         # exact, then prefix
    assert cat.search("origo")[0]["match"] == "exact" and cat.search("origo")[1]["match"] == "prefix"
    assert [r["place_id"] for r in cat.search("CISMIGIU")] == ["1001"]               # diacritics / case
    assert [r["place_id"] for r in cat.search("ateneul roman")] == ["1006"]
    assert cat.search("national")[0]["place_id"] == str(BIG)                          # substring ("Național")
    assert cat.search("muzeul arta")[0]["match"] == "all_words"
    assert [r["place_id"] for r in cat.search("lipscani")] == ["1005", "1002"]       # address fallback, by reviews
    assert cat.search("la mama") == []                                               # closed_forever hidden
    assert cat.search("la mama", include_closed_forever=True)[0]["business_status"] == "closed_forever"
    assert cat.search("ryan")[0]["business_status"] == "temporarily_closed"         # included, flagged
    assert cat.search("   ") == [] and cat.search("zzz") == []


def test_search_near_ranks_by_distance_within_a_tier_and_limits(cat):
    from walk_planner.catalog import SEARCH_DISTANCE_WEIGHT
    from walk_planner.core import haversine_km

    near = (44.4300, 26.1100)
    hits = cat.search("strada", near=near)                       # all address matches (one tier)
    assert {r["match"] for r in hits} == {"address"} and all("distance_m" in r for r in hits)
    assert hits[[r["place_id"] for r in hits].index("1008")]["distance_m"] == 0

    def score(r):
        n = cat.row(r["place_id"])["google_user_rating_count"]
        n = 0.0 if n != n else float(n)
        return math.log1p(n) - SEARCH_DISTANCE_WEIGHT * haversine_km(near[0], near[1], r["lat"], r["lon"])
    assert [score(r) for r in hits] == sorted((score(r) for r in hits), reverse=True)
    assert "distance_m" not in cat.search("strada")[0]
    assert len(cat.search("strada", limit=2)) == 2
    hit = cat.search("origo", photo_base_url="https://cdn")[0]
    assert set(hit) >= {"place_id", "name", "type_label", "rating", "rating_count", "address", "business_status",
                        "theme_group", "lat", "lon", "photo", "match", "google_maps_url"}


def test_search_validates_near_and_limit(cat):
    # non-finite / out-of-range numbers are a validation_error (422), never a crash or a bogus distance
    from walk_planner.messages import PlannerInputError
    for near in ((float("nan"), 26.1), (44.4, float("inf")), (95.0, 26.1), (44.4, -181.0), ("a", "b"), (1.0,),
                 (1.0, 2.0, 3.0), 5):
        with pytest.raises(PlannerInputError) as e:
            cat.search("origo", near=near)
        assert (e.value.code, e.value.http_status, e.value.params["field"]) == ("validation_error", 422, "near")
    for limit in (float("inf"), float("nan"), 2.5, "5", True, None):
        with pytest.raises(PlannerInputError) as e:
            cat.search("origo", limit=limit)
        assert (e.value.code, e.value.params["field"]) == ("validation_error", "limit")
    assert len(cat.search("origo", limit=1.0)) == 1 and cat.search("origo", limit=-3) == []
    assert cat.search("origo", near=[44.4335, 26.1006])[0]["distance_m"] == 0


def test_warm_precomputes_the_lazy_caches(csv_frame):
    c = CityCatalog.from_frame(csv_frame, city="Testville")
    assert c._search is None
    assert c.warm() is c
    assert c._version is not None and c._cold is not None and c._cold_arr is not None and c._text is not None
    names, addrs, statuses, counts = c._search
    assert len(names) == len(addrs) == len(statuses) == len(counts) == len(c)


def test_concurrent_first_searches_never_see_a_half_built_index(csv_frame, monkeypatch):
    # v0 published the folded names before the address / status / count lists, so a search arriving while
    # the first one was still building read None for those (TypeError; 5 of 8 threads in the review). The
    # index is now built under a lock and published as one tuple. _fold is slowed down to widen the build
    # window, and the 8 threads arrive staggered across it.
    import time
    from concurrent.futures import ThreadPoolExecutor

    from walk_planner import catalog as catalog_mod

    big = pd.concat([csv_frame] * 30, ignore_index=True)
    big["place_id"] = [str(1000 + i) for i in range(len(big))]
    expected = CityCatalog.from_frame(big, city="Testville").search("origo", limit=50)
    real_fold = catalog_mod._fold

    def slow_fold(text):
        time.sleep(0.0002)
        return real_fold(text)

    monkeypatch.setattr(catalog_mod, "_fold", slow_fold)
    c = CityCatalog.from_frame(big, city="Testville")

    def search_after(delay_s):
        time.sleep(delay_s)
        return c.search("origo", limit=50)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(search_after, [0.02 * i for i in range(8)]))
    assert len(expected) > 20 and all(r == expected for r in results)


def test_catalog_pickles_and_copies(cat):
    import copy
    import pickle
    cat.search("origo")                                            # with a built index and its lock
    for clone in (pickle.loads(pickle.dumps(cat)), copy.deepcopy(cat)):
        assert clone.place_ids == cat.place_ids and clone.cold_interest() == cat.cold_interest()
        assert clone.search("origo") == cat.search("origo")
        assert clone._search_lock is not cat._search_lock


def test_catalog_needs_rows():
    with pytest.raises(ValueError):
        CityCatalog(pd.DataFrame({"place_id": [], "latitude": [], "longitude": []}))


def test_used_columns_cover_everything_the_pipeline_reads():
    for col in ("place_id", "latitude", "longitude", "theme", "theme_group", "primary_type", "ai_place_type_summary",
                "ai_card_summary", "google_user_rating_count", "bayesian_rating", "opening_hours",
                "business_status", "google_place_id", "name"):
        assert col in USED_COLUMNS
