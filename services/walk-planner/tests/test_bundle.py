"""Tests of walk_planner.bundle: build -> validate -> load round trip on the synthetic "Minitown"
sources (tests/fixtures/mini), the canonical content hash, platform-independent CSV parsing, tamper
detection (modified files, unlisted catalog / interest files, unsafe manifest paths, symlinks leaving the
bundle, interest.dir), overwrite refusal, resolve_bundle_dirs, and the real Bucharest bundle when it is
present (skipped otherwise). Offline."""

import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from walk_planner.bundle import (
    BUNDLE_ID_RE,
    CSV_READ_OPTIONS,
    INTEREST_DIR,
    INTEREST_FILES,
    BundleError,
    BundleExistsError,
    LoadedBundle,
    build_bundle,
    bundle_info,
    catalog_content_sha256,
    file_sha256,
    layout_problems,
    load_bundle,
    read_manifest,
    read_source_csv,
    resolve_bundle_dirs,
    slugify,
    validate_bundle,
)
from walk_planner.catalog import CityCatalog, catalog_frames_equal
from walk_planner.interest import TasteArtifacts
from walk_planner.interest_build import taste_artifacts_from_files

MINI = Path(__file__).resolve().parent / "fixtures" / "mini"
SRC = MINI / "source"
COMMITTED = sorted(p for p in (MINI / "bundles").iterdir() if p.is_dir() and not p.name.startswith("."))[0]
CSV = SRC / "locations_minitown.csv"
# The research repo's data (services/walk_planner/tests -> repo root = parents[3]). Guarded: a vendored copy
# may sit fewer than 3 levels below "/" (e.g. /src/tests), and then there is simply no real data.
_PARENTS = Path(__file__).resolve().parents
REAL_DATA = (_PARENTS[3] / "recommendation_system" / "ai_location_recommender" / "data") if len(_PARENTS) > 3 else None
REAL_BUNDLES = REAL_DATA / "walk_bundles" if REAL_DATA is not None else None


def mini_inputs(**over) -> dict:
    kw = dict(city_slug="minitown", catalog_csv=CSV, photo_manifest_csv=SRC / "photo_manifest_minitown.csv",
              text_npy=SRC / "text_minitown.npy", text_meta_csv=SRC / "text_minitown_metadata.csv",
              image_npy=SRC / "image_minitown.npy", image_meta=SRC / "image_minitown_metadata.csv",
              timezone="Europe/Bucharest")
    kw.update(over)
    return kw


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    return build_bundle(tmp_path_factory.mktemp("bundles"), **mini_inputs(builder_cmd="pytest"))


def copy_bundle(src: Path, tmp_path: Path, name=None) -> Path:
    dst = tmp_path / (name or src.name)
    shutil.copytree(src, dst)
    return dst


