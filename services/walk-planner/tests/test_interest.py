"""Unit tests of the interest model (cold start + taste port + rank blend) on a synthetic city.

The synthetic city has three catalog themes and four "taste" clusters that cut across them: each
cluster has its own direction in the text and photo embedding spaces, its own tags and its own
vibe axes, so favourites from one cluster must lift that cluster's places in every theme.
"""
import json
import pickle
import sys

import numpy as np
import pandas as pd
import pytest

from walk_planner import interest as wi
from walk_planner.interest import (
    MODE_FAVOURITES,
    MODE_POPULARITY,
    TasteArtifacts,
    cluster_profiles,
    cold_start,
    cold_start_map,
    interest_map,
    resolve_seeds,
    taste_scores,
)
from walk_planner.interest_build import (
    build_taste_artifacts,
    csls_density,
    parse_tag_names,
    quality_score_v2,
    taste_artifacts_from_files,
)

THEMES = ("food_drink", "culture_sights", "nature_outdoors")
CLUSTER_TAGS = (("cozy", "specialty_coffee", "quiet"), ("lively", "cocktails", "night_out"),
                ("historic", "architecture", "museum"), ("green", "family_friendly", "outdoor"))
AXES = wi.AXIS_COLUMNS


def _tags_json(tags, extra=()):
    items = [{"tag": t, "confidence": "high", "polarity": "positive"} for t in tags]
    items += list(extra)
    return json.dumps(items)


def make_city(n_per=12, seed=7):
    """(features frame, text, image, has_image, cluster per place); place_ids are CID-like strings
    beyond int64."""
    rng = np.random.default_rng(seed)
    d_text, d_img = 24, 12
    text_dirs = rng.normal(size=(4, d_text))
    img_dirs = rng.normal(size=(4, d_img))
    rows, text, image, has_image, cluster = [], [], [], [], []
    i = 0
    for th in THEMES:
        for k in range(4):
            for _ in range(n_per):
                pid = str(10**19 + 7919 * i)
                axes = np.clip(20 + 20 * k + rng.normal(scale=6, size=len(AXES)), 0, 100)
                row = {
                    "place_id": pid, "name": f"place {i}", "theme": th,
                    "ai_tags_json": _tags_json(CLUSTER_TAGS[k][: 1 + i % 3],
                                               [{"tag": "noise_low", "confidence": "low", "polarity": "positive"},
                                                {"tag": "noise_neg", "confidence": "high", "polarity": "negative"}]),
                    "ai_tags_csv": ",".join(CLUSTER_TAGS[k]),
                    "google_rating": round(float(rng.uniform(3.5, 5.0)), 1),
                    "google_user_rating_count": float(rng.integers(0, 5000)),
                    "bayesian_rating": round(float(rng.uniform(3.8, 4.9)), 3),
                }
                for j, col in enumerate(AXES):
                    row[col] = float(axes[j])
                rows.append(row)
                text.append(text_dirs[k] + 0.45 * rng.normal(size=d_text))
                image.append(img_dirs[k] + 0.45 * rng.normal(size=d_img))
                has_image.append(i % 9 != 4)          # some places have no photo
                cluster.append(k)
                i += 1
    feats = pd.DataFrame(rows)
    # a place without axes, one without tags, one with an unknown rating
    feats.loc[5, list(AXES)] = np.nan
    feats.loc[6, ["ai_tags_json", "ai_tags_csv"]] = np.nan
    feats.loc[7, ["google_rating", "bayesian_rating"]] = np.nan
    image = np.asarray(image)
    has_image = np.asarray(has_image)
    image[~has_image] = 0.0
    return feats, np.asarray(text), image, has_image, np.asarray(cluster)


@pytest.fixture(scope="module")
def city():
    feats, text, image, has_image, cluster = make_city()
    art = build_taste_artifacts(feats["place_id"].tolist(), feats["theme"].tolist(), text, image, has_image, feats)
    cold = cold_start_map(feats)
    return {"feats": feats, "text": text, "image": image, "has_image": has_image, "cluster": cluster,
            "art": art, "cold": cold, "pids": feats["place_id"].tolist(), "themes": feats["theme"].tolist()}


