"""Data bundle of the Walk Planner: one city's catalog + taste artifacts, versioned and verifiable.

A bundle is the unit the service (and the CLI) loads per city — built offline from the research
data, shipped as a directory, never edited in place::

    <out_root>/<bundle_id>/
        manifest.json               identity, provenance, coverage, sha256 of every file
        walk_catalog.parquet        zstd; source (CSV) row order; ``catalog.BUNDLE_COLUMNS``
        interest/                   taste artifacts (``interest.TasteArtifacts.save``), row-aligned
            text_f16.npy  image_f16.npy  has_image.npy  csls_density.npy  features.parquet  interest_meta.json

``bundle_id = <city_slug>-<YYYYMMDD>-<content_sha8>``: the build date (UTC) and the first 8 hex
digits of ``content_sha256``. ``content_sha256`` is CANONICAL — sha256 over the catalog rows as
canonical JSON lines (``catalog_sha256``: file order, sorted keys, null for missing / NaN) plus the
taste artifacts' content fingerprint (``TasteArtifacts.fingerprint``) plus city / timezone / schema —
so it does not depend on the parquet writer, its version or the compression; the per-file sha256 in
``files`` pin the exact bytes of one build.

  build_bundle(out_root, *, city_slug, catalog_csv, ...)  -> Path   (atomic: temp dir + rename; never overwrites)
  validate_bundle(bundle_dir, deep=True, photos_root=None) -> report dict ("ok", "errors", "warnings", "checks")
  load_bundle(bundle_dir, *, verify=True)                -> LoadedBundle(dir, manifest, catalog, taste, bundle_id, city)
  resolve_bundle_dirs(spec)                              -> [bundle dirs]  (one dir, "a,b", or a root: newest per city)

Every failure raises :class:`BundleError` with a message that names the file and the problem; the
service refuses to start on any mismatch (``load_bundle(verify=True)``).

Integrity rules (``validate_bundle`` and ``load_bundle`` alike): every file the planner reads -- the catalog
parquet and, when the manifest has an ``interest`` block, all six interest files -- MUST be listed in
``manifest.files`` (and is then size- and sha256-checked); every listed path must be a plain relative path
inside the bundle (no absolute path, no ``..`` / ``.`` / empty component, no backslash) that does not resolve
outside the bundle through a symlink; ``interest.dir`` must be ``interest`` and ``catalog.file``
``walk_catalog.parquet``. These structural checks run even with ``verify=False`` (they cost no hashing).

CSV parsing is platform-independent: the builder reads every CSV with ``float_precision="round_trip"``
(Python's correctly rounded ``float()``). pandas' default C parser may be off by one unit in the last place
for 17-digit values (``44.478407499999996``) and differs between macOS and glibc, which used to make a
bundle built on Linux differ (another bundle_id) from the same data built on macOS.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Union

import numpy as np
import pandas as pd

from .catalog import (
    BUNDLE_COLUMNS,
    CATALOG_FILE,
    CITY_TIMEZONES,
    MANIFEST_FILE,
    MAX_BUNDLE_PHOTOS,
    NUMERIC_COLUMNS,
    CityCatalog,
    to_bundle_frame,
)
from .core import haversine_km
from .version import ALGORITHM_VERSION, BUNDLE_SCHEMA_VERSION

if TYPE_CHECKING:  # pragma: no cover
    from .interest import TasteArtifacts

__all__ = [
    "INTEREST_DIR", "INTEREST_FILES", "CONTENT_HASH_FORMAT", "BUNDLE_ID_RE", "CITY_SLUG_RE", "PHOTO_KEY_RE",
    "PLACE_ID_RE", "CITY_BBOXES", "MAX_CITY_RADIUS_KM", "KNOWN_THEME_GROUPS", "KNOWN_STATUSES", "REQUIRED_COLUMNS",
    "CSV_READ_OPTIONS", "BundleError", "BundleExistsError", "LoadedBundle",
    "slugify", "file_sha256", "catalog_content_sha256", "content_sha256", "read_source_csv", "layout_problems",
    "build_bundle", "validate_bundle", "load_bundle", "read_manifest", "resolve_bundle_dirs", "bundle_info",
]

INTEREST_DIR = "interest"
# the interest files a bundle with taste artifacts must list in manifest.files (interest.ARTIFACT_FILES)
INTEREST_FILES = ("text_f16.npy", "image_f16.npy", "has_image.npy", "csls_density.npy", "features.parquet",
                  "interest_meta.json")
# pandas.read_csv options of every source CSV: string place ids, platform-independent float parsing
CSV_READ_OPTIONS = {"dtype": {"place_id": str}, "low_memory": False, "float_precision": "round_trip"}
CONTENT_HASH_FORMAT = "walk-bundle-content/v1"
BUNDLE_ID_RE = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9_]*)-(?P<date>[0-9]{8})-(?P<sha8>[0-9a-f]{8})$")
CITY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
# Photo keys: photos_cid/<place_id>/<NN>_<source>.jpg (the source is "vibe" or "all" today; the
# dashboard's manifest loader also ranks "review" photos, so that one is accepted too).
PHOTO_KEY_RE = re.compile(r"^photos_cid/(?P<cid>[0-9]+)/[0-9]{2}_(?:vibe|all|review)\.jpg$")
PLACE_ID_RE = re.compile(r"^[0-9]+$")          # the Google CID in decimal
# (minLon, minLat, maxLon, maxLat) a city's places must lie in — generous (city + its metro area).
CITY_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "Bucharest": (25.70, 44.20, 26.50, 44.70),
}
# Cities without a bbox above: every place within this distance of the median coordinate.
MAX_CITY_RADIUS_KM = 80.0
KNOWN_THEME_GROUPS = frozenset({"food_drink", "sights", "things_to_do", "shopping"})
KNOWN_STATUSES = ("", "closed_forever", "temporarily_closed")
# Columns without which the planner cannot work (validation: error); the rest of BUNDLE_COLUMNS is optional.
REQUIRED_COLUMNS = ("place_id", "name", "latitude", "longitude", "photos", "photo_count")
_FLOAT_COLUMNS = frozenset(NUMERIC_COLUMNS)
_MAX_EXAMPLES = 5


class BundleError(Exception):
    """A bundle cannot be built, read or trusted (the message names the file and the problem)."""


class BundleExistsError(BundleError, FileExistsError):
    """`build_bundle` refuses to overwrite an existing bundle directory."""


# --------------------------------------------------------------------------- #
# Hashing / identity
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    """A city name as a bundle city slug: ASCII lower case, other characters -> "_" ("Bucharest" -> "bucharest")."""
    import unicodedata

    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not slug:
        raise BundleError(f"cannot derive a city slug from {name!r}; pass city_slug")
    return slug


def file_sha256(path: Union[str, Path]) -> str:
    """sha256 (hex) of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(v: Any) -> Any:
    """A cell as a canonical JSON value: None for missing / NaN / ±inf, Python scalars, lists of them."""
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_canonical(x) for x in v]
    return str(v)