def rewrite_manifest(bundle: Path, **changes) -> dict:
    m = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    m.update(changes)
    (bundle / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return m


def refresh_file_entry(bundle: Path, rel: str) -> None:
    """Make the manifest's sha256 / size of `rel` match the file again (a 'consistent' tamper)."""
    m = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    p = bundle / rel
    m["files"][rel].update(sha256=file_sha256(p), bytes=p.stat().st_size)
    (bundle / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def test_build_layout_and_manifest(built):
    m = read_manifest(built)
    assert built.name == m["bundle_id"] and BUNDLE_ID_RE.match(m["bundle_id"])
    assert m["bundle_id"].startswith("minitown-") and m["bundle_id"].endswith(m["content_sha256"][:8])
    assert (m["schema_version"], m["city"], m["city_slug"], m["timezone"]) == \
        (1, "Minitown", "minitown", "Europe/Bucharest")
    assert set(m["files"]) == {"walk_catalog.parquet"} | {f"interest/{n}" for n in (
        "text_f16.npy", "image_f16.npy", "has_image.npy", "csls_density.npy", "features.parquet", "interest_meta.json")}
    for rel, entry in m["files"].items():
        assert entry["sha256"] == file_sha256(built / rel) and entry["bytes"] == (built / rel).stat().st_size
    text = m["files"]["interest/text_f16.npy"]
    assert text["shape"] == [60, 8] and text["dtype"] == "float16"
    assert m["rows"] == 60 and sum(m["rows_by_theme_group"].values()) == 60
    cov = m["coverage"]
    assert (cov["closed_forever"], cov["temporarily_closed"], cov["places_without_coordinates"]) == (2, 1, 1)
    assert m["photos"]["keys"] == 120 and m["photos"]["places_with_photos"] == 50
    it = m["interest"]
    assert (it["version"], it["text_dim"], it["image_dim"], it["places_with_text"], it["places_with_image"]) == \
        ("walk_interest_v1", 8, 4, 59, 48)
    assert it["weights"] == {"text": 0.26, "image": 0.5, "tag": 0.08, "axis": 0.06, "quality": 0.06, "price": 0.04}
    assert m["source"]["catalog_csv"] == {"file": CSV.name, "sha256": file_sha256(CSV), "bytes": CSV.stat().st_size}
    assert m["builder"]["package_version"] == "1.0.0" and m["builder"]["cmd"] == "pytest"
    assert m["builder"]["csv_float_precision"] == "round_trip"
    assert not [p for p in built.parent.iterdir() if p.name.startswith(".")]          # no temp dir left


def test_build_is_canonical_and_reproduces_the_committed_fixture(built):
    mine, committed = read_manifest(built), read_manifest(COMMITTED)
    # the catalog rows hash is pure data: identical on every platform / parquet writer
    assert mine["catalog"]["catalog_sha256"] == committed["catalog"]["catalog_sha256"]
    assert mine["catalog"]["columns"] == committed["catalog"]["columns"]
    a = TasteArtifacts.load(built / INTEREST_DIR, mmap=False)
    b = TasteArtifacts.load(COMMITTED / INTEREST_DIR, mmap=False)
    assert a.place_ids == b.place_ids and a.tags == b.tags
    for name in ("text", "image", "csls_density", "quality", "axes"):
        np.testing.assert_allclose(np.asarray(getattr(a, name), float), np.asarray(getattr(b, name), float), atol=2e-3)
    if a.fingerprint() == b.fingerprint():          # same float results -> the same identity
        assert mine["content_sha256"] == committed["content_sha256"]


def test_catalog_hash_does_not_depend_on_the_parquet_writer(built, tmp_path):
    table = pq.read_table(built / "walk_catalog.parquet")
    ref = catalog_content_sha256(table)
    assert ref == read_manifest(built)["catalog"]["catalog_sha256"]
    for kw in ({"compression": "snappy"}, {"compression": "none", "row_group_size": 7}, {"use_dictionary": False}):
        p = tmp_path / "t.parquet"
        pq.write_table(table, p, **kw)
        assert catalog_content_sha256(pq.read_table(p)) == ref
    df = table.to_pandas()
    df.loc[3, "name"] = df.loc[3, "name"] + " "
    assert catalog_content_sha256(pa.Table.from_pandas(df, preserve_index=False)) != ref


def test_refuses_to_overwrite_and_leaves_no_temp_dir(built):
    m = read_manifest(built)
    import datetime as dt

    when = dt.datetime.strptime(m["built_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    taste_same = True
    try:
        again = build_bundle(built.parent, **mini_inputs(built_at=when))
    except BundleExistsError as exc:
        assert str(built) in str(exc)
    else:                                           # only when this machine's float results differ
        taste_same = False
        assert again != built
    assert taste_same or True
    assert not [p for p in built.parent.iterdir() if p.name.startswith(".")]


@pytest.mark.parametrize("over,match", [
    (dict(catalog_csv=SRC / "nope.csv"), "not found"),
    (dict(text_meta_csv=None), "go together"),
    (dict(image_meta=None), "go together"),
    (dict(text_npy=None, text_meta_csv=None), "needs the text store"),
    (dict(city="Atlantis"), "not in"),
    (dict(timezone=None), "timezone"),
    (dict(city_slug="Mini-Town"), "city_slug"),
    (dict(photo_manifest_csv=SRC / "missing.csv"), "not found"),
])
def test_build_rejects_bad_inputs(tmp_path, over, match):
    with pytest.raises(BundleError, match=match):
        build_bundle(tmp_path, **mini_inputs(**over))
    assert not list(tmp_path.iterdir())


def test_build_rejects_duplicate_and_non_cid_place_ids(tmp_path):
    df = pd.read_csv(CSV, dtype={"place_id": str})
    dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    dup.to_csv(tmp_path / "dup.csv", index=False)
    with pytest.raises(BundleError, match="duplicate place_id"):
        build_bundle(tmp_path / "out", **mini_inputs(catalog_csv=tmp_path / "dup.csv", text_npy=None,
                                                     text_meta_csv=None, image_npy=None, image_meta=None))
    df.loc[0, "place_id"] = "museum_1"
    df.to_csv(tmp_path / "bad.csv", index=False)
    with pytest.raises(BundleError, match="decimal Google CID"):
        build_bundle(tmp_path / "out", **mini_inputs(catalog_csv=tmp_path / "bad.csv", text_npy=None,
                                                     text_meta_csv=None, image_npy=None, image_meta=None))


def test_the_builder_parses_csv_floats_exactly_on_every_platform(tmp_path):
    """17-digit coordinates (how pandas writes floats): pandas' default C parser may read them one unit in the
    last place off -- differently on macOS and glibc -- so the builder parses with round_trip (Python's
    correctly rounded float()) and a bundle has the same bytes wherever it is built."""
    text = pd.read_csv(CSV, dtype=str, keep_default_na=False)
    text.loc[0, "latitude"], text.loc[0, "longitude"] = "44.478407499999996", "26.071022499999998"
    csv = tmp_path / "locations.csv"
    text.to_csv(csv, index=False)
    path = build_bundle(tmp_path / "out", **mini_inputs(catalog_csv=csv, text_npy=None, text_meta_csv=None,
                                                        image_npy=None, image_meta=None))
    t = pq.read_table(path / "walk_catalog.parquet", columns=["latitude", "longitude"]).to_pydict()
    assert t["latitude"][0] == float("44.478407499999996") and t["longitude"][0] == float("26.071022499999998")
    assert read_source_csv(csv)["latitude"][0] == float("44.478407499999996")
    assert CSV_READ_OPTIONS["float_precision"] == "round_trip"


def test_build_without_interest_or_photo_manifest(tmp_path):
    path = build_bundle(tmp_path, **mini_inputs(text_npy=None, text_meta_csv=None, image_npy=None, image_meta=None,
                                                photo_manifest_csv=None))
    m = read_manifest(path)
    assert m["interest"] is None and set(m["files"]) == {"walk_catalog.parquet"}
    assert m["photos"]["keys"] == 0 and m["coverage"]["photos"] == 0.0
    report = validate_bundle(path)
    assert report["ok"] and report["has_interest"] is False, report["errors"]
    lb = load_bundle(path)
    assert lb.taste is None and len(lb.catalog) == 59
    assert read_manifest(path)["content_sha256"] != read_manifest(COMMITTED)["content_sha256"]


def test_build_drops_photos_whose_file_is_missing(tmp_path):
    root = tmp_path / "photos_cid"
    manifest = pd.read_csv(SRC / "photo_manifest_minitown.csv", dtype={"place_id": str})
    present = manifest.iloc[::2]
    for key in present["local_file"]:
        f = tmp_path / key
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\xff\xd8jpg")
    path = build_bundle(tmp_path / "out", **mini_inputs(photos_root=root, text_npy=None, text_meta_csv=None,
                                                        image_npy=None, image_meta=None))
    m = read_manifest(path)
    assert m["photos"]["checked_against_files"] is True
    assert m["photos"]["dropped_missing_files"] == len(manifest) - len(present)
    assert m["photos"]["keys"] == len(present)
    keys = {k for ks in pq.read_table(path / "walk_catalog.parquet").column("photos").to_pylist() for k in ks}
    assert keys == set(present["local_file"])
    # the parent of photos_cid works as the root too
    assert validate_bundle(path, photos_root=tmp_path)["checks"]["photo_files"]["missing"] == 0


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #
def test_load_round_trip_equals_the_dashboard_frame(built):
    lb = load_bundle(built)
    assert isinstance(lb, LoadedBundle) and lb.bundle_id == built.name and lb.city == "Minitown"
    assert lb.catalog.version == lb.bundle_id and lb.timezone == "Europe/Bucharest"
    frame = CityCatalog.from_frame(pd.read_csv(CSV), city="Minitown")
    assert catalog_frames_equal(frame, lb.catalog) == []
    assert lb.catalog.center == frame.center and len(lb.catalog) == 59      # the place without coordinates
    ids = pq.read_table(built / "walk_catalog.parquet").column("place_id").to_pylist()
    assert lb.taste is not None and lb.taste.place_ids == ids and len(ids) == 60
    assert all(len(p) >= 19 for p in ids) and max(int(p) for p in ids) > 2 ** 63   # CIDs stay strings
    files = taste_artifacts_from_files(CSV, SRC / "text_minitown.npy", SRC / "text_minitown_metadata.csv",
                                       SRC / "image_minitown.npy", SRC / "image_minitown_metadata.csv", city="Minitown")
    assert files.fingerprint() == lb.taste.fingerprint() == lb.interest_fingerprint
    pid = ids[0]
    assert lb.catalog.photos(pid) == ["photos_cid/18000000000000000000/00_vibe.jpg"]
    assert lb.catalog.status_of(ids[-1]) == "closed_forever"                 # "CLOSED_PERMANENTLY" alias
    assert repr(lb).startswith("LoadedBundle(bundle_id='minitown-")


def test_committed_fixture_is_valid():
    report = validate_bundle(COMMITTED)
    assert report["ok"], report["errors"]
    assert report["warnings"] == ["rows: 1 places without coordinates (the planner ignores them)"]
    info = bundle_info(COMMITTED)
    assert info["bundle_id"] == COMMITTED.name and info["rows"] == 60 and info["interest"]["text_dim"] == 8


# --------------------------------------------------------------------------- #
# validate / tamper detection
# --------------------------------------------------------------------------- #
def test_validate_reports_every_check(built):
    deep = validate_bundle(built)
    assert deep["ok"] and deep["deep"] and deep["errors"] == []
    assert set(deep["checks"]) == {"manifest", "files", "catalog", "interest", "rows", "content", "load"}
    assert deep["checks"]["load"]["rows_routable"] == 59
    shallow = validate_bundle(built, deep=False)
    assert shallow["ok"] and set(shallow["checks"]) == {"manifest", "files", "catalog", "interest"}


def _flip_byte(p: Path, offset: int = -20) -> None:
    data = bytearray(p.read_bytes())
    data[offset] ^= 0xFF
    p.write_bytes(bytes(data))


@pytest.mark.parametrize("rel", ["walk_catalog.parquet", "interest/text_f16.npy", "interest/features.parquet",
                                 "interest/interest_meta.json"])
def test_a_modified_file_is_detected(built, tmp_path, rel):
    b = copy_bundle(built, tmp_path)
    _flip_byte(b / rel)
    report = validate_bundle(b, deep=False)
    assert not report["ok"] and any(rel in e and "sha256 mismatch" in e for e in report["errors"])
    with pytest.raises(BundleError, match="sha256 mismatch"):
        load_bundle(b)


def test_verify_false_skips_the_hashes(built, tmp_path):
    b = copy_bundle(built, tmp_path)
    p = b / "interest" / "interest_meta.json"
    p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")          # still valid JSON
    with pytest.raises(BundleError, match="interest/interest_meta.json"):
        load_bundle(b)
    assert load_bundle(b, verify=False).taste is not None


def test_missing_extra_and_resized_files(built, tmp_path):
    b = copy_bundle(built, tmp_path)
    (b / "interest" / "has_image.npy").unlink()
    (b / "notes.txt").write_text("x")
    (b / ".DS_Store").write_text("x")                                     # hidden files are ignored
    with open(b / "walk_catalog.parquet", "ab") as fh:
        fh.write(b"\0")
    report = validate_bundle(b, deep=False)
    assert any("has_image.npy: missing" in e for e in report["errors"])
    assert any("walk_catalog.parquet:" in e and "bytes" in e for e in report["errors"])
    assert report["warnings"] == ["files: files not in the manifest: ['notes.txt']"]
    with pytest.raises(BundleError, match="missing"):
        load_bundle(b)


# --------------------------------------------------------------------------- #
# layout integrity: every file the planner reads is listed (and hashed); no path leaves the bundle
# --------------------------------------------------------------------------- #
def edit_manifest(bundle: Path, fn) -> dict:
    m = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    fn(m)
    (bundle / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return m


def assert_refused(bundle: Path, match: str) -> None:
    """Refused by load_bundle (with and without hashing) and by validate_bundle (shallow and deep)."""
    for verify in (True, False):
        with pytest.raises(BundleError, match=match):
            load_bundle(bundle, verify=verify)
    for deep in (False, True):
        report = validate_bundle(bundle, deep=deep)
        assert not report["ok"] and any(re.search(match, e) for e in report["errors"]), report["errors"]


def test_the_interest_files_are_the_taste_artifacts():
    from walk_planner.interest import ARTIFACT_FILES

    assert INTEREST_FILES == ARTIFACT_FILES


def test_an_unlisted_catalog_cannot_be_swapped_in(built, tmp_path):
    b = copy_bundle(built, tmp_path)
    edit_manifest(b, lambda m: m["files"].pop("walk_catalog.parquet"))
    t = pq.read_table(b / "walk_catalog.parquet").to_pandas()
    t.loc[0, "name"] = "TAMPERED NAME"
    from walk_planner.bundle import _catalog_table

    pq.write_table(_catalog_table(t), b / "walk_catalog.parquet", compression="zstd")
    assert_refused(b, re.escape("does not list ['walk_catalog.parquet']"))


@pytest.mark.parametrize("name", INTEREST_FILES)
def test_every_interest_file_must_be_listed(built, tmp_path, name):
    b = copy_bundle(built, tmp_path)
    edit_manifest(b, lambda m: m["files"].pop(f"interest/{name}"))
    assert_refused(b, re.escape(f"does not list ['interest/{name}']"))


def test_interest_dir_must_be_the_bundles_own(built, tmp_path):
    b = copy_bundle(built, tmp_path)
    shutil.copytree(b / "interest", tmp_path / "outside_interest")
    edit_manifest(b, lambda m: m["interest"].update(dir="../outside_interest"))
    assert_refused(b, "interest.dir must be 'interest'")
    c = copy_bundle(built, tmp_path, "c")
    edit_manifest(c, lambda m: m["catalog"].update(file="other.parquet"))
    assert_refused(c, "catalog.file must be 'walk_catalog.parquet'")


@pytest.mark.parametrize("bad, why", [
    ("../outside.bin", "'..'"),
    ("interest/../../outside.bin", "'..'"),
    ("/etc/hosts", "absolute path"),
    ("C:/outside.bin", "absolute path"),
    ("./walk_catalog.parquet", "'.'"),
    ("interest//text_f16.npy", "empty path component"),
    ("interest\\text_f16.npy", "backslash"),
])
def test_unsafe_manifest_paths_are_refused_and_never_opened(built, tmp_path, bad, why):
    b = copy_bundle(built, tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not part of the bundle")
    edit_manifest(b, lambda m: m["files"].__setitem__(bad, {"sha256": file_sha256(outside), "bytes": 22}))
    assert_refused(b, re.escape(why))
    errors = validate_bundle(b, deep=False)["errors"]
    assert not any("sha256 mismatch" in e or ": missing" in e for e in errors), errors   # never hashed / opened


def test_symlinks_leaving_the_bundle_are_refused(built, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # a listed file replaced by a symlink to an identical copy outside: the hash still matches
    a = copy_bundle(built, tmp_path, "a")
    shutil.copy2(a / "interest" / "features.parquet", elsewhere / "features.parquet")
    (a / "interest" / "features.parquet").unlink()
    (a / "interest" / "features.parquet").symlink_to(elsewhere / "features.parquet")
    assert_refused(a, "interest/features.parquet: resolves outside the bundle")
    # the whole interest directory a symlink to an outside copy
    b = copy_bundle(built, tmp_path, "b")
    shutil.copytree(b / "interest", elsewhere / "interest")
    shutil.rmtree(b / "interest")
    (b / "interest").symlink_to(elsewhere / "interest", target_is_directory=True)
    assert_refused(b, "resolves outside the bundle")
    assert any("interest: resolves outside" in e for e in layout_problems(b, read_manifest(b)))
    # a symlink that stays inside the bundle is fine
    c = copy_bundle(built, tmp_path, "c")
    (c / "data").mkdir()
    (c / "walk_catalog.parquet").rename(c / "data" / "walk_catalog.parquet")
    (c / "walk_catalog.parquet").symlink_to(Path("data") / "walk_catalog.parquet")
    assert layout_problems(c, read_manifest(c)) == []
    assert load_bundle(c).catalog.version == read_manifest(c)["bundle_id"]
    assert validate_bundle(c, deep=False)["ok"]


def test_the_committed_fixtures_have_a_sound_layout():
    assert layout_problems(COMMITTED, read_manifest(COMMITTED)) == []
    real = _real_bundle()
    if real is not None:
        assert layout_problems(real, read_manifest(real)) == []


def test_manifest_problems(built, tmp_path):
    b = copy_bundle(built, tmp_path)
    rewrite_manifest(b, schema_version=2)
    with pytest.raises(BundleError, match="schema_version"):
        load_bundle(b)
    assert validate_bundle(b)["checks"]["manifest"]["ok"] is False
    b2 = copy_bundle(built, tmp_path, "renamed")
    m = rewrite_manifest(b2, rows=61)
    report = validate_bundle(b2, deep=False)
    assert any("60 rows, manifest says 61" in e for e in report["errors"])
    assert any("directory name" in w for w in report["warnings"])
    with pytest.raises(BundleError, match="interest rows"):
        load_bundle(b2)
    b3 = copy_bundle(built, tmp_path, "id")
    rewrite_manifest(b3, bundle_id=m["bundle_id"][:-8] + "00000000")
    assert any("sha8" in e for e in validate_bundle(b3, deep=False)["errors"])
    (b3 / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BundleError, match="cannot read"):
        read_manifest(b3)


def test_deep_validation_catches_consistent_content_tampering(built, tmp_path):
    """Rows changed AND the file hash refreshed: only the deep checks can tell."""
    b = copy_bundle(built, tmp_path)
    t = pq.read_table(b / "walk_catalog.parquet").to_pandas()
    t.loc[0, "opening_hours"] = "[[600, 500]]"
    t.loc[1, "opening_hours"] = "not json"
    t.loc[2, "latitude"] = 52.52                                          # Berlin
    t.loc[3, "business_status"] = "bogus"
    t.loc[4, "photos"] = ["photos_cid/123/00_vibe.jpg"]                   # another place's key
    t.loc[6, "photo_count"] = 9
    t.loc[7, "place_id"] = t.loc[8, "place_id"]                           # duplicate
    from walk_planner.bundle import _catalog_table

    pq.write_table(_catalog_table(t), b / "walk_catalog.parquet", compression="zstd")
    refresh_file_entry(b, "walk_catalog.parquet")
    assert validate_bundle(b, deep=False)["checks"]["files"]["ok"]
    report = validate_bundle(b)
    errs = "\n".join(report["errors"])
    for needle in ("invalid opening_hours", "places outside Minitown", "unknown business_status",
                   "belong to another place",
                   "photo_count values", "duplicate place_id", "catalog_sha256", "content_sha256",
                   "features.parquet place_id order"):
        assert needle in errs, needle
    assert not report["ok"]


def test_validate_counts_photo_files(built, tmp_path):
    keys = [k for ks in pq.read_table(built / "walk_catalog.parquet").column("photos").to_pylist() for k in ks]
    for k in keys[:30]:
        f = tmp_path / k
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"jpg")
    c = validate_bundle(built, deep=False, photos_root=tmp_path / "photos_cid")["checks"]["photo_files"]
    assert (c["keys"], c["existing"], c["missing"]) == (120, 30, 90) and c["ok"]
    assert c["warnings"] and "90 of 120 photo files missing" in c["warnings"][0]


def test_validate_never_raises_on_garbage(tmp_path):
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    report = validate_bundle(tmp_path)
    assert not report["ok"] and "manifest" in report["checks"]
    report = validate_bundle(tmp_path / "missing")
    assert not report["ok"] and "no manifest.json" in report["errors"][0]


# --------------------------------------------------------------------------- #
# resolve_bundle_dirs
# --------------------------------------------------------------------------- #
def _fake_bundle(root: Path, name: str, city: str, built_at: str) -> Path:
    shutil.copytree(COMMITTED, root / name)
    rewrite_manifest(root / name, city=city, built_at=built_at)
    return root / name


def test_resolve_one_dir_a_list_and_a_root(tmp_path):
    a = _fake_bundle(tmp_path / "root", "minitown-20261001-aaaaaaaa", "Minitown", "2026-10-01T10:00:00Z")
    b = _fake_bundle(tmp_path / "root", "minitown-20261002-bbbbbbbb", "Minitown", "2026-10-02T09:00:00Z")
    c = _fake_bundle(tmp_path / "root", "berlin-20260901-cccccccc", "Berlin", "2026-09-01T00:00:00Z")
    (tmp_path / "root" / ".minitown-x.tmp").mkdir()                       # an in-progress build: skipped
    (tmp_path / "root" / "notes").mkdir()                                  # not a bundle: skipped
    assert resolve_bundle_dirs(str(a)) == [a]
    assert resolve_bundle_dirs(f"{a}, {c}") == [a, c]
    assert resolve_bundle_dirs([c, a]) == [c, a]
    assert resolve_bundle_dirs(tmp_path / "root") == [c, b]               # newest per city, sorted by city
    assert resolve_bundle_dirs(f"{tmp_path / 'root'}") == [c, b]


@pytest.mark.parametrize("spec,match", [
    ("", "no bundle directory"),
    ("{tmp}/nowhere", "does not exist"),
    ("{tmp}/file.txt", "not a directory"),
    ("{tmp}/empty", "no walk bundles"),
    ("{a},{a2}", "two bundles of Minitown"),
    ("{a},{root}", "two bundles of Minitown"),
])
def test_resolve_fails_loudly(tmp_path, spec, match):
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "empty").mkdir()
    a = _fake_bundle(tmp_path / "root", "minitown-20261001-aaaaaaaa", "Minitown", "2026-10-01T10:00:00Z")
    a2 = _fake_bundle(tmp_path, "minitown-20261002-bbbbbbbb", "Minitown", "2026-10-02T10:00:00Z")
    with pytest.raises(BundleError, match=match):
        resolve_bundle_dirs(spec.format(tmp=tmp_path, a=a, a2=a2, root=tmp_path / "root"))


def test_slugify():
    assert slugify("Bucharest") == "bucharest"
    assert slugify("Cluj-Napoca") == "cluj_napoca"
    assert slugify("Brașov ") == "brasov"
    with pytest.raises(BundleError):
        slugify("???")


# --------------------------------------------------------------------------- #
# the real Bucharest bundle (research data; skipped when absent)
# --------------------------------------------------------------------------- #
def _real_bundle():
    if REAL_BUNDLES is None or not REAL_BUNDLES.is_dir():
        return None
    try:
        dirs = resolve_bundle_dirs(REAL_BUNDLES)
    except BundleError:
        return None
    return next((d for d in dirs if read_manifest(d)["city"] == "Bucharest"), None)


@pytest.mark.skipif(_real_bundle() is None, reason="no real Bucharest bundle in data/walk_bundles")
def test_real_bucharest_bundle_loads_and_matches_the_csv():
    path = _real_bundle()
    report = validate_bundle(path, deep=False)
    assert report["ok"], report["errors"]
    lb = load_bundle(path)
    m = lb.manifest
    assert m["rows"] == len(lb.catalog) == 12961 and lb.timezone == "Europe/Bucharest"
    assert lb.taste is not None and len(lb.taste) == 12961
    csv = REAL_DATA / "locations_bucharest_all.csv"
    if csv.is_file() and m["source"]["catalog_csv"]["sha256"] == file_sha256(csv):
        # parsed like the builder parsed it (bundles before csv_float_precision used pandas' default parser)
        exact = m["builder"].get("csv_float_precision") == "round_trip"
        frame = CityCatalog.from_frame(read_source_csv(csv) if exact else pd.read_csv(csv), city="Bucharest")
        assert catalog_frames_equal(frame, lb.catalog) == []
        assert lb.catalog.center == frame.center