def ids_of(city, cluster, theme=None, n=None):
    f = city["feats"]
    mask = city["cluster"] == cluster
    if theme is not None:
        mask &= (f["theme"] == theme).to_numpy()
    out = f.loc[mask, "place_id"].tolist()
    return out[:n] if n else out


def run(city, favs=(), wtg=(), strength=0.5, taste="default"):
    return interest_map(city["pids"], city["themes"], city["cold"], city["art"] if taste == "default" else taste,
                        favs, wtg, strength)


# --------------------------------------------------------------------------- #
# cold start
# --------------------------------------------------------------------------- #
def test_cold_start_formula_and_fallbacks():
    rows = pd.DataFrame({
        "place_id": ["a", "b", "c", "d"],
        "google_user_rating_count": [100, np.nan, -5, 2000],
        "bayesian_rating": [4.5, 3.9, np.nan, 5.2],
        "google_rating": [1.0, 1.0, 1.0, 1.0],          # ignored: bayesian_rating wins
    })
    n = np.array([100.0, 0.0, 0.0, 2000.0])
    fame = np.log1p(n) / np.log1p(2000.0)
    quality = np.array([0.5, 0.0, 0.3, 1.0])
    np.testing.assert_array_equal(cold_start(rows), 0.7 * fame + 0.3 * quality)
    assert cold_start_map(rows) == dict(zip("abcd", (0.7 * fame + 0.3 * quality).tolist()))
    # google_rating when there is no bayesian_rating
    r2 = rows.drop(columns="bayesian_rating")
    np.testing.assert_allclose(cold_start(r2), 0.7 * fame + 0.3 * 0.0)
    # no review counts -> min-max of map_visibility_score (NaN -> 0), else google_rating, else zeros
    r3 = pd.DataFrame({"place_id": ["a", "b", "c"], "map_visibility_score": [10.0, np.nan, 30.0]})
    assert cold_start(r3).tolist() == [0.0, 0.0, 1.0]
    r4 = pd.DataFrame({"place_id": ["a", "b"], "google_rating": [4.0, 4.0]})
    assert cold_start(r4).tolist() == [0.0, 0.0]
    assert cold_start(pd.DataFrame({"place_id": ["a"]})).tolist() == [0.0]


# --------------------------------------------------------------------------- #
# interest_map invariants
# --------------------------------------------------------------------------- #
def test_no_favourites_is_exactly_cold_start(city):
    for res in (run(city), run(city, favs=["not-a-place"]), run(city, taste=None, favs=ids_of(city, 0, n=2))):
        assert res.mode == MODE_POPULARITY
        assert res.map == {p: city["cold"][p] for p in city["pids"]}
        assert res.used == [] and res.profiles == 0 and res.taste_pct is None and res.similar_to is None


def test_strength_zero_is_exactly_cold_start(city):
    res = run(city, favs=ids_of(city, 0, n=3), strength=0.0)
    assert res.mode == MODE_POPULARITY
    assert res.map == {p: city["cold"][p] for p in city["pids"]}


def test_every_place_has_a_value_and_seeds_keep_cold(city):
    favs = ids_of(city, 2, "culture_sights", n=2) + ids_of(city, 2, "food_drink", n=1)
    res = run(city, favs=favs)
    assert res.mode == MODE_FAVOURITES and res.profiles == 1
    assert list(res.map) == city["pids"]
    vals = np.array(list(res.map.values()))
    assert np.isfinite(vals).all() and (vals >= 0).all() and (vals <= 1).all()
    for s in favs:
        assert res.map[s] == city["cold"][s]
        assert res.taste_pct[s] == 1.0 and res.similar_to[s] == s
    assert res.used == favs and res.ignored == []


def test_per_theme_multiset_of_values_is_the_cold_multiset(city):
    favs = ids_of(city, 1, n=2) + ids_of(city, 3, "nature_outdoors", n=3)
    for strength in (0.3, 0.5, 1.0):
        res = run(city, favs=favs, wtg=ids_of(city, 0, n=1), strength=strength)
        for th in THEMES:
            members = [p for p, t in zip(city["pids"], city["themes"]) if t == th]
            assert sorted(res.map[p] for p in members) == sorted(city["cold"][p] for p in members)


