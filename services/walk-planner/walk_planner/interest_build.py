"""Build the per-city taste artifacts (:class:`~walk_planner.interest.TasteArtifacts`) that
personalise the Walk Planner's interest by favourites.

Offline (bundle build) or in-process (dashboard), from the same inputs the v4 feed engine loads:
the city catalog (tags, vibe axes, ratings), the text embeddings (OpenAI, one row per place via the
store's metadata) and the direct-image store (OpenCLIP, keyed by place_id). The heavy per-city work
happens here once — L2 normalisation, the CSLS hubness density of every place (mean text cosine
to its 10 nearest city neighbours; ~1-2 s for 13k places), tag parsing and the quality prior — so
a request only needs a few matrix-vector products (``interest.taste_scores``).

The helpers below are ports of ``recommendation_system/ai_location_recommender/
location_recommender_utils.py`` (``parse_tag_names`` and its parsers, ``compute_quality_score_v2``)
and of the engine's preparation in ``backend_recommender.LocationRecommender._prepare_locations``
(axis fill + ``has_axes``, embedding-row alignment). They are copied, not imported: this package
must not depend on the research code.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .interest import (
    AXIS_COLUMNS,
    AXIS_FILL,
    CSLS_K,
    CSLS_PENALTY,
    QUALITY_SHRINKAGE_PRIOR,
    TASTE_WEIGHTS,
    TasteArtifacts,
)
from .version import INTEREST_VERSION

__all__ = [
    "TEXT_MODEL_DEFAULT", "clean_text", "parse_listish", "parse_ai_tags_json", "parse_tag_names",
    "quality_score_v2", "axis_matrix", "tag_lists", "l2_normalize_rows", "csls_density",
    "build_taste_artifacts", "taste_artifacts_from_files",
]

TEXT_MODEL_DEFAULT = "text-embedding-3-small"      # location_recommender_utils.DEFAULT_EMBEDDING_MODEL
_DENSITY_BLOCK = 1024                               # rows per block of the city x city cosine matrix


# --------------------------------------------------------------------------- #
# Tag parsing (location_recommender_utils: clean_text, parse_listish, parse_ai_tags_json,
# parse_tag_names) — same behaviour, incl. the ast.literal_eval fallbacks
# --------------------------------------------------------------------------- #
def clean_text(value) -> str:
    if value is None or value is pd.NA:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _is_missing(value) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def parse_listish(value) -> list[str]:
    """A list-like cell (python list, its repr, or a ``,``/``;`` separated string) -> clean strings."""
    if _is_missing(value):
        return []
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, (tuple, set)):
        parsed = list(value)
    else:
        raw = str(value).strip()
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            parsed = [part.strip() for part in re.split(r",|;", raw) if part.strip()]
    if isinstance(parsed, dict):
        parsed = [key for key, enabled in parsed.items() if enabled]
    if not isinstance(parsed, list):
        parsed = [parsed]
    return [clean_text(item) for item in parsed if clean_text(item)]


def parse_ai_tags_json(value) -> list[dict]:
    """The catalog's ``ai_tags_json`` cell -> ``[{"tag", "confidence", "polarity"}, ...]``."""
    if _is_missing(value):
        return []
    if isinstance(value, list):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                return []
    if not isinstance(parsed, list):
        return []
    tags = []
    for item in parsed:
        if isinstance(item, str):
            tags.append({"tag": clean_text(item), "confidence": "unknown", "polarity": "neutral"})
        elif isinstance(item, dict):
            tag = clean_text(item.get("tag"))
            if tag:
                tags.append({
                    "tag": tag,
                    "confidence": clean_text(item.get("confidence")).lower() or "unknown",
                    "polarity": clean_text(item.get("polarity")).lower() or "neutral",
                })
    return tags