def catalog_content_sha256(table) -> str:
    """sha256 (hex) of the catalog rows as canonical JSON lines: one line per row in file order,
    ``json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`` with null for missing /
    NaN values. `table` is a ``pyarrow.Table`` (or a DataFrame, converted like the builder writes it).
    The same rows give the same hash whatever wrote the parquet file (writer, version, compression)."""
    import pyarrow as pa

    if isinstance(table, pd.DataFrame):
        table = _catalog_table(table)
    if not isinstance(table, pa.Table):
        raise TypeError("catalog_content_sha256 expects a pyarrow.Table or a DataFrame")
    names = list(table.column_names)
    cols = [table.column(c).to_pylist() for c in names]
    h = hashlib.sha256()
    for values in zip(*cols) if cols else ():
        row = {n: _canonical(v) for n, v in zip(names, values)}
        h.update(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                 .encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def content_sha256(catalog_sha256: str, interest_fingerprint: Optional[str], *, city: str, timezone: str,
                   schema_version: int = BUNDLE_SCHEMA_VERSION) -> str:
    """The bundle's canonical content hash: catalog rows + taste artifacts + city / timezone / schema."""
    blob = json.dumps({"format": CONTENT_HASH_FORMAT, "schema_version": int(schema_version), "city": city,
                       "timezone": timezone, "catalog_sha256": catalog_sha256,
                       "interest_fingerprint": interest_fingerprint}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def _catalog_schema(columns: Iterable[str]):
    """Explicit Arrow types of walk_catalog.parquet: str ids / text, float64 numerics, list<string> photos."""
    import pyarrow as pa

    fields = []
    for c in columns:
        if c in _FLOAT_COLUMNS:
            fields.append(pa.field(c, pa.float64()))
        elif c == "photos":
            fields.append(pa.field(c, pa.list_(pa.string())))
        elif c == "photo_count":
            fields.append(pa.field(c, pa.int32()))
        else:
            fields.append(pa.field(c, pa.string()))
    return pa.schema(fields)


def _catalog_table(frame: pd.DataFrame):
    """``to_bundle_frame`` output as the Arrow table written to walk_catalog.parquet (explicit schema)."""
    import pyarrow as pa

    return pa.Table.from_pandas(frame, schema=_catalog_schema(frame.columns), preserve_index=False)


def read_source_csv(path: Union[str, Path]) -> pd.DataFrame:
    """A source CSV as the bundle builder reads it (``CSV_READ_OPTIONS``): place_id as str, floats parsed
    with ``float_precision="round_trip"`` -- the same values on every platform."""
    return pd.read_csv(path, **CSV_READ_OPTIONS)


def _read_csv(path: Path, what: str) -> pd.DataFrame:
    if not path.is_file():
        raise BundleError(f"{what} not found: {path}")
    try:
        return read_source_csv(path)
    except Exception as exc:        # pandas raises many types; the message names the file
        raise BundleError(f"cannot read {what} {path}: {type(exc).__name__}: {exc}") from exc


def _resolve_city(df: pd.DataFrame, city: Optional[str], path: Path) -> tuple[str, bool]:
    """(exact city name, the CSV has a usable city column). A given name is matched case-insensitively."""
    if "city" in df.columns and df["city"].notna().any():
        cities = sorted(str(c) for c in df["city"].dropna().unique())
        if city is None:
            if len(cities) != 1:
                raise BundleError(f"{path} holds {len(cities)} cities ({', '.join(cities[:8])}); pass city=")
            return cities[0], True
        folded = {c.casefold(): c for c in cities}
        if str(city).casefold() not in folded:
            raise BundleError(f"city {city!r} is not in {path} (it has: {', '.join(cities[:8])})")
        return folded[str(city).casefold()], True
    if not city:
        raise BundleError(f"{path} has no city column; pass city=")
    return str(city), False


def _photo_file(photos_root: Path, key: str) -> Path:
    """File of a photo key under `photos_root` (the directory holding the per-place folders)."""
    rel = key.split("/", 1)[1] if key.startswith("photos_cid/") else key
    return photos_root.joinpath(*rel.split("/"))


def _photos_dir(photos_root: Union[str, Path]) -> Path:
    """`photos_root` as the directory that holds ``<cid>/<NN>_<source>.jpg`` (its parent works too)."""
    root = Path(photos_root).expanduser()
    if (root / "photos_cid").is_dir():
        root = root / "photos_cid"
    if not root.is_dir():
        raise BundleError(f"photos_root is not a directory: {root}")
    return root


def _filter_existing_photos(manifest: pd.DataFrame, photos_root: Path) -> tuple[pd.DataFrame, int]:
    """Drop photo-manifest rows whose file is missing under `photos_root` (the dashboard's loader does
    the same). Returns (kept rows, number dropped)."""
    from .catalog import _photo_key

    path_col = next((c for c in ("bundle_relative_file", "local_file", "resolved_file", "original_file",
                                 "resized_file") if c in manifest.columns), None)
    if path_col is None:
        return manifest, 0
    keep = []
    for pid, path in zip(manifest["place_id"].astype(str), manifest[path_col]):
        key = _photo_key(pid, path)
        keep.append(key is not None and _photo_file(photos_root, key).is_file())
    mask = np.asarray(keep, dtype=bool)
    return manifest[mask], int((~mask).sum())


def _git_sha() -> Optional[str]:
    """git HEAD of the package's checkout (None outside git / without git)."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parent),
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _source_entry(path: Optional[Union[str, Path]]) -> Optional[dict]:
    if path is None:
        return None
    p = Path(path)
    return {"file": p.name, "sha256": file_sha256(p), "bytes": p.stat().st_size}


def _file_entries(bundle_dir: Path) -> dict[str, dict]:
    """{relative path: {sha256, bytes[, shape, dtype]}} of every file under `bundle_dir` except the manifest."""
    out: dict[str, dict] = {}
    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(bundle_dir).as_posix()
        if rel == MANIFEST_FILE:
            continue
        entry: dict[str, Any] = {"sha256": file_sha256(p), "bytes": p.stat().st_size}
        if p.suffix == ".npy":
            arr = np.load(p, mmap_mode="r", allow_pickle=False)
            entry["shape"] = [int(x) for x in arr.shape]
            entry["dtype"] = str(arr.dtype)
            del arr
        out[rel] = entry
    return out


def _coverage(frame: pd.DataFrame) -> dict:
    n = max(len(frame), 1)
    status = frame["business_status"] if "business_status" in frame.columns else pd.Series([""] * len(frame))
    hours = frame["opening_hours"] if "opening_hours" in frame.columns else pd.Series([None] * len(frame))
    n_forever = int((status == "closed_forever").sum())
    n_temp = int((status == "temporarily_closed").sum())
    n_hours = int(sum(1 for v in hours if isinstance(v, str) and v.strip().startswith("[")))
    n_photos = int((frame["photo_count"] > 0).sum()) if "photo_count" in frame.columns else 0
    lat = pd.to_numeric(frame["latitude"], errors="coerce")
    lon = pd.to_numeric(frame["longitude"], errors="coerce")
    return {
        "opening_hours": round(n_hours / n, 4), "photos": round(n_photos / n, 4),
        "closed": round((n_forever + n_temp) / n, 4),
        "places_with_opening_hours": n_hours, "places_with_photos": n_photos,
        "closed_forever": n_forever, "temporarily_closed": n_temp,
        "places_without_coordinates": int((lat.isna() | lon.isna()).sum()),
    }


def _bbox(frame: pd.DataFrame) -> Optional[list[float]]:
    lat = pd.to_numeric(frame["latitude"], errors="coerce").dropna()
    lon = pd.to_numeric(frame["longitude"], errors="coerce").dropna()
    if lat.empty or lon.empty:
        return None
    return [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]


def build_bundle(out_root: Union[str, Path], *, city_slug: Optional[str], catalog_csv: Union[str, Path],
                 photo_manifest_csv: Optional[Union[str, Path]] = None, text_npy: Optional[Union[str, Path]] = None,
                 text_meta_csv: Optional[Union[str, Path]] = None, image_npy: Optional[Union[str, Path]] = None,
                 image_meta: Optional[Union[str, Path]] = None, city: Optional[str] = None,
                 timezone: Optional[str] = None, photos_root: Optional[Union[str, Path]] = None,
                 builder_cmd: Optional[str] = None, max_photos: int = MAX_BUNDLE_PHOTOS,
                 built_at: Optional[_dt.datetime] = None,
                 progress: Optional[Callable[[str], None]] = None) -> Path:
    """Build one city's bundle under ``<out_root>/<bundle_id>/`` and return that directory.

    catalog_csv          the city catalog (``locations_<city>_all.csv``); rows of `city` (case-insensitive;
                         default: the CSV's only city), in CSV order, all kept (closed places too: the
                         cold-start normalisation and the must-visit status checks need them).
    photo_manifest_csv   photo manifest (``place_id, photo_source, local_file, photo_index``); with
                         `photos_root` (the ``photos_cid`` directory) rows whose file is missing are
                         dropped first, like the dashboard's loader. At most `max_photos` keys per place.
    text_npy + text_meta_csv   the text embedding store -> taste artifacts in ``interest/`` (none without them);
    image_npy + image_meta     the OpenCLIP place-image store (optional; needs the text store).
    city_slug            bundle id prefix (``[a-z0-9_]``; None -> ``slugify(city)``); timezone: IANA name
                         (default ``catalog.CITY_TIMEZONES[city]``; required for other cities).
    builder_cmd          the command line recorded in ``manifest.builder.cmd``; built_at (UTC) fixes the
                         build time (reproducible builds / tests); progress(message) reports the steps.

    Atomic: everything is written into a temporary directory next to the target and renamed into
    place at the end. Raises BundleExistsError when ``<out_root>/<bundle_id>`` exists, BundleError on
    bad inputs (missing files, unknown city / timezone, misaligned embedding stores)."""
    say = progress or (lambda _msg: None)
    t_all = time.perf_counter()
    catalog_csv = Path(catalog_csv).expanduser()
    if (text_npy is None) != (text_meta_csv is None):
        raise BundleError("text_npy and text_meta_csv go together (the text embedding store and its metadata)")
    if (image_npy is None) != (image_meta is None):
        raise BundleError("image_npy and image_meta go together (the image embedding store and its metadata)")
    if image_npy is not None and text_npy is None:
        raise BundleError("the image store needs the text store (taste artifacts are built from both)")
    for what, p in (("text embeddings", text_npy), ("text metadata", text_meta_csv), ("image embeddings", image_npy),
                    ("image metadata", image_meta), ("photo manifest", photo_manifest_csv)):
        if p is not None and not Path(p).expanduser().is_file():
            raise BundleError(f"{what} not found: {p}")
    if int(max_photos) < 0:
        raise BundleError("max_photos must be >= 0")

    # 1. catalog rows of the city (CSV order)
    t = time.perf_counter()
    df = _read_csv(catalog_csv, "catalog CSV")
    if "place_id" not in df.columns:
        raise BundleError(f"{catalog_csv} has no place_id column")
    city, has_city_col = _resolve_city(df, city, catalog_csv)
    if has_city_col:
        df = df[df["city"].astype(str) == city].reset_index(drop=True)
    if df.empty:
        raise BundleError(f"{catalog_csv} has no rows for city {city!r}")
    for col in ("latitude", "longitude"):
        if col not in df.columns:
            raise BundleError(f"{catalog_csv} has no {col} column")
    slug = city_slug if city_slug is not None else slugify(city)
    if not CITY_SLUG_RE.match(str(slug)):
        raise BundleError(f"city_slug {slug!r} must match {CITY_SLUG_RE.pattern} (it is the bundle id prefix)")
    tz = timezone or CITY_TIMEZONES.get(city)
    if not tz:
        raise BundleError(f"no known timezone for city {city!r}; pass timezone= (an IANA name, e.g. Europe/Berlin)")
    say(f"catalog: {len(df)} rows of {city} from {catalog_csv.name} ({time.perf_counter() - t:.2f} s)")

    # 2. photos
    t = time.perf_counter()
    photo_info: dict[str, Any] = {"max_per_place": int(max_photos), "checked_against_files": False,
                                  "dropped_missing_files": 0,
                                  "key_pattern": "photos_cid/<place_id>/<NN>_<vibe|all>.jpg"}
    pm = None
    if photo_manifest_csv is not None:
        pm = _read_csv(Path(photo_manifest_csv).expanduser(), "photo manifest")
        if "place_id" not in pm.columns:
            raise BundleError(f"{photo_manifest_csv} has no place_id column")
        pm["place_id"] = pm["place_id"].astype(str)
        if photos_root is not None:
            pm, dropped = _filter_existing_photos(pm, _photos_dir(photos_root))
            photo_info.update(checked_against_files=True, dropped_missing_files=dropped)
    frame = to_bundle_frame(df, photo_manifest_df=pm, max_photos=int(max_photos))
    photo_info["keys"] = int(frame["photo_count"].sum())
    photo_info["places_with_photos"] = int((frame["photo_count"] > 0).sum())
    say(f"photos: {photo_info['keys']} keys for {photo_info['places_with_photos']} places"
        + (f", {photo_info['dropped_missing_files']} manifest rows without a file dropped"
           if photo_info["checked_against_files"] else "") + f" ({time.perf_counter() - t:.2f} s)")
    if not frame["place_id"].map(lambda p: bool(PLACE_ID_RE.match(p))).all():
        bad = [p for p in frame["place_id"] if not PLACE_ID_RE.match(p)][:_MAX_EXAMPLES]
        raise BundleError(f"{catalog_csv}: place_id must be the decimal Google CID; got e.g. {bad}")
    dup = frame["place_id"].duplicated()
    if dup.any():
        dups = frame.loc[dup, "place_id"].head(5).tolist()
        raise BundleError(f"{catalog_csv}: duplicate place_id values, e.g. {dups}")

    t = time.perf_counter()
    table = _catalog_table(frame)
    catalog_sha = catalog_content_sha256(table)
    say(f"catalog table: {table.num_columns} columns, catalog_sha256 {catalog_sha[:12]} "
        f"({time.perf_counter() - t:.2f} s)")

    # 3. taste artifacts (row-aligned with the catalog rows)
    taste = None
    if text_npy is not None:
        from .interest_build import taste_artifacts_from_files

        t = time.perf_counter()
        try:
            taste = taste_artifacts_from_files(
                catalog_csv=catalog_csv, text_npy=Path(text_npy).expanduser(),
                text_meta_csv=Path(text_meta_csv).expanduser(),
                image_npy=Path(image_npy).expanduser() if image_npy is not None else None,
                image_meta=Path(image_meta).expanduser() if image_meta is not None else None,
                city=city if has_city_col else None)
        except (ValueError, OSError) as exc:
            raise BundleError(f"cannot build the taste artifacts: {exc}") from exc
        if list(taste.place_ids) != frame["place_id"].tolist():
            raise BundleError("taste artifacts are not row-aligned with the catalog rows (place_id order differs)")
        say(f"interest: {int(np.count_nonzero(taste.has_text))} places with text, "
            f"{int(np.count_nonzero(taste.has_image))} with photos, fingerprint {taste.fingerprint()[:12]} "
            f"({time.perf_counter() - t:.2f} s)")
    interest_fp = taste.fingerprint() if taste is not None else None
    content = content_sha256(catalog_sha, interest_fp, city=city, timezone=tz)

    # 4. identity + atomic write
    built = (built_at or _dt.datetime.now(_dt.timezone.utc))
    if built.tzinfo is not None:
        built = built.astimezone(_dt.timezone.utc)
    bundle_id = f"{slug}-{built:%Y%m%d}-{content[:8]}"
    out_root = Path(out_root).expanduser()
    final = out_root / bundle_id
    if final.exists():
        raise BundleExistsError(f"bundle already exists (same city, build date and content): {final}")
    out_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", suffix=".tmp", dir=str(out_root)))
    try:
        import pyarrow.parquet as pq

        t = time.perf_counter()
        pq.write_table(table, tmp / CATALOG_FILE, compression="zstd")
        interest_entry = None
        if taste is not None:
            taste.save(tmp / INTEREST_DIR)
            meta = taste.meta or {}
            interest_entry = {
                "version": meta.get("version"), "dir": INTEREST_DIR, "fingerprint": interest_fp,
                "text_model": meta.get("text_model"), "text_run_id": meta.get("text_run_id"),
                "image_model": meta.get("image_model"), "image_model_tag": meta.get("image_model_tag"),
                "csls_k": meta.get("csls_k"), "csls_penalty": meta.get("csls_penalty"), "weights": meta.get("weights"),
                "missing_image_policy": meta.get("missing_image_policy"), "quality": meta.get("quality"),
                "rows": len(taste), "text_dim": int(taste.text.shape[1]),
                "image_dim": int(taste.image.shape[1]) if taste.image is not None else 0,
                "places_with_text": int(np.count_nonzero(taste.has_text)),
                "places_with_image": int(np.count_nonzero(taste.has_image)),
            }
        sources = {"catalog_csv": _source_entry(catalog_csv), "photo_manifest_csv": _source_entry(photo_manifest_csv),
                   "text_npy": _source_entry(text_npy), "text_meta_csv": _source_entry(text_meta_csv),
                   "image_npy": _source_entry(image_npy), "image_meta": _source_entry(image_meta)}
        groups = frame["theme_group"].fillna("").astype(str) if "theme_group" in frame.columns else None
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "city": city,
            "city_slug": slug,
            "timezone": tz,
            "built_at": built.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "builder": {"package": "sloco-walk-planner", "package_version": ALGORITHM_VERSION, "git_sha": _git_sha(),
                        "cmd": builder_cmd, "python": platform.python_version(), "numpy": np.__version__,
                        "pandas": pd.__version__, "pyarrow": _pyarrow_version(),
                        "csv_float_precision": CSV_READ_OPTIONS["float_precision"]},
            "source": {k: v for k, v in sources.items() if v is not None},
            "rows": int(len(frame)),
            "rows_by_theme_group": ({k: int(v) for k, v in groups.value_counts().sort_index().items()}
                                    if groups is not None else {}),
            "coverage": _coverage(frame),
            "photos": photo_info,
            "bbox": _bbox(frame),
            "catalog": {"file": CATALOG_FILE, "columns": list(table.column_names), "catalog_sha256": catalog_sha},
            "interest": interest_entry,
            "content_sha256": content,
            "files": _file_entries(tmp),
        }
        (tmp / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        say(f"written: {sum(f['bytes'] for f in manifest['files'].values()) / 1e6:.1f} MB in "
            f"{len(manifest['files'])} files ({time.perf_counter() - t:.2f} s)")
        _check_loadable(tmp, manifest)
        _make_readable(tmp)
        try:
            os.rename(tmp, final)
        except OSError as exc:
            if final.exists():
                raise BundleExistsError(f"bundle already exists (created concurrently): {final}") from exc
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    say(f"bundle {bundle_id} built in {time.perf_counter() - t_all:.1f} s -> {final}")
    return final


def _make_readable(root: Path) -> None:
    """Directories created by ``tempfile.mkdtemp`` are owner-only (0700): give the bundle the usual
    permissions (0777 / 0666 minus the umask) so a service running as another user can read it."""
    mask = os.umask(0)
    os.umask(mask)
    for p in [root, *root.rglob("*")]:
        try:
            os.chmod(p, (0o777 if p.is_dir() else 0o666) & ~mask)
        except OSError:  # pragma: no cover  (best effort)
            pass


def _pyarrow_version() -> Optional[str]:
    try:
        import pyarrow
    except ImportError:  # pragma: no cover  (a dependency)
        return None
    return pyarrow.__version__


def _check_loadable(bundle_dir: Path, manifest: dict) -> None:
    """Self-check of a fresh build: the catalog loads as the service loads it and the taste artifacts
    (if any) read back row-aligned."""
    catalog = CityCatalog.from_bundle(bundle_dir)
    if catalog.version != manifest["bundle_id"]:
        raise BundleError(f"self-check: catalog version {catalog.version!r} != bundle id {manifest['bundle_id']!r}")
    if manifest.get("interest"):
        from .interest import TasteArtifacts

        taste = TasteArtifacts.load(bundle_dir / INTEREST_DIR, mmap=False, verify=True)
        if len(taste) != manifest["rows"]:
            raise BundleError(f"self-check: {len(taste)} interest rows, catalog {manifest['rows']}")


# --------------------------------------------------------------------------- #
# Manifest / verification helpers
# --------------------------------------------------------------------------- #
_MANIFEST_KEYS = ("schema_version", "bundle_id", "city", "city_slug", "timezone", "built_at", "rows",
                  "catalog", "content_sha256", "files")


def read_manifest(bundle_dir: Union[str, Path]) -> dict:
    """The parsed manifest.json of a bundle (BundleError when missing / not JSON / wrong schema version)."""
    path = Path(bundle_dir).expanduser() / MANIFEST_FILE
    if not path.is_file():
        raise BundleError(f"not a walk bundle (no {MANIFEST_FILE}): {path.parent}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(f"cannot read {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BundleError(f"{path}: the manifest must be a JSON object")
    version = manifest.get("schema_version")
    if version != BUNDLE_SCHEMA_VERSION:
        raise BundleError(f"{path}: bundle schema_version {version!r}, this package reads {BUNDLE_SCHEMA_VERSION}")
    missing = [k for k in _MANIFEST_KEYS if k not in manifest]
    if missing:
        raise BundleError(f"{path}: manifest is missing {missing}")
    return manifest


_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _unsafe_rel(rel: Any) -> Optional[str]:
    """Why a manifest path is not a plain relative POSIX path inside the bundle (None when it is)."""
    if not isinstance(rel, str) or not rel:
        return "not a non-empty string"
    if "\\" in rel or "\x00" in rel:
        return "backslash or NUL in the path"
    if rel.startswith("/") or _DRIVE_RE.match(rel):
        return "absolute path"
    if any(part in ("", ".", "..") for part in rel.split("/")):
        return "'..', '.' or empty path component"
    return None


def _inside(root: Path, path: Path) -> bool:
    """`path` (symlinks resolved) is `root` or below it; `root` is already resolved."""
    try:
        path.resolve().relative_to(root)
    except (ValueError, OSError, RuntimeError):     # outside, or a symlink loop
        return False
    return True


def layout_problems(bundle_dir: Union[str, Path], manifest: dict) -> list[str]:
    """Structural integrity errors of a bundle (no hashing, so cheap): every listed path is a plain relative
    path that stays inside the bundle (also after resolving symlinks); the catalog parquet and -- with an
    ``interest`` block -- all ``INTEREST_FILES`` are listed in ``manifest.files``; ``interest.dir`` is
    ``interest`` and ``catalog.file`` is ``walk_catalog.parquet``; the catalog file and the interest
    directory themselves do not resolve outside the bundle. [] = sound."""
    root_dir = Path(bundle_dir).expanduser()
    root = root_dir.resolve()
    errors: list[str] = []
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return ["manifest lists no files"]
    for rel, entry in files.items():
        why = _unsafe_rel(rel)
        if why:
            errors.append(f"manifest.files entry {rel!r}: {why}")
        elif not _inside(root, root_dir / rel):
            errors.append(f"{rel}: resolves outside the bundle (symlink)")
        elif not isinstance(entry, dict):
            errors.append(f"{rel}: manifest entry is not an object")
    catalog = manifest.get("catalog")
    if isinstance(catalog, dict) and catalog.get("file", CATALOG_FILE) != CATALOG_FILE:
        errors.append(f"manifest.catalog.file must be {CATALOG_FILE!r}, got {catalog.get('file')!r}")
    required = [CATALOG_FILE]
    interest = manifest.get("interest")
    if interest:
        if not isinstance(interest, dict):
            errors.append("manifest.interest must be an object or null")
        else:
            if interest.get("dir", INTEREST_DIR) != INTEREST_DIR:
                errors.append(f"manifest.interest.dir must be {INTEREST_DIR!r}, got {interest.get('dir')!r}")
            required += [f"{INTEREST_DIR}/{name}" for name in INTEREST_FILES]
    unlisted = [rel for rel in required if rel not in files]
    if unlisted:
        errors.append(f"manifest.files does not list {unlisted} (every file the planner reads must be hashed)")
    for rel in (CATALOG_FILE, INTEREST_DIR):
        p = root_dir / rel
        if (p.exists() or p.is_symlink()) and not _inside(root, p):
            errors.append(f"{rel}: resolves outside the bundle (symlink)")
    return errors


def _file_problems(bundle_dir: Path, manifest: dict, hashes: bool = True) -> tuple[list[str], list[str]]:
    """(errors, warnings) of the files listed in the manifest: the layout rules (``layout_problems``) first --
    a path that is unsafe is never opened -- then missing, size, sha256; unlisted files warn."""
    files = manifest.get("files") or {}
    errors = layout_problems(bundle_dir, manifest)
    if not isinstance(files, dict) or not files:
        return errors, []
    unsafe = {rel for rel in files if _unsafe_rel(rel) or not _inside(Path(bundle_dir).resolve(), bundle_dir / rel)}
    for rel, entry in files.items():
        if rel in unsafe or not isinstance(entry, dict):
            continue
        p = bundle_dir / rel
        if not p.is_file():
            errors.append(f"{rel}: missing")
            continue
        size = p.stat().st_size
        if size != entry.get("bytes"):
            errors.append(f"{rel}: {size} bytes, manifest says {entry.get('bytes')}")
            continue
        if hashes and file_sha256(p) != entry.get("sha256"):
            errors.append(f"{rel}: sha256 mismatch (the file was modified or corrupted)")
    listed = set(files) | {MANIFEST_FILE}
    extra = sorted(p.relative_to(bundle_dir).as_posix() for p in bundle_dir.rglob("*")
                   if p.is_file() and p.relative_to(bundle_dir).as_posix() not in listed
                   and not p.name.startswith("."))
    warnings = [f"files not in the manifest: {extra[:_MAX_EXAMPLES]}" + (" ..." if len(extra) > _MAX_EXAMPLES else "")
                ] if extra else []
    return errors, warnings


# --------------------------------------------------------------------------- #
# Validate
# --------------------------------------------------------------------------- #
class _Check:
    """One named check of a validation report."""

    def __init__(self, name: str):
        self.name = name
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: dict[str, Any] = {}

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def as_dict(self) -> dict:
        return {"ok": not self.errors, "errors": list(self.errors), "warnings": list(self.warnings), **self.info}


def _examples(items: list) -> str:
    return ", ".join(map(str, items[:_MAX_EXAMPLES])) + (" ..." if len(items) > _MAX_EXAMPLES else "")


def _hours_problem(text: str) -> Optional[str]:
    """Why an opening_hours cell is not a valid week-minute interval list, or None."""
    try:
        hours = json.loads(text)
    except ValueError:
        return "not JSON"
    if not isinstance(hours, list):
        return "not a JSON list"
    prev = None
    for iv in hours:
        if not (isinstance(iv, list) and len(iv) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                              for x in iv)):
            return "an interval is not [open, close]"
        o, c = iv
        if not (0 <= o < c <= o + 1440 and o < 10080):
            return f"interval {iv} out of range (0 <= open < close <= open + 1440, open < 10080)"
        if prev is not None and o < prev:
            return "intervals not sorted"
        prev = o
    return None


def _in_city(lat: float, lon: float, bbox: Optional[tuple], median: tuple[float, float]) -> bool:
    if bbox is not None:
        return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]
    return haversine_km(lat, lon, median[0], median[1]) <= MAX_CITY_RADIUS_KM


def validate_bundle(bundle_dir: Union[str, Path], deep: bool = True, *,
                    photos_root: Optional[Union[str, Path]] = None) -> dict:
    """Check a bundle and return a report: ``{"ok", "bundle_dir", "bundle_id", "city", "deep", "errors",
    "warnings", "checks": {name: {"ok", "errors", "warnings", ...}}, "elapsed_s"}``. Never raises for a
    bad bundle (that is what the report says); ``ok`` is False when any check has an error.

    Always: the manifest (schema version, required keys, bundle id format and its sha8), every listed
    file (exists, size, sha256; unlisted files warn), the catalog parquet (required columns, column
    types, row count) and the interest files (shapes, dtypes, place_id row alignment with the catalog).
    deep=True adds the row checks — place_id format / uniqueness, opening_hours JSON + interval sanity,
    business_status values, coordinates finite and inside the city (``CITY_BBOXES``, else within
    ``MAX_CITY_RADIUS_KM`` of the median), photo keys (pattern, own place id, count), theme groups —
    recomputes catalog_sha256 / content_sha256 and the taste fingerprint, and loads the catalog like
    the service. `photos_root` (the ``photos_cid`` directory) adds photo-file existence counts
    (missing files warn)."""
    t0 = time.perf_counter()
    root = Path(bundle_dir).expanduser()
    checks: dict[str, _Check] = {}

    def check(name: str) -> _Check:
        c = checks[name] = _Check(name)
        return c

    report: dict[str, Any] = {"bundle_dir": str(root), "bundle_id": None, "city": None, "deep": bool(deep)}

    def finish() -> dict:
        report["checks"] = {k: c.as_dict() for k, c in checks.items()}
        report["errors"] = [f"{k}: {e}" for k, c in checks.items() for e in c.errors]
        report["warnings"] = [f"{k}: {w}" for k, c in checks.items() for w in c.warnings]
        report["ok"] = not report["errors"]
        report["elapsed_s"] = round(time.perf_counter() - t0, 3)
        return report

    # -- manifest
    c = check("manifest")
    try:
        manifest = read_manifest(root)
    except BundleError as exc:
        c.error(str(exc))
        return finish()
    bid = str(manifest.get("bundle_id"))
    report.update(bundle_id=bid, city=manifest.get("city"), schema_version=manifest.get("schema_version"))
    m = BUNDLE_ID_RE.match(bid)
    if not m:
        c.error(f"bundle_id {bid!r} does not match <city_slug>-<YYYYMMDD>-<sha8>")
    else:
        if m.group("slug") != manifest.get("city_slug"):
            c.error(f"bundle_id prefix {m.group('slug')!r} != city_slug {manifest.get('city_slug')!r}")
        if not str(manifest.get("content_sha256", "")).startswith(m.group("sha8")):
            c.error("bundle_id sha8 is not the start of content_sha256")
    if root.name != bid:
        c.warn(f"directory name {root.name!r} differs from the bundle id {bid!r}")
    c.info.update(built_at=manifest.get("built_at"), rows=manifest.get("rows"))

    # -- files (always hashed: a few tens of MB, well under a second)
    c = check("files")
    errs, warns = _file_problems(root, manifest, hashes=True)
    for e in errs:
        c.error(e)
    for w in warns:
        c.warn(w)
    c.info["files"] = len(manifest.get("files") or {})
    c.info["bytes"] = int(sum((e or {}).get("bytes", 0) for e in (manifest.get("files") or {}).values()
                              if isinstance(e, dict)))
    if layout_problems(root, manifest):       # unsafe layout (reported above): open nothing more
        return finish()

    # -- catalog parquet: schema + rows
    c = check("catalog")
    table = None
    catalog_file = root / CATALOG_FILE
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pq.read_table(catalog_file)
    except Exception as exc:          # unreadable parquet: report, do not raise
        c.error(f"cannot read {CATALOG_FILE}: {type(exc).__name__}: {exc}")
    if table is not None:
        schema = table.schema
        missing = [col for col in REQUIRED_COLUMNS if col not in schema.names]
        if missing:
            c.error(f"missing required columns {missing}")
        unknown = [col for col in schema.names if col not in BUNDLE_COLUMNS]
        if unknown:
            c.warn(f"columns the planner does not know: {unknown}")
        expected = _catalog_schema([n for n in schema.names if n in BUNDLE_COLUMNS])
        for f in expected:
            got = schema.field(f.name).type
            ok = (got == f.type or (pa.types.is_integer(got) and pa.types.is_integer(f.type))
                  or (pa.types.is_large_string(got) and pa.types.is_string(f.type))
                  or (pa.types.is_list(got) and f.name == "photos" and pa.types.is_string(got.value_type)))
            if not ok:
                c.error(f"column {f.name}: type {got}, expected {f.type}")
        if table.num_rows != manifest.get("rows"):
            c.error(f"{table.num_rows} rows, manifest says {manifest.get('rows')}")
        if manifest.get("catalog", {}).get("columns") not in (None, list(schema.names)):
            c.error("columns differ from manifest.catalog.columns")
        c.info.update(rows=table.num_rows, columns=len(schema.names))
    catalog_ids: Optional[list[str]] = (table.column("place_id").to_pylist()
                                        if table is not None and "place_id" in table.column_names else None)

    # -- interest artifacts
    interest = manifest.get("interest")
    report["has_interest"] = bool(interest)
    if interest:
        c = check("interest")
        _check_interest(c, root, manifest, interest, catalog_ids, deep)

    # -- deep row checks + canonical hashes + load
    if deep and table is not None:
        _check_rows(check("rows"), table, manifest)
        c = check("content")
        try:
            cat_sha = catalog_content_sha256(table)
        except Exception as exc:
            c.error(f"cannot hash the catalog rows: {type(exc).__name__}: {exc}")
        else:
            recorded = (manifest.get("catalog") or {}).get("catalog_sha256")
            if cat_sha != recorded:
                c.error(f"catalog_sha256 {cat_sha[:12]}… != manifest {str(recorded)[:12]}…")
            fp = (interest or {}).get("fingerprint") if interest else None
            whole = content_sha256(cat_sha, fp, city=str(manifest.get("city")), timezone=str(manifest.get("timezone")),
                                   schema_version=int(manifest.get("schema_version")))
            if whole != manifest.get("content_sha256"):
                c.error("content_sha256 does not match the recomputed content hash")
            c.info.update(catalog_sha256=cat_sha, content_sha256=whole)
        c = check("load")
        try:
            cat = CityCatalog.from_bundle(root)
        except Exception as exc:
            c.error(f"CityCatalog.from_bundle failed: {type(exc).__name__}: {exc}")
        else:
            c.info.update(rows_routable=len(cat), center=[cat.center[0], cat.center[1]], timezone=cat.timezone,
                          version=cat.version)
            if cat.version != bid:
                c.error(f"catalog version {cat.version!r} != bundle id {bid!r}")

    # -- photo files
    if photos_root is not None and table is not None and "photos" in table.column_names:
        c = check("photo_files")
        try:
            pdir = _photos_dir(photos_root)
        except BundleError as exc:
            c.error(str(exc))
        else:
            keys = table.column("photos").to_pylist()
            n_keys = n_ok = places_ok = 0
            missing_ex: list[str] = []
            for ks in keys:
                found = False
                for k in ks or []:
                    n_keys += 1
                    if _photo_file(pdir, k).is_file():
                        n_ok += 1
                        found = True
                    elif len(missing_ex) < _MAX_EXAMPLES:
                        missing_ex.append(k)
                places_ok += found
            c.info.update(photos_root=str(pdir), keys=n_keys, existing=n_ok, missing=n_keys - n_ok,
                          places_with_photo_file=places_ok)
            if n_keys - n_ok:
                c.warn(f"{n_keys - n_ok} of {n_keys} photo files missing, e.g. {_examples(missing_ex)}")
    return finish()


def _check_interest(c: _Check, root: Path, manifest: dict, interest: dict, catalog_ids: Optional[list],
                    deep: bool) -> None:
    from .interest import (DENSITY_FILE, FEATURES_FILE, HAS_IMAGE_FILE, IMAGE_FILE, META_FILE, TEXT_FILE,
                           TasteArtifacts)

    idir = root / INTEREST_DIR                      # interest.dir other than "interest" is a layout error
    names = (TEXT_FILE, IMAGE_FILE, HAS_IMAGE_FILE, DENSITY_FILE, FEATURES_FILE, META_FILE)
    missing = [n for n in names if not (idir / n).is_file()]
    if missing:
        c.error(f"missing interest files {missing}")
        return
    n = manifest.get("rows")
    try:
        text = np.load(idir / TEXT_FILE, mmap_mode="r", allow_pickle=False)
        image = np.load(idir / IMAGE_FILE, mmap_mode="r", allow_pickle=False)
        has_image = np.load(idir / HAS_IMAGE_FILE, allow_pickle=False)
        density = np.load(idir / DENSITY_FILE, allow_pickle=False)
        feats = pd.read_parquet(idir / FEATURES_FILE, columns=["place_id"])
        meta = json.loads((idir / META_FILE).read_text(encoding="utf-8"))
    except Exception as exc:
        c.error(f"cannot read the interest files: {type(exc).__name__}: {exc}")
        return
    for name, arr, dtype in ((TEXT_FILE, text, np.float16), (IMAGE_FILE, image, np.float16)):
        if arr.ndim != 2 or arr.shape[0] != n:
            c.error(f"{name}: shape {arr.shape}, expected ({n}, d)")
        if arr.dtype != dtype:
            c.error(f"{name}: dtype {arr.dtype}, expected {np.dtype(dtype)}")
    if text.ndim == 2 and text.shape[1] != interest.get("text_dim"):
        c.error(f"{TEXT_FILE}: {text.shape[1]} dims, manifest says {interest.get('text_dim')}")
    for name, arr in ((HAS_IMAGE_FILE, has_image), (DENSITY_FILE, density)):
        if arr.shape != (n,):
            c.error(f"{name}: shape {arr.shape}, expected ({n},)")
    ids = feats["place_id"].astype(str).tolist()
    if catalog_ids is not None and ids != [str(p) for p in catalog_ids]:
        c.error("features.parquet place_id order differs from walk_catalog.parquet (rows not aligned)")
    if meta.get("fingerprint") != interest.get("fingerprint"):
        c.error("interest_meta.json fingerprint != manifest.interest.fingerprint")
    c.info.update(rows=len(ids), text_dim=int(text.shape[1]) if text.ndim == 2 else None,
                  image_dim=int(image.shape[1]) if image.ndim == 2 else None,
                  places_with_text=interest.get("places_with_text"),
                  places_with_image=interest.get("places_with_image"), version=interest.get("version"))
    del text, image
    if deep:
        try:
            taste = TasteArtifacts.load(idir, mmap=True, verify=False, float32_cache=False)
            fp = taste.fingerprint()
        except Exception as exc:
            c.error(f"cannot load the taste artifacts: {type(exc).__name__}: {exc}")
            return
        if fp != interest.get("fingerprint"):
            c.error(f"taste fingerprint {fp[:12]}… != manifest {str(interest.get('fingerprint'))[:12]}…")


def _check_rows(c: _Check, table, manifest: dict) -> None:
    cols = set(table.column_names)
    get = (lambda name: table.column(name).to_pylist() if name in cols else None)
    pids = get("place_id") or []
    bad = [p for p in pids if not isinstance(p, str) or not PLACE_ID_RE.match(p)]
    if bad:
        c.error(f"{len(bad)} place_id values are not decimal CIDs, e.g. {_examples(bad)}")
    seen: set = set()
    dups = [p for p in pids if p in seen or seen.add(p)]
    if dups:
        c.error(f"{len(dups)} duplicate place_id values, e.g. {_examples(dups)}")
    gp = get("google_place_id")
    if gp is not None:
        odd = [p for p, g in zip(pids, gp) if g is not None and not str(g).startswith("ChIJ")]
        if odd:
            c.warn(f"{len(odd)} google_place_id values do not start with ChIJ (navigation pins fall back to "
                   f"coordinates), e.g. {_examples(odd)}")
    hours = get("opening_hours")
    if hours is not None:
        bad_h = []
        for p, h in zip(pids, hours):
            if h is None:
                continue
            why = _hours_problem(h) if isinstance(h, str) else "not a string"
            if why:
                bad_h.append(f"{p} ({why})")
        if bad_h:
            c.error(f"{len(bad_h)} invalid opening_hours values, e.g. {_examples(bad_h)}")
        c.info["opening_hours_known"] = sum(h is not None for h in hours)
    status = get("business_status")
    if status is not None:
        odd = sorted({str(s) for s in status if (s or "") not in KNOWN_STATUSES})
        if odd:
            c.error(f"unknown business_status values {odd} (expected '', closed_forever, temporarily_closed)")
    groups = get("theme_group")
    if groups is not None:
        odd = sorted({str(g) for g in groups if g is not None and g not in KNOWN_THEME_GROUPS})
        if odd:
            c.warn(f"theme_group values the planner has no slots for: {odd}")
    lat, lon = get("latitude") or [], get("longitude") or []

    def located(a, b) -> bool:
        return a is not None and b is not None and math.isfinite(a) and math.isfinite(b)

    finite = [(a, b) for a, b in zip(lat, lon) if located(a, b)]
    if len(finite) < len(pids):
        c.warn(f"{len(pids) - len(finite)} places without coordinates (the planner ignores them)")
    city = str(manifest.get("city"))
    bbox = CITY_BBOXES.get(city)
    if finite:
        med = (float(np.median([a for a, _ in finite])), float(np.median([b for _, b in finite])))
        outside = [p for p, a, b in zip(pids, lat, lon) if located(a, b) and not _in_city(a, b, bbox, med)]
        if outside:
            where = f"bbox {list(bbox)}" if bbox else f"{MAX_CITY_RADIUS_KM:.0f} km of the median {med}"
            c.error(f"{len(outside)} places outside {city} ({where}), e.g. {_examples(outside)}")
        c.info["city_area"] = list(bbox) if bbox else {"median": list(med), "radius_km": MAX_CITY_RADIUS_KM}
    photos, counts = get("photos"), get("photo_count")
    if photos is not None:
        max_n = int((manifest.get("photos") or {}).get("max_per_place") or MAX_BUNDLE_PHOTOS)
        bad_k, wrong_owner, too_many, dup_k, bad_count = [], [], [], [], []
        for i, (p, ks) in enumerate(zip(pids, photos)):
            ks = ks or []
            for k in ks:
                mk = PHOTO_KEY_RE.match(k or "")
                if not mk:
                    bad_k.append(k)
                elif mk.group("cid") != p:
                    wrong_owner.append(f"{p}: {k}")
            if len(ks) > max_n:
                too_many.append(p)
            if len(set(ks)) != len(ks):
                dup_k.append(p)
            if counts is not None and counts[i] != len(ks):
                bad_count.append(p)
        for items, msg in ((bad_k, "photo keys do not match photos_cid/<cid>/<NN>_<vibe|all>.jpg"),
                           (wrong_owner, "photo keys belong to another place"),
                           (too_many, f"places have more than {max_n} photos"),
                           (dup_k, "places list a photo key twice"),
                           (bad_count, "photo_count values differ from the number of keys")):
            if items:
                c.error(f"{len(items)} {msg}, e.g. {_examples(items)}")
        c.info["photo_keys"] = int(sum(len(ks or []) for ks in photos))
    c.info["rows"] = len(pids)


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
@dataclass
class LoadedBundle:
    """A bundle loaded for planning: the catalog (``CityCatalog.from_bundle``) and the taste
    artifacts (``TasteArtifacts.load``, memory-mapped; None when the bundle has none)."""

    dir: Path
    manifest: dict
    catalog: CityCatalog
    taste: Optional["TasteArtifacts"]
    bundle_id: str
    city: str

    @property
    def timezone(self) -> str:
        return str(self.manifest.get("timezone") or self.catalog.timezone)

    @property
    def content_sha256(self) -> str:
        return str(self.manifest.get("content_sha256"))

    @property
    def catalog_sha256(self) -> Optional[str]:
        return (self.manifest.get("catalog") or {}).get("catalog_sha256")

    @property
    def interest_fingerprint(self) -> Optional[str]:
        return (self.manifest.get("interest") or {}).get("fingerprint")

    def __repr__(self) -> str:
        return (f"LoadedBundle(bundle_id={self.bundle_id!r}, city={self.city!r}, rows={len(self.catalog)}, "
                f"taste={'yes' if self.taste is not None else 'no'})")


def load_bundle(bundle_dir: Union[str, Path], *, verify: bool = True) -> LoadedBundle:
    """Load a bundle for planning. Always checks the layout (``layout_problems``: every file the planner
    reads is listed in the manifest, no path leaves the bundle). verify=True (the service's default) also
    checks every listed file's size and sha256 and the interest rows' alignment with the catalog; any
    mismatch raises BundleError. verify=False skips the hashing (e.g. a developer's hot loop)."""
    root = Path(bundle_dir).expanduser()
    manifest = read_manifest(root)
    bid = str(manifest["bundle_id"])
    layout = layout_problems(root, manifest)
    if layout:
        raise BundleError(f"bundle {bid} failed verification ({root}): " + "; ".join(layout[:8])
                          + (" ..." if len(layout) > 8 else ""))
    if verify:
        errors, _warnings = _file_problems(root, manifest, hashes=True)
        if errors:
            raise BundleError(f"bundle {bid} failed verification ({root}): " + "; ".join(errors[:8])
                              + (" ..." if len(errors) > 8 else ""))
    try:
        catalog = CityCatalog.from_bundle(root)
    except Exception as exc:
        raise BundleError(f"cannot load the catalog of bundle {bid} ({root}): {type(exc).__name__}: {exc}") from exc
    if catalog.version != bid:
        raise BundleError(f"bundle {bid}: catalog version {catalog.version!r} differs from the bundle id")
    taste = None
    interest = manifest.get("interest")
    if interest:
        from .interest import TasteArtifacts

        try:
            taste = TasteArtifacts.load(root / INTEREST_DIR, mmap=True, verify=False)
        except Exception as exc:
            raise BundleError(f"cannot load the taste artifacts of bundle {bid}: {type(exc).__name__}: {exc}") from exc
        if len(taste) != manifest["rows"]:
            raise BundleError(f"bundle {bid}: {len(taste)} interest rows, catalog {manifest['rows']}")
        if verify:
            import pyarrow.parquet as pq

            ids = pq.read_table(root / CATALOG_FILE, columns=["place_id"]).column("place_id").to_pylist()
            if [str(p) for p in ids] != list(taste.place_ids):
                raise BundleError(f"bundle {bid}: interest rows are not aligned with the catalog rows")
    return LoadedBundle(dir=root, manifest=manifest, catalog=catalog, taste=taste, bundle_id=bid,
                        city=str(manifest.get("city") or catalog.city))


# --------------------------------------------------------------------------- #
# Locate bundles
# --------------------------------------------------------------------------- #
def resolve_bundle_dirs(spec: Union[str, Path, Iterable[Union[str, Path]]]) -> list[Path]:
    """Bundle directories named by `spec` (``WALK_BUNDLE_DIR`` / ``--bundle``):

      * a bundle directory (it has manifest.json)        -> [it]
      * comma-separated bundle directories ("a,b")       -> each of them (at most one per city)
      * a root directory holding bundle directories      -> the NEWEST bundle of each city: the
        largest ``built_at`` (ISO-8601 UTC, compared as text), ties broken by bundle id; hidden
        directories (in-progress builds ``.<id>.*.tmp``) are skipped.

    Results are in spec order (bundles found under a root: sorted by city). Raises BundleError for
    an empty spec, a path that does not exist, a directory that is neither a bundle nor holds any,
    an unreadable manifest, or two bundles of the same city named explicitly (ambiguous)."""
    if isinstance(spec, (str, Path)):
        parts = [s.strip() for s in str(spec).split(",")] if isinstance(spec, str) else [str(spec)]
    else:
        parts = [str(s).strip() for s in spec]
    parts = [p for p in parts if p]
    if not parts:
        raise BundleError("no bundle directory given (set WALK_BUNDLE_DIR or pass --bundle)")
    out: list[Path] = []
    explicit_city: dict[str, Path] = {}
    for part in parts:
        p = Path(part).expanduser()
        if not p.exists():
            raise BundleError(f"bundle path does not exist: {p}")
        if not p.is_dir():
            raise BundleError(f"bundle path is not a directory: {p}")
        if (p / MANIFEST_FILE).is_file():
            city = str(read_manifest(p).get("city"))
            if city in explicit_city:
                raise BundleError(f"two bundles of {city} given: {explicit_city[city]} and {p}")
            explicit_city[city] = p
            out.append(p)
            continue
        newest: dict[str, tuple[str, str, Path]] = {}
        for child in sorted(p.iterdir()):
            if child.name.startswith(".") or not child.is_dir() or not (child / MANIFEST_FILE).is_file():
                continue
            m = read_manifest(child)
            city = str(m.get("city"))
            key = (str(m.get("built_at") or ""), str(m.get("bundle_id") or child.name), child)
            if city not in newest or key[:2] > newest[city][:2]:
                newest[city] = key
        if not newest:
            raise BundleError(f"no walk bundles in {p} (expected <bundle_id>/{MANIFEST_FILE} directories)")
        for city in sorted(newest):
            if city in explicit_city:
                raise BundleError(f"two bundles of {city} given: {explicit_city[city]} and {newest[city][2]}")
            explicit_city[city] = newest[city][2]
            out.append(newest[city][2])
    return out


def bundle_info(bundle_dir: Union[str, Path]) -> dict:
    """A short summary of a bundle's manifest (id, city, rows, coverage, sizes, interest) — no checks."""
    root = Path(bundle_dir).expanduser()
    m = read_manifest(root)
    files = m.get("files") or {}
    interest = m.get("interest") or None
    return {
        "bundle_dir": str(root), "bundle_id": m.get("bundle_id"), "city": m.get("city"),
        "city_slug": m.get("city_slug"), "timezone": m.get("timezone"), "built_at": m.get("built_at"),
        "schema_version": m.get("schema_version"),
        "builder": m.get("builder"), "rows": m.get("rows"), "rows_by_theme_group": m.get("rows_by_theme_group"),
        "coverage": m.get("coverage"), "photos": m.get("photos"), "bbox": m.get("bbox"),
        "content_sha256": m.get("content_sha256"), "catalog_sha256": (m.get("catalog") or {}).get("catalog_sha256"),
        "interest": None if not interest else {k: interest.get(k) for k in (
            "version", "fingerprint", "text_model", "image_model", "csls_k", "rows", "text_dim", "image_dim",
            "places_with_text", "places_with_image")},
        "files": {rel: e.get("bytes") for rel, e in files.items()},
        "total_bytes": int(sum(e.get("bytes", 0) for e in files.values())),
        "source": {k: (v or {}).get("file") for k, v in (m.get("source") or {}).items()},
    }