def test_deterministic_and_independent_of_input_order(city):
    favs = ids_of(city, 0, n=2) + ids_of(city, 2, n=2)
    a = run(city, favs=favs)
    b = run(city, favs=favs)
    assert a.map == b.map and a.taste_pct == b.taste_pct and a.similar_to == b.similar_to
    perm = np.random.default_rng(3).permutation(len(city["pids"]))
    shuffled = interest_map([city["pids"][i] for i in perm], [city["themes"][i] for i in perm], city["cold"],
                            city["art"], favs)
    assert shuffled.map == a.map
    assert shuffled.taste_pct == a.taste_pct


def test_taste_reorders_within_theme_towards_the_favourites(city):
    favs = ids_of(city, 2, "culture_sights", n=3)            # historic / museum taste
    res = run(city, favs=favs, strength=1.0)
    for th in THEMES:
        pct = {k: np.mean([res.taste_pct[p] for p in ids_of(city, k, th) if p not in favs]) for k in range(4)}
        assert max(pct, key=pct.get) == 2, (th, pct)
        # strength 1: the theme's best non-seed taste gets the theme's highest blendable cold value
        members = [p for p, t in zip(city["pids"], city["themes"]) if t == th and p not in favs]
        top = max(members, key=lambda p: res.taste_pct[p])
        assert res.map[top] == max(city["cold"][p] for p in members)
    # places "similar to" a favourite point at one of the favourites
    assert set(res.similar_to.values()) <= set(favs)
    # with strength 0.5 the cluster still gains on average, and a theme without seeds changes too
    half = run(city, favs=favs)
    gain = np.mean([half.map[p] - city["cold"][p] for p in ids_of(city, 2, "nature_outdoors")])
    assert gain > 0


def test_want_to_go_is_a_weaker_seed_than_a_favourite(city):
    a, b = ids_of(city, 0, "food_drink", n=1), ids_of(city, 1, "food_drink", n=1)

    def mean_pct(res, k):
        return np.mean([res.taste_pct[p] for p in ids_of(city, k) if p not in a + b])

    fav_a = run(city, favs=a, wtg=b)
    fav_b = run(city, favs=b, wtg=a)
    assert mean_pct(fav_a, 0) > mean_pct(fav_a, 1)
    assert mean_pct(fav_b, 1) > mean_pct(fav_b, 0)
    rows, weights, used, _ = resolve_seeds(city["art"], a, b)
    assert used == a + b and sorted(weights.tolist()) == [0.55, 1.0]
    # a place in both lists counts once, as a favourite
    both = run(city, favs=a, wtg=a)
    assert both.used == a and both.map == run(city, favs=a).map
    rows, weights, _, _ = resolve_seeds(city["art"], a, a)
    assert weights.tolist() == [1.0]


def test_unknown_ids_are_ignored(city):
    good = ids_of(city, 3, n=2)
    res = run(city, favs=[good[0], "123", "", None, good[0]], wtg=["456", good[1]])
    assert res.used == good and res.ignored == ["123", "456"]
    assert res.map == run(city, favs=[good[0]], wtg=[good[1]]).map
    none = run(city, taste=None, favs=["1", "2"], wtg=["2", "3"])
    assert none.ignored == ["1", "2", "3"] and none.mode == MODE_POPULARITY
    assert run(city, favs=good[0]).used == [good[0]]        # a bare id string is one id


def test_seed_cap_keeps_favourites_first(city):
    favs, wtg = ids_of(city, 0, n=3), ids_of(city, 1, n=3)
    rows, weights, used, ignored = resolve_seeds(city["art"], favs, wtg, max_seeds=4)
    assert used == favs + wtg[:1] and ignored == wtg[1:]
    assert len(rows) == 4