def parse_tag_names(row: Mapping[str, Any], include_low_confidence: bool = False,
                    include_negative: bool = False) -> list[str]:
    """Sorted unique tag names of a catalog row (``recommendation_tags`` in the engine): the
    ``ai_tags_json`` tags (else the ``ai_tags_csv`` ones), without low-confidence and negative
    tags."""
    tags = parse_ai_tags_json(row.get("ai_tags_json"))
    if not tags:
        tags = [{"tag": tag, "confidence": "unknown", "polarity": "neutral"}
                for tag in parse_listish(row.get("ai_tags_csv"))]
    selected = []
    for item in tags:
        confidence = item.get("confidence", "unknown")
        polarity = item.get("polarity", "neutral")
        if not include_low_confidence and confidence == "low":
            continue
        if not include_negative and polarity == "negative":
            continue
        tag = clean_text(item.get("tag"))
        if tag:
            selected.append(tag)
    return sorted(set(selected))


# --------------------------------------------------------------------------- #
# Numeric features
# --------------------------------------------------------------------------- #
def quality_score_v2(features: Optional[pd.DataFrame], shrinkage_prior: float = QUALITY_SHRINKAGE_PRIOR,
                     n: Optional[int] = None) -> np.ndarray:
    """Bayesian-shrunk rating in [0, 1] (``compute_quality_score_v2``):
    ``(v / (v + m)) * R + (m / (v + m)) * C`` over 5, with R = ``google_rating``, v = review count,
    C = mean rating over ``features`` (the whole catalog), m = 25. 0.5 without ratings."""
    if features is None or "google_rating" not in features.columns:
        return np.full(len(features) if features is not None else int(n or 0), 0.5)
    m = float(shrinkage_prior)
    rating = pd.to_numeric(features["google_rating"], errors="coerce").clip(0, 5)
    catalog_mean = float(rating.mean()) if rating.notna().any() else 0.0
    rating = rating.fillna(catalog_mean)
    if "google_user_rating_count" in features.columns:
        votes = pd.to_numeric(features["google_user_rating_count"], errors="coerce").fillna(0).clip(lower=0)
    else:
        votes = pd.Series(0.0, index=features.index)
    denom = votes + m
    shrunk = (votes / denom) * rating + (m / denom) * catalog_mean
    return np.asarray((shrunk / 5.0).clip(0, 1), dtype=float)


def axis_matrix(features: Optional[pd.DataFrame], n: Optional[int] = None
                ) -> tuple[np.ndarray, np.ndarray, tuple[str, ...]]:
    """``(axes, has_axes, axis_columns)``: the vibe-axis columns present (engine order), missing
    values filled with 50, and whether a row had any axis value (``_prepare_locations``)."""
    rows = len(features) if features is not None else int(n or 0)
    cols = tuple(c for c in AXIS_COLUMNS if features is not None and c in features.columns)
    if not cols:
        return np.zeros((rows, 0)), np.zeros(rows, dtype=bool), ()
    raw = features[list(cols)].apply(pd.to_numeric, errors="coerce")
    has_axes = raw.notna().any(axis=1).to_numpy(dtype=bool)
    return raw.fillna(AXIS_FILL).to_numpy(dtype=float), has_axes, cols


def tag_lists(features: Optional[pd.DataFrame], n: Optional[int] = None) -> list[tuple[str, ...]]:
    """``parse_tag_names`` for every row (from ``ai_tags_json`` / ``ai_tags_csv``; an
    engine-prepared ``recommendation_tags`` column is used when the raw columns are absent)."""
    rows = len(features) if features is not None else int(n or 0)
    if features is None:
        return [()] * rows
    if "ai_tags_json" in features.columns or "ai_tags_csv" in features.columns:
        js = features["ai_tags_json"].tolist() if "ai_tags_json" in features.columns else [None] * rows
        cs = features["ai_tags_csv"].tolist() if "ai_tags_csv" in features.columns else [None] * rows
        return [tuple(parse_tag_names({"ai_tags_json": j, "ai_tags_csv": c})) for j, c in zip(js, cs)]
    if "recommendation_tags" in features.columns:
        return [tuple(sorted(set(t))) if isinstance(t, (list, tuple, set, np.ndarray)) else ()
                for t in features["recommendation_tags"].tolist()]
    return [()] * rows


