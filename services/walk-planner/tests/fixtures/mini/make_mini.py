"""Generate the synthetic "Minitown" source data of the mini golden fixture (deterministic).

Writes ``source/`` (catalog CSV, photo manifest, text / image embedding stores + metadata) and
``scenarios.json`` next to this file. Everything is derived from a tiny pure-arithmetic PRNG (an LCG,
no libm, no numpy random streams), so the files are byte-identical on every platform and numpy
version. The committed bundle and expected outputs are built FROM these files (see README.md):

    python tests/fixtures/mini/make_mini.py
    python -m walk_planner bundle build --out-root tests/fixtures/mini/bundles --city-slug minitown \\
        --timezone Europe/Bucharest --catalog-csv tests/fixtures/mini/source/locations_minitown.csv ...
    python -m walk_planner golden update --bundle tests/fixtures/mini/bundles \\
        --scenarios tests/fixtures/mini/scenarios.json --expected-root tests/fixtures/mini/expected

60 places around (44.43, 26.10): sights, churches, parks, markets, cafés, restaurants, bars,
entertainment and shops, with opening hours (some unknown, some 24 h, bars past midnight), one
closed_forever restaurant, one temporarily_closed pub, one "closed_permanently" shop (alias), one café
without coordinates, one place without a text embedding, 80% with an image embedding, 1-4 photos for
most places. place_ids are 19-20 digit CIDs, half of them above the int64 maximum.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE / "source"
CITY = "Minitown"
CENTER = (44.4300, 26.1000)
TEXT_DIM = 8
IMAGE_DIM = 4
AXES = ("axis_quiet_lively", "axis_work_social", "axis_day_night", "axis_casual_premium", "axis_drinks_food",
        "axis_local_tourist", "axis_cheap_expensive", "axis_traditional_experimental")


class Lcg:
    """Numerical Recipes LCG: platform-independent pseudo-random numbers (integers only)."""

    def __init__(self, seed: int):
        self.x = seed % 2 ** 32

    def u(self) -> float:
        self.x = (1664525 * self.x + 1013904223) % 2 ** 32
        return self.x / 2 ** 32

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.u()

    def normal(self) -> float:                      # Irwin-Hall: sum of 12 uniforms - 6
        return sum(self.u() for _ in range(12)) - 6.0

    def choice(self, items):
        return items[int(self.u() * len(items)) % len(items)]


def week(open_min: int, close_min: int, days=range(7)) -> list:
    """Week-minute intervals [[d*1440 + open, d*1440 + close], ...] (close may pass midnight)."""
    return [[d * 1440 + open_min, d * 1440 + close_min] for d in days]


H24 = week(0, 1440)
# kind -> (theme, theme_group, [(primary_type, ai type summary)], names, hours, text prototype dims, tags)
KINDS = {
    "museum": ("culture_sights", "sights", [("museum", "History museum"), ("art_gallery", "Art gallery"),
                                            ("historical_landmark", "Historic landmark"), ("museum", "Art museum")],
               ["City History Museum", "Galeria Nord", "Old Clock Tower", "Museum of Maps", "Palace of Arts",
                "Gallery Lumen", "Merchant House", "Museum of Glass", "Fortress Gate", "Collection Verde"],
               week(600, 1080, days=range(1, 7)), (0, 1), ["historic", "art", "quiet", "tourist_favorite"]),
    "church": ("religious_sights", "sights", [("church", "Orthodox church")],
               ["St. Elias Church", "Holy Trinity Church", "Monastery of the Lake"],
               week(480, 1140), (0, 2), ["historic", "quiet", "spiritual"]),
    "park": ("nature_outdoors", "sights", [("park", "City park"), ("garden", "Botanical garden"),
                                           ("lake", "Urban lake")],
             ["Central Park", "Rose Garden", "Lake Promenade", "Linden Park", "Botanical Garden", "Willow Park",
              "Reed Lake"], H24, (3,), ["green", "outdoor", "quiet", "family_friendly"]),
    "market": ("markets_walks", "sights", [("market", "Food market"), ("town_square", "Town square"),
                                           ("plaza", "Pedestrian plaza")],
               ["Old Market", "Union Square", "Fountain Plaza"], week(480, 1200), (3, 4),
               ["lively", "local_favorite", "outdoor"]),
    "coffee": ("food_drink", "food_drink", [("cafe", "Specialty coffee shop"), ("coffee_shop", "Coffee roaster")],
               ["Bean Roastery Cafe", "Espresso Corner", "Morning Cafe", "Cafe Alba", "Roaster Lab",
                "Cafe Tei", "Little Espresso", "Cafe Sky", "Cafe Nomad", "Cafe Lost"],
               week(480, 1200), (5,), ["specialty_coffee", "cozy", "laptop_friendly"]),
    "food": ("food_drink", "food_drink", [("restaurant", "Romanian restaurant"), ("italian_restaurant", "Trattoria"),
                                          ("bistro", "Bistro"), ("romanian_restaurant", "Traditional restaurant")],
             ["Casa Veche Restaurant", "Trattoria Sole", "Bistro Verde", "Grill House", "Kitchen 12",
              "Dining Hall Restaurant", "Pizzeria Roma", "Eatery Mosaic", "Bistro Luna", "Restaurant Dacia",
              "Old Tavern Restaurant", "Bistro Nord"], week(720, 1380), (6,), ["food_focused", "group_friendly"]),
    "bar": ("food_drink", "food_drink", [("bar", "Cocktail bar"), ("pub", "Irish pub"), ("wine_bar", "Wine bar")],
            ["Pub 21", "Wine Cellar Bar", "Cocktail Club", "Brewery Yard", "Lounge Bar Nine", "Paused Pub",
             "Night Owl Bar"], week(1080, 1560), (6, 7), ["cocktails", "good_for_night_out", "lively"]),
    "fun": ("performing_arts", "things_to_do", [("performing_arts_theater", "Theatre"),
                                                ("movie_theater", "Cinema")],
            ["National Theatre", "Cinema Lumiere", "Opera Stage", "Comedy Show Hall"], week(1020, 1380), (7, 4),
            ["show", "evening", "culture"]),
    "shop": ("shopping_souvenirs", "shopping", [("book_store", "Bookstore"), ("gift_shop", "Souvenir shop"),
                                               ("clothing_store", "Boutique")],
             ["Book Nook", "Souvenir House", "Boutique Mara", "Craft Corner"], week(600, 1260), (4, 1),
             ["local_crafts", "gifts"]),
}
COUNTS = {"museum": 10, "church": 3, "park": 7, "market": 3, "coffee": 10, "food": 12, "bar": 7, "fun": 4, "shop": 4}


def place_id(i: int) -> str:
    return str(18000000000000000000 + 7919 * i) if i % 2 == 0 else str(9100000000000000000 + 104729 * i)


def build_places(rng: Lcg) -> list[dict]:
    rows = []
    i = 0
    for kind, n in COUNTS.items():
        theme, group, types, names, hours, _proto, tags = KINDS[kind]
        for j in range(n):
            ptype, summary = types[j % len(types)]
            reviews = int(round(10 ** rng.uniform(1.2, 4.3)))
            rating = round(rng.uniform(3.9, 4.9), 1)
            bayes = round((reviews * rating + 25 * 4.4) / (reviews + 25), 4)
            h = hours
            if kind == "museum" and j in (2, 8):
                h = None                                   # landmarks: unknown hours
            if kind == "market" and j == 0:
                h = None
            if kind == "coffee" and j == 3:
                h = week(780, 1140)                        # opens at 13:00
            if kind == "park" and j == 6:
                h = None
            status = ""
            if kind == "food" and j == 10:
                status = "closed_forever"
            if kind == "bar" and j == 5:
                status = "temporarily_closed"
            if kind == "shop" and j == 3:
                status = "CLOSED_PERMANENTLY"                # Google's enum name: normalised to closed_forever
            lat = round(CENTER[0] + rng.uniform(-0.012, 0.012), 6)
            lon = round(CENTER[1] + rng.uniform(-0.016, 0.016), 6)
            if kind == "coffee" and j == 9:
                lat = lon = None                           # no coordinates: the planner ignores it
            pid = place_id(i)
            my_tags = [tags[k] for k in range(len(tags)) if (j + k) % 3 != 2] or tags[:1]
            axes = {a: int(round(rng.uniform(5, 95))) for a in AXES}
            if kind == "park" and j == 1:
                axes = {a: None for a in AXES}             # no vibe axes
            rows.append({
                "place_id": pid,
                "name": names[j],
                "primary_type": ptype,
                "google_rating": rating,
                "google_user_rating_count": reviews,
                "price_level": (rng.choice(["inexpensive", "moderate", "expensive", ""])
                                if group == "food_drink" else ""),
                "ai_card_summary": f"{summary} in Minitown, place {i}.",
                "ai_place_type_summary": summary,
                "ai_vibe": f"{summary} with a {rng.choice(['calm', 'lively', 'cosy', 'grand'])} feel.",
                "ai_what_to_expect": "Synthetic test place.",
                "ai_tags_csv": ",".join(my_tags),
                "ai_tags_json": json.dumps([{"tag": t, "confidence": "high", "polarity": "positive"} for t in my_tags]),
                **axes,
                "ai_confidence": "high" if i % 7 else "medium",
                "latitude": lat,
                "longitude": lon,
                "city": CITY,
                "bayesian_rating": bayes,
                "map_visibility_score": round(rng.uniform(5, 95), 2),
                "theme": "leisure_active" if kind == "fun" and j == 3 else theme,
                "theme_group": group,
                "address": f"Strada Mini {i + 1}, Minitown",
                "google_place_id": f"ChIJmini{i:04d}xyz",
                "opening_hours": json.dumps(h) if h is not None else "",
                "business_status": status,
                "_kind": kind,
            })
            i += 1
    return rows


def text_vectors(rows: list[dict], rng: Lcg) -> np.ndarray:
    out = np.zeros((len(rows), TEXT_DIM), dtype=np.float32)
    for i, r in enumerate(rows):
        proto = KINDS[r["_kind"]][5]
        v = [0.25 * rng.normal() for _ in range(TEXT_DIM)]
        for d in proto:
            v[d] += 1.0
        out[i] = [round(x, 4) for x in v]
    return out


def image_vectors(rows: list[dict], rng: Lcg) -> np.ndarray:
    out = np.zeros((len(rows), IMAGE_DIM), dtype=np.float32)
    for i, r in enumerate(rows):
        base = {"museum": 0, "church": 0, "park": 1, "market": 1, "coffee": 2, "food": 2, "bar": 3, "fun": 3,
                "shop": 1}[r["_kind"]]
        v = [0.3 * rng.normal() for _ in range(IMAGE_DIM)]
        v[base] += 1.0
        out[i] = [round(x, 4) for x in v]
    return out


def main() -> None:
    rng = Lcg(20261003)
    rows = build_places(rng)
    SRC.mkdir(parents=True, exist_ok=True)
    cols = [c for c in rows[0] if not c.startswith("_")]
    with open(SRC / "locations_minitown.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in cols})

    # photos: 1-4 per place (none for every 6th), vibe or all, two-digit indices with gaps
    with open(SRC / "photo_manifest_minitown.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["place_id", "photo_source", "local_file", "photo_index", "photo_index_in_place"])
        for i, r in enumerate(rows):
            if i % 6 == 5:
                continue
            src = "vibe" if i % 3 == 0 else "all"
            for k in range(1 + i % 4):
                idx = 2 * k + (i % 2)
                w.writerow([r["place_id"], src, f"photos_cid/{r['place_id']}/{idx:02d}_{src}.jpg", idx, k])

    # text store: rows in REVERSE catalog order (joined through the metadata); place 5 has no embedding
    text = text_vectors(rows, rng)
    order = list(range(len(rows)))[::-1]
    store = np.zeros((len(rows), TEXT_DIM), dtype=np.float32)
    with open(SRC / "text_minitown_metadata.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["source_row_index", "place_id", "custom_id", "embedding_row", "has_embedding"])
        for row_n, i in enumerate(order):
            has = i != 5
            if has:
                store[row_n] = text[i]
            w.writerow([row_n, rows[i]["place_id"], f"location_{row_n}_{rows[i]['place_id']}", row_n, has])
    np.save(SRC / "text_minitown.npy", store, allow_pickle=False)

    # image store: places with a photo vector (4 of 5) + one place of another city
    img = image_vectors(rows, rng)
    keep = [i for i in range(len(rows)) if i % 5 != 4]
    istore = np.zeros((len(keep) + 1, IMAGE_DIM), dtype=np.float32)
    with open(SRC / "image_minitown_metadata.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["place_id", "direct_place_embedding_row", "has_direct_image_embedding", "run_id", "model_tag"])
        for row_n, i in enumerate(keep):
            istore[row_n] = img[i]
            w.writerow([rows[i]["place_id"], row_n, True, "openclip_vitb32_v1", "openclip_vitb32_laion2b"])
        istore[len(keep)] = [0.5, 0.5, 0.5, 0.5]
        w.writerow(["1234567890123456789", len(keep), True, "openclip_vitb32_v1", "openclip_vitb32_laion2b"])
    np.save(SRC / "image_minitown.npy", istore, allow_pickle=False)

    by_name = {r["name"]: r["place_id"] for r in rows}
    write_scenarios(by_name)
    print(f"wrote {len(rows)} places to {SRC}")


def write_scenarios(ids: dict) -> None:
    base = {"city": CITY, "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00", "end_day_offset": 0,
            "shape": "loop", "start": "city_center", "style": "max",
            "slots": [{"activity": a, "dwell_min": None} for a in ("sight", "coffee", "park", "food")],
            "must_visit_place_ids": [], "variants": 3, "radius_km": 2.5, "fill_window": True,
            "known_hours_only": False, "top_k": 8}
    chains = [{"chain": "A", "ops": [{"op": "move", "from": -1, "to": 0}]},
              {"chain": "B", "ops": [{"op": "remove", "index": 1}]},
              {"chain": "C", "ops": [{"op": "add", "place_id": ids["Museum of Glass"]}]},
              {"chain": "D", "ops": [{"op": "set_dwell", "index": 0, "dwell_min": 120}]},
              {"chain": "E", "ops": [{"op": "remove", "index": 0}, {"op": "restore", "removed_index": 0, "to": 0}]}]
    sc = [
        {"id": "M01_default", **base, "edits": chains},
        {"id": "M02_one_way_scenic", **base, "shape": "one_way", "style": "scenic",
         "start": {"lat": 44.425, "lon": 26.09},
         "slots": [{"activity": "coffee", "dwell_min": 20}, {"activity": "sight", "dwell_min": None},
                   {"activity": "market", "dwell_min": None}], "edits": []},
        {"id": "M03_free_chill", **base, "shape": "free", "start": None, "style": "chill",
         "slots": [{"activity": a, "dwell_min": None} for a in ("sight", "coffee", "food")],
         "edits": [{"chain": "A", "ops": [{"op": "move", "from": 1, "to": 0}]}]},
        {"id": "M04_must_status", **base, "end_time": "16:00",
         "slots": [{"activity": "coffee", "dwell_min": None}],
         "must_visit_place_ids": [ids["Old Tavern Restaurant"], ids["Paused Pub"], ids["City History Museum"],
                                  "1111111111111111111"],
         "edits": [{"chain": "A", "ops": [{"op": "add", "place_id": ids["Craft Corner"]}]},     # alias -> 422
                   {"chain": "B", "ops": [{"op": "add", "place_id": ids["Paused Pub"]}]},       # in route -> 409
                   {"chain": "C", "ops": [{"op": "set_dwell", "index": 1, "dwell_min": 45},
                                          {"op": "remove", "index": 0}]},
                   {"chain": "D", "ops": [{"op": "add", "place_id": ids["Souvenir House"]},
                                          {"op": "add", "place_id": "3333333333333333333"}]}]},  # 200, then 404
        {"id": "M05_favourites", **base, "favourite_place_ids": [ids["Galeria Nord"], ids["Palace of Arts"]],
         "want_to_go_place_ids": [ids["Roaster Lab"], "2222222222222222222"], "personalization_strength": 0.7,
         "edits": [{"chain": "A", "ops": [{"op": "move", "from": -1, "to": 0}]},
                   {"chain": "C", "ops": [{"op": "add", "place_id": ids["Museum of Glass"]}]}]},
        {"id": "M06_night_bars", **base, "shape": "one_way", "start": {"place_id": ids["Pub 21"]},
         "start_time": "19:00", "end_time": "01:00", "end_day_offset": 1,
         "slots": [{"activity": "bar", "dwell_min": None}, {"activity": "food", "dwell_min": 60}],
         "variants": 2, "edits": []},
        {"id": "M07_all_slots_long", **base, "start_time": "09:00", "end_time": "18:00", "radius_km": 5.0,
         "slots": [{"activity": a, "dwell_min": None} for a in
                   ("sight", "coffee", "food", "bar", "park", "market", "entertainment", "shopping")],
         "variants": 1,
         "edits": [{"chain": "A", "ops": [{"op": "remove", "index": 2}, {"op": "move", "from": 0, "to": 3}]}]},
        {"id": "M08_invalid_duplicate_slot", **base,
         "slots": [{"activity": "sight", "dwell_min": None}, {"activity": "sight", "dwell_min": None}], "edits": []},
    ]
    (HERE / "scenarios.json").write_text(
        "[\n" + ",\n".join(" " + json.dumps(s, ensure_ascii=False) for s in sc) + "\n]\n", encoding="utf-8")


if __name__ == "__main__":
    main()