def test_places_unknown_to_the_artifacts_keep_cold(city):
    pids = city["pids"] + ["777", "778"]
    themes = city["themes"] + ["food_drink", "brand_new_theme"]
    cold = dict(city["cold"], **{"777": 0.42, "778": 0.1})
    res = interest_map(pids, themes, cold, city["art"], ids_of(city, 0, n=2))
    assert res.map["777"] == 0.42 and res.map["778"] == 0.1
    assert "777" not in res.taste_pct and "777" not in res.similar_to
    # a place missing from `cold` counts as 0; duplicates keep their first occurrence
    dup = interest_map(["999", "999"], ["food_drink", "x"], {}, city["art"], ids_of(city, 0, n=2))
    assert dup.map == {"999": 0.0}


def test_sklearn_missing_falls_back_to_one_profile(city, monkeypatch):
    seeds = ids_of(city, 0, "food_drink", n=4) + ids_of(city, 3, "nature_outdoors", n=4)
    pytest.importorskip("sklearn")
    assert run(city, favs=seeds).profiles == 2
    for mod in ("sklearn", "sklearn.cluster", "sklearn.metrics"):
        monkeypatch.setitem(sys.modules, mod, None)
    res = run(city, favs=seeds)
    assert res.profiles == 1 and res.mode == MODE_FAVOURITES


def test_cluster_profiles_rules():
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=16), rng.normal(size=16)
    two = np.vstack([a + 0.05 * rng.normal(size=(4, 16)), b + 0.05 * rng.normal(size=(4, 16))])
    labels = cluster_profiles(two)
    assert len(set(labels[:4])) == 1 and len(set(labels[4:])) == 1 and labels[0] != labels[4]
    assert cluster_profiles(two[:3]).tolist() == [0, 0, 0]               # < 4 seeds: one profile
    one = a + 0.05 * rng.normal(size=(8, 16))
    assert set(cluster_profiles(one).tolist()) == {0}                     # coherent: no split
    lonely = np.vstack([a + 0.05 * rng.normal(size=(5, 16)), b[None, :]])
    assert set(cluster_profiles(lonely).tolist()) == {0}                  # never isolate a singleton


def test_max_over_profiles(city):
    pytest.importorskip("sklearn")
    s0, s3 = ids_of(city, 0, "food_drink", n=4), ids_of(city, 3, "nature_outdoors", n=4)
    both = taste_scores(city["art"], s0 + s3)
    assert len(both.profiles) == 2
    pool = both.pool
    one0 = taste_scores(city["art"], s0, pool=pool)
    one3 = taste_scores(city["art"], s3, pool=pool)
    np.testing.assert_allclose(both.score[pool], np.maximum(one0.score[pool], one3.score[pool]), atol=1e-12)


# --------------------------------------------------------------------------- #
# taste channels
# --------------------------------------------------------------------------- #
def test_taste_pool_and_density_override(city):
    art = city["art"]
    seeds = ids_of(city, 1, n=2)
    pool = np.zeros(len(art), dtype=bool)
    pool[:30] = True
    ts = taste_scores(art, seeds, pool=pool)
    seed_rows = [art.row_of(s) for s in seeds]
    assert not ts.pool[seed_rows].any()
    assert np.isnan(ts.score[~ts.pool]).all() and np.isfinite(ts.score[ts.pool]).all()
    assert (ts.profile[~ts.pool] == -1).all() and (ts.similar[ts.pool] >= 0).all()
    other = taste_scores(art, seeds, pool=pool, density=np.zeros(len(art)))
    assert not np.allclose(other.score[ts.pool], ts.score[ts.pool])
    assert taste_scores(art, ["nope"]) is None


def test_missing_photo_policy_zero(city):
    art = city["art"]
    seeds = ids_of(city, 0, n=2)
    image_only = {c: 0.0 for c in wi.TASTE_WEIGHTS} | {"image": 1.0}
    ts = taste_scores(art, seeds, weights=image_only)
    no_photo = ts.pool & ~art.has_image
    assert no_photo.any() and (ts.score[no_photo] == 0).all()
    with_photo = ts.pool & art.has_image
    assert (ts.score[with_photo] > 0).all()
    # a seed set without any photo switches the photo channel off instead of zeroing everyone
    no_photo_seed = [art.place_ids[i] for i in np.flatnonzero(~art.has_image)[:1]]
    off = taste_scores(art, no_photo_seed)
    ref = taste_scores(art, no_photo_seed, weights={"image": 0.0})
    np.testing.assert_array_equal(off.score, ref.score)