def l2_normalize_rows(matrix: np.ndarray, present: Optional[np.ndarray] = None
                      ) -> tuple[np.ndarray, np.ndarray]:
    """``(unit rows as float32, present mask)``; rows that are absent, all-zero or non-finite
    become zero rows and ``present=False`` (``common.normalize_matrix`` keeps zero rows too)."""
    mat = np.array(matrix, dtype=np.float32, copy=True)
    if mat.ndim != 2:
        raise ValueError(f"embedding matrix must be 2-D, got {mat.shape}")
    finite = np.isfinite(mat).all(axis=1)
    mat[~finite] = 0.0
    norms = np.linalg.norm(mat, axis=1)
    ok = finite & (norms > 0)
    if present is not None:
        ok &= np.asarray(present, dtype=bool).reshape(-1)
    mat[ok] /= norms[ok, None]
    mat[~ok] = 0.0
    return mat, ok


def csls_density(unit_text: np.ndarray, has_text: Optional[np.ndarray] = None, k: int = CSLS_K,
                 block: int = _DENSITY_BLOCK) -> np.ndarray:
    """CSLS hubness density per row: mean cosine to its ``k`` nearest other rows that have text
    (``item_to_item_rerank.hubness_density`` over the whole city). Rows without text -> 0."""
    unit_text = np.asarray(unit_text, dtype=np.float32)
    n = unit_text.shape[0]
    has = np.ones(n, dtype=bool) if has_text is None else np.asarray(has_text, dtype=bool).reshape(-1)
    out = np.zeros(n, dtype=np.float32)
    idx = np.flatnonzero(has)
    k_eff = min(int(k), len(idx) - 1)
    if k_eff <= 0:
        return out
    sub = np.ascontiguousarray(unit_text[idx])
    for start in range(0, len(idx), block):
        stop = min(start + block, len(idx))
        sims = sub[start:stop] @ sub.T
        sims[np.arange(stop - start), np.arange(start, stop)] = -np.inf     # not its own neighbour
        out[idx[start:stop]] = np.partition(sims, -k_eff, axis=1)[:, -k_eff:].mean(axis=1)
    return out


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def build_taste_artifacts(place_ids: Sequence[str], themes: Sequence[str], text_emb: np.ndarray,
                          image_emb: Optional[np.ndarray] = None, has_image: Optional[np.ndarray] = None,
                          features: Optional[pd.DataFrame] = None, csls_k: int = CSLS_K, *,
                          has_text: Optional[np.ndarray] = None,
                          meta: Optional[Mapping[str, Any]] = None) -> TasteArtifacts:
    """Taste artifacts from raw, row-aligned arrays (row i = ``place_ids[i]``).

    ``text_emb`` (n x d, any float) — zero / non-finite rows (or ``has_text=False``) mean "no
    embedding". ``image_emb`` (n x d2) + ``has_image`` — zeros where there is no photo; None =
    no photo store. ``features`` (row-aligned frame) supplies ``ai_tags_json`` / ``ai_tags_csv``,
    the eight ``axis_*`` columns, ``google_rating`` and ``google_user_rating_count``; the quality
    prior's catalog mean is taken over these rows, so pass the whole city. ``meta`` adds
    provenance (models, sources) to the recorded metadata."""
    pids = [str(p) for p in place_ids]
    n = len(pids)
    themes = list(themes)
    if len(themes) != n:
        raise ValueError(f"themes has {len(themes)} rows, place_ids {n}")
    if features is not None and len(features) != n:
        raise ValueError(f"features has {len(features)} rows, place_ids {n}")
    text_arr = np.asarray(text_emb)
    if text_arr.ndim != 2 or text_arr.shape[0] != n:
        raise ValueError(f"text_emb must be (n={n}, d), got {text_arr.shape}")
    text32, text_ok = l2_normalize_rows(text_arr, has_text)
    density = csls_density(text32, text_ok, k=csls_k)

    image16 = None
    image_ok = np.zeros(n, dtype=bool)
    if image_emb is not None:
        image_arr = np.asarray(image_emb)
        if image_arr.ndim != 2 or image_arr.shape[0] != n:
            raise ValueError(f"image_emb must be (n={n}, d), got {image_arr.shape}")
        if image_arr.shape[1] > 0:
            image32, image_ok = l2_normalize_rows(image_arr, has_image)
            image16 = image32.astype(np.float16)
            del image32

    features = features.reset_index(drop=True) if features is not None else None
    axes, has_axes, axis_cols = axis_matrix(features, n)
    info = {
        "version": INTEREST_VERSION,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "csls_k": int(csls_k),
        "csls_penalty": CSLS_PENALTY,
        "weights": dict(TASTE_WEIGHTS),
        "missing_image_policy": "zero",
        "quality": {"version": "v2", "shrinkage_prior": QUALITY_SHRINKAGE_PRIOR},
        "text_model": TEXT_MODEL_DEFAULT,
        "image_model": None,
    }
    if meta:
        info.update(dict(meta))
    return TasteArtifacts(
        place_ids=pids,
        themes=themes,
        text=text32.astype(np.float16),
        image=image16,
        has_image=image_ok,
        csls_density=density,
        tags=tag_lists(features, n),
        axes=axes,
        has_axes=has_axes,
        quality=quality_score_v2(features, n=n),
        meta=info,
        has_text=text_ok,
        axis_columns=axis_cols,
    )