def test_tag_axis_price_channels_match_the_engine_formulas(city):
    art = city["art"]
    seeds = ids_of(city, 2, n=2)
    rows = [art.row_of(s) for s in seeds]
    for channel in ("tag", "axis", "price", "quality"):
        w = {c: 0.0 for c in wi.TASTE_WEIGHTS} | {channel: 1.0}
        got = taste_scores(art, seeds, weights=w)
        pool = np.flatnonzero(got.pool)
        if channel == "tag":
            prof = set().union(*[set(art.tags[r]) for r in rows])
            want = [len(set(art.tags[i]) & prof) / len(set(art.tags[i]) | prof) if art.tags[i] else 0.0 for i in pool]
        elif channel == "axis":
            centre = art.axes[rows].mean(axis=0)
            want = np.clip(1 - np.abs(art.axes[pool] - centre).mean(axis=1) / 100, 0, 1)
            has = art.has_axes[pool]
            want[~has] = np.median(want[has])
        elif channel == "price":
            j = AXES.index("axis_cheap_expensive")
            want = np.clip(1 - np.abs(art.axes[pool, j] - art.axes[rows, j].mean()) / 100, 0, 1)
        else:
            want = art.quality[pool]
        np.testing.assert_allclose(got.score[pool], want, atol=1e-12, err_msg=channel)


def test_pct_is_pandas_average_rank():
    x = np.random.default_rng(0).integers(0, 40, 500).astype(float)
    np.testing.assert_array_equal(wi._pct(x), pd.Series(x).rank(method="average", pct=True).to_numpy())
    assert wi._pct(np.array([])).size == 0
    assert wi._pct(np.array([3.0])).tolist() == [1.0]


# --------------------------------------------------------------------------- #
# building / persistence
# --------------------------------------------------------------------------- #
def test_parse_tag_names_port():
    row = {"ai_tags_json": json.dumps([
        {"tag": "cozy ", "confidence": "high", "polarity": "positive"},
        {"tag": "Loud", "confidence": "LOW", "polarity": "positive"},
        {"tag": "dirty", "confidence": "high", "polarity": "Negative"},
        "quiet", {"tag": "cozy"}, {"tag": None}]), "ai_tags_csv": "x,y"}
    assert parse_tag_names(row) == ["cozy", "quiet"]
    assert parse_tag_names(row, include_low_confidence=True, include_negative=True) == ["Loud", "cozy", "dirty", "quiet"]
    assert parse_tag_names({"ai_tags_json": "[]", "ai_tags_csv": "b; a,  c ,a"}) == ["a", "b", "c"]
    assert parse_tag_names({"ai_tags_json": "not json", "ai_tags_csv": "['k', 'j']"}) == ["j", "k"]
    assert parse_tag_names({"ai_tags_json": np.nan, "ai_tags_csv": np.nan}) == []
    assert parse_tag_names({}) == []


def test_quality_v2_port():
    f = pd.DataFrame({"google_rating": [5.0, 4.0, np.nan, 6.0], "google_user_rating_count": [2, 300, 10, np.nan]})
    c = np.mean([5.0, 4.0, 5.0])             # catalog mean after clipping to [0, 5]
    r = np.array([5.0, 4.0, c, 5.0])
    v = np.array([2.0, 300.0, 10.0, 0.0])
    want = ((v / (v + 25)) * r + (25 / (v + 25)) * c) / 5
    np.testing.assert_allclose(quality_score_v2(f), want, atol=1e-15)
    assert quality_score_v2(pd.DataFrame({"x": [1, 2]})).tolist() == [0.5, 0.5]