def taste_artifacts_from_files(catalog_csv: str | Path, text_npy: str | Path, text_meta_csv: str | Path,
                               image_npy: str | Path | None = None, image_meta: str | Path | None = None,
                               city: Optional[str] = None) -> TasteArtifacts:
    """Taste artifacts for a catalog CSV (rows in CSV order; ``city`` keeps the rows whose
    ``city`` column equals it), aligned to the embedding stores the way ``LocationRecommender``
    aligns them: the text store through its metadata (``place_id``, ``embedding_row``,
    ``has_embedding``), the image store through its metadata keyed by ``place_id``
    (``direct_place_embedding_row``, ``has_direct_image_embedding``). Places missing from a store
    get no vector. Raises ``ValueError`` on inconsistent stores (row counts, duplicate ids,
    out-of-range rows). CSVs are parsed with ``float_precision="round_trip"`` like the bundle builder's
    (``bundle.CSV_READ_OPTIONS``): the same floats on every platform."""
    catalog_csv, text_npy, text_meta_csv = Path(catalog_csv), Path(text_npy), Path(text_meta_csv)
    cat = pd.read_csv(catalog_csv, dtype={"place_id": str}, low_memory=False, float_precision="round_trip")
    if "place_id" not in cat.columns:
        raise ValueError(f"{catalog_csv} has no place_id column")
    if city and "city" in cat.columns:
        cat = cat[cat["city"].astype(str) == str(city)]
    cat = cat.reset_index(drop=True)
    pids = cat["place_id"].astype(str)
    n = len(cat)

    # text store: metadata row -> embedding_row (backend_recommender._prepare_locations)
    tmeta = _read_table(text_meta_csv)
    _require(tmeta, {"place_id", "embedding_row", "has_embedding"}, text_meta_csv)
    tmeta["place_id"] = tmeta["place_id"].astype(str)
    _no_duplicates(tmeta, text_meta_csv)
    text_all = np.load(text_npy, mmap_mode="r")
    if len(text_all) != len(tmeta):
        raise ValueError(f"{text_npy}: {len(text_all)} rows, metadata {text_meta_csv} {len(tmeta)}")
    t_rows, t_has = _store_rows(pids, tmeta, "embedding_row", "has_embedding", len(text_all), text_npy)
    text = np.zeros((n, text_all.shape[1]), dtype=np.float32)
    if t_has.any():
        text[t_has] = np.asarray(text_all[t_rows[t_has]], dtype=np.float32)

    # image store (all cities in one file): metadata keyed by place_id
    image = None
    has_image = None
    image_info: dict[str, Any] = {"image_model": None}
    if image_npy is not None and image_meta is not None:
        image_npy, image_meta = Path(image_npy), Path(image_meta)
        imeta = _read_table(image_meta)
        _require(imeta, {"place_id", "direct_place_embedding_row", "has_direct_image_embedding"}, image_meta)
        imeta["place_id"] = imeta["place_id"].astype(str)
        _no_duplicates(imeta, image_meta)
        image_all = np.load(image_npy, mmap_mode="r")
        i_rows, has_image = _store_rows(pids, imeta, "direct_place_embedding_row", "has_direct_image_embedding",
                                        len(image_all), image_npy)
        image = np.zeros((n, image_all.shape[1]), dtype=np.float32)
        if has_image.any():
            image[has_image] = np.asarray(image_all[i_rows[has_image]], dtype=np.float32)
        image_info = {
            "image_model": _single_value(imeta, "run_id") or image_npy.stem,
            "image_model_tag": _single_value(imeta, "model_tag"),
            "image_source": image_npy.name,
        }

    run_id = re.search(r"location_embeddings_(.+)\.npy$", text_npy.name)
    info = {
        "city": city,
        "catalog_source": catalog_csv.name,
        "text_source": text_npy.name,
        "text_run_id": run_id.group(1) if run_id else None,
        **image_info,
    }
    if "theme" in cat.columns:
        themes = cat["theme"]
    elif "theme_group" in cat.columns:
        themes = cat["theme_group"]
    else:
        themes = pd.Series([""] * n)
    return build_taste_artifacts(pids.tolist(), themes.tolist(), text, image, has_image, cat, meta=info)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype={"place_id": str}, low_memory=False, float_precision="round_trip")


def _require(frame: pd.DataFrame, cols: set[str], path) -> None:
    missing = sorted(cols - set(frame.columns))
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")


def _no_duplicates(frame: pd.DataFrame, path) -> None:
    dup = frame["place_id"].duplicated()
    if dup.any():
        raise ValueError(f"{path} has duplicate place_id values: {frame.loc[dup, 'place_id'].head(10).tolist()}")


def _store_rows(pids: pd.Series, meta: pd.DataFrame, row_col: str, has_col: str, n_store: int,
                path) -> tuple[np.ndarray, np.ndarray]:
    """Store row + availability per catalog place (left join on place_id; missing -> no vector)."""
    row_of = dict(zip(meta["place_id"], pd.to_numeric(meta[row_col], errors="coerce").tolist()))
    has_of = {p: bool(v) if pd.notna(v) else False for p, v in zip(meta["place_id"], meta[has_col].tolist())}
    rows = np.array([row_of.get(p, np.nan) for p in pids], dtype=float)
    has = np.array([has_of.get(p, False) for p in pids], dtype=bool) & ~np.isnan(rows)
    rows = np.where(np.isnan(rows), -1, rows).astype(np.int64)
    bad = has & ((rows < 0) | (rows >= n_store))
    if bad.any():
        raise ValueError(f"{path}: invalid store rows for place_ids {pids[bad].head(10).tolist()}")
    return rows, has


def _single_value(frame: pd.DataFrame, col: str) -> Optional[str]:
    if col not in frame.columns:
        return None
    values = sorted({str(v) for v in frame[col].dropna().unique()})
    return ",".join(values) if values else None