def test_csls_density_matches_brute_force():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(50, 8)).astype(np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    has = np.ones(50, dtype=bool)
    has[[3, 10]] = False
    got = csls_density(x, has, k=5, block=7)
    sims = x @ x.T
    for i in range(50):
        if not has[i]:
            assert got[i] == 0
            continue
        others = [sims[i, j] for j in range(50) if j != i and has[j]]
        assert got[i] == pytest.approx(np.mean(sorted(others)[-5:]), abs=1e-6)


def test_build_normalises_and_flags(city):
    art = city["art"]
    n = len(city["pids"])
    assert art.text.dtype == np.float16 and art.image.dtype == np.float16
    norms = np.linalg.norm(art.text.astype(np.float32), axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=2e-3)
    assert art.has_text.all() and (art.has_image == city["has_image"]).all()
    assert (art.image[~art.has_image] == 0).all()
    assert art.csls_density.dtype == np.float32 and art.csls_density.shape == (n,)
    assert not art.has_axes[5] and art.has_axes.sum() == n - 1 and (art.axes[5] == 50).all()
    assert art.tags[6] == () and "noise_low" not in set().union(*map(set, art.tags))
    assert art.meta["version"] == wi.INTEREST_VERSION and art.meta["csls_k"] == 10
    # zero / non-finite embedding rows -> no text; mismatched inputs are rejected
    text = city["text"].copy()
    text[0] = 0
    text[1, 0] = np.nan
    a2 = build_taste_artifacts(city["pids"], city["themes"], text, None, None, city["feats"])
    assert not a2.has_text[0] and not a2.has_text[1] and a2.image is None and not a2.has_image.any()
    with pytest.raises(ValueError):
        build_taste_artifacts(city["pids"][:-1], city["themes"], city["text"], features=city["feats"])
    with pytest.raises(ValueError):
        build_taste_artifacts(city["pids"][:2] * 2, ["a"] * 4, city["text"][:4])


def test_save_load_roundtrip(city, tmp_path):
    art = city["art"]
    paths = art.save(tmp_path / "interest")
    assert sorted(p.name for p in paths.values()) == sorted(wi.ARTIFACT_FILES)
    meta = json.loads((tmp_path / "interest" / wi.META_FILE).read_text())
    assert meta["rows"] == len(art) and meta["fingerprint"] == art.fingerprint()
    for mmap in (True, False):
        back = TasteArtifacts.load(tmp_path / "interest", mmap=mmap, verify=True)
        assert back.place_ids == art.place_ids and back.themes == art.themes and back.tags == art.tags
        for name in ("text", "image", "has_image", "csls_density", "axes", "has_axes", "quality", "has_text"):
            np.testing.assert_array_equal(getattr(back, name), getattr(art, name), err_msg=name)
        assert back.axis_columns == art.axis_columns and back.fingerprint() == art.fingerprint()
        favs = ids_of(city, 1, n=2)
        a = interest_map(city["pids"], city["themes"], city["cold"], art, favs)
        b = interest_map(city["pids"], city["themes"], city["cold"], back, favs)
        assert a.map == b.map and a.taste_pct == b.taste_pct and a.similar_to == b.similar_to
    # tampering is detected by verify=True
    density = np.load(paths[wi.DENSITY_FILE])
    density[0] += 1
    np.save(paths[wi.DENSITY_FILE], density)
    with pytest.raises(ValueError):
        TasteArtifacts.load(tmp_path / "interest", verify=True)


def test_float32_cache_is_only_a_speed_trade(city, tmp_path):
    city["art"].save(tmp_path)
    fast = TasteArtifacts.load(tmp_path)
    lean = TasteArtifacts.load(tmp_path, float32_cache=False)
    seeds = ids_of(city, 2, n=3)
    a, b = taste_scores(fast, seeds), taste_scores(lean, seeds)
    np.testing.assert_allclose(a.score, b.score, rtol=0, atol=1e-6)
    assert fast.matrix("text").dtype == np.float32 and fast.matrix("text") is fast.matrix("text")
    assert lean.matrix("text").dtype == np.float16 and lean.align(lean.place_ids[:2]).float32_cache is False


def test_save_load_without_photo_store(city, tmp_path):
    art = build_taste_artifacts(city["pids"], city["themes"], city["text"], None, None, city["feats"])
    art.save(tmp_path)
    back = TasteArtifacts.load(tmp_path)
    assert back.image is None and not back.has_image.any() and back.fingerprint() == art.fingerprint()


def test_pickle_and_align(city):
    art = city["art"]
    back = pickle.loads(pickle.dumps(art))
    assert back.fingerprint() == art.fingerprint() and back.row_of(art.place_ids[3]) == 3
    sub = art.align([art.place_ids[5], "unknown", art.place_ids[2]])
    assert sub.place_ids == [art.place_ids[5], "unknown", art.place_ids[2]]
    assert sub.has_text.tolist() == [True, False, True] and sub.tags[1] == ()
    np.testing.assert_array_equal(sub.text[2], art.text[2])
    assert sub.themes[1] == "" and sub.quality[0] == art.quality[5]


def test_artifacts_from_files_align_stores_by_place_id(city, tmp_path):
    feats = city["feats"].copy()
    feats["city"] = ["Bucharest"] * (len(feats) - 2) + ["Tbilisi"] * 2
    feats.to_csv(tmp_path / "catalog.csv", index=False)
    n = len(feats)
    # text store in reverse order, one place missing; photo store keyed by place_id with extra rows
    order = list(range(n))[::-1][1:]                   # drops the last place
    np.save(tmp_path / "location_embeddings_test_city.npy", city["text"][order].astype(np.float32))
    pd.DataFrame({"place_id": feats["place_id"].iloc[order].tolist(), "embedding_row": range(len(order)),
                  "has_embedding": True}).to_csv(tmp_path / "meta.csv", index=False)
    img_rows = [i for i in range(n) if city["has_image"][i]]
    img = np.vstack([city["image"][img_rows], np.ones((2, city["image"].shape[1]))]).astype(np.float16)
    np.save(tmp_path / "img.npy", img)
    pd.DataFrame({"place_id": feats["place_id"].iloc[img_rows].tolist() + ["other1", "other2"],
                  "direct_place_embedding_row": range(len(img)), "has_direct_image_embedding": True,
                  "run_id": "openclip_vitb32_v1", "model_tag": "openclip_vitb32_laion2b"}
                 ).to_parquet(tmp_path / "img_meta.parquet")
    art = taste_artifacts_from_files(tmp_path / "catalog.csv", tmp_path / "location_embeddings_test_city.npy",
                                     tmp_path / "meta.csv", tmp_path / "img.npy", tmp_path / "img_meta.parquet",
                                     city="Bucharest")
    assert art.place_ids == feats["place_id"].tolist()[:-2]
    assert art.has_text.sum() == n - 2 and art.meta["text_run_id"] == "test_city"
    assert art.meta["image_model"] == "openclip_vitb32_v1"
    ref = city["art"]
    np.testing.assert_array_equal(art.text, ref.text[: n - 2])
    np.testing.assert_array_equal(art.has_image, ref.has_image[: n - 2])
    np.testing.assert_allclose(art.image.astype(np.float32), ref.image[: n - 2].astype(np.float32), atol=2e-3)
    assert art.tags == ref.tags[: n - 2]
    # duplicated ids in a store are rejected
    pd.DataFrame({"place_id": ["a", "a"], "embedding_row": [0, 1], "has_embedding": True}).to_csv(
        tmp_path / "bad.csv", index=False)
    with pytest.raises(ValueError):
        taste_artifacts_from_files(tmp_path / "catalog.csv", tmp_path / "location_embeddings_test_city.npy",
                                   tmp_path / "bad.csv")


def test_warmup_and_strength_validation(city):
    wi.warmup(city["art"])
    wi.warmup(None)
    with pytest.raises(ValueError):
        run(city, favs=ids_of(city, 0, n=1), strength=float("nan"))
    clipped = run(city, favs=ids_of(city, 0, n=1), strength=7)
    assert clipped.strength == 1.0 and clipped.map == run(city, favs=ids_of(city, 0, n=1), strength=1.0).map
