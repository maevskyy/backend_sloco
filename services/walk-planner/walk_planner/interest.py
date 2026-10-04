"""Interest of every city place for the Walk Planner: cold-start popularity, optionally re-ordered
by the user's taste (favourites / want-to-go places).

``interest`` in [0, 1] is what the route solver trades against walking minutes
(``core.STYLE_PRESETS[...]["interest_weight"]``), what gates the optional "on the way" stops
(``core.EXTRA_MIN_INTEREST``) and what ranks the per-slot candidate pools. Two parts:

* **Cold start** (:func:`cold_start`) — the dashboard's popularity proxy, byte-identical:
  ``0.7 * log1p(reviews) / log1p(max reviews in the city) + 0.3 * clip(rating - 4, 0, 1)``
  (rating = ``bayesian_rating``, else ``google_rating``; unknown rating -> 0.3).
* **Taste** (:func:`taste_scores`) — a numpy port of the v4 feed engine's per-profile blend
  (``LocationRecommender._score_candidates_for_profile`` with ``TEXT_DIRECT_WEIGHTS`` and the
  missing-photo policy "zero"): text-embedding similarity to the profile centroid minus a CSLS
  hubness penalty, OpenCLIP photo similarity, tag Jaccard, vibe-axis and price-axis closeness and
  the Bayesian rating; the two similarity channels are calibrated to percentiles over the city.
  Seeds are split into taste profiles exactly like the engine (agglomerative cosine clustering;
  one profile when scikit-learn is not installed) and a place's taste is its best profile's score.
  The CSLS density is precomputed per city (``TasteArtifacts.csls_density``), which is what makes
  a call take tens of milliseconds instead of seconds.

:func:`interest_map` combines them with a **quantile-preserving rank blend**: within each catalog
theme, places are re-ordered by ``(1 - strength) * pct(cold) + strength * pct(taste)`` and take the
theme's cold-start values in that order. The set of interest values per theme therefore stays the
cold-start set, so the planner's tuned constants keep their meaning; taste only decides which place
gets which value. Seeds keep their cold value; no favourites (or ``strength == 0``) is the cold
start exactly. The per-city inputs live in :class:`TasteArtifacts` (built by
``interest_build.py``, stored under ``<bundle>/interest/``).

numpy + pandas (+ pyarrow to save/load); scikit-learn is optional (multi-profile clustering only).
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .version import INTEREST_VERSION

__all__ = [
    "TASTE_WEIGHTS", "CSLS_K", "CSLS_PENALTY", "FAVOURITE_WEIGHT", "WANT_TO_GO_WEIGHT", "DEFAULT_STRENGTH",
    "MAX_SEEDS", "MAX_PROFILE_CLUSTERS", "MIN_PROFILE_SILHOUETTE", "MIN_PROFILE_CLUSTER_SIZE",
    "MIN_SEEDS_TO_CLUSTER", "AXIS_COLUMNS", "PRICE_AXIS", "AXIS_FILL", "QUALITY_SHRINKAGE_PRIOR",
    "MODE_FAVOURITES", "MODE_POPULARITY", "TEXT_FILE", "IMAGE_FILE", "HAS_IMAGE_FILE", "DENSITY_FILE",
    "FEATURES_FILE", "META_FILE", "ARTIFACT_FILES", "COLD_START_COLUMNS",
    "cold_start_available", "cold_start", "cold_start_map", "TasteArtifacts", "TasteScores", "resolve_seeds",
    "cluster_profiles", "taste_scores", "InterestResult", "interest_map", "warmup",
]

# --------------------------------------------------------------------------- #
# Constants (= the v4 engine defaults the dashboard ran with)
# --------------------------------------------------------------------------- #
# Channel weights = backend_recommender.TEXT_DIRECT_WEIGHTS (semantic_similarity, direct_image_similarity,
# tag_overlap, axis_similarity, quality_score, price_match). The visual-text / subtype / category
# channels have weight 0 there and are not ported.
TASTE_WEIGHTS: dict[str, float] = {
    "text": 0.26, "image": 0.50, "tag": 0.08, "axis": 0.06, "quality": 0.06, "price": 0.04,
}
_CHANNELS = ("text", "image", "tag", "axis", "quality", "price")   # the engine's summation order
CSLS_K = 10                     # hubness_k: neighbours in the CSLS density
CSLS_PENALTY = 0.5              # hubness_penalty: similarity -= penalty * density
FAVOURITE_WEIGHT = 1.0          # RecommenderConfig.favorites_weight
WANT_TO_GO_WEIGHT = 0.55        # RecommenderConfig.want_to_go_weight (a place in both lists is a favourite)
DEFAULT_STRENGTH = 0.5          # beta of the rank blend: 0 = cold start, 1 = order by taste only
MAX_SEEDS = 200                 # valid seeds beyond this many are ignored (favourites are kept first)
# Profile clustering (RecommenderConfig): fewer than 4 seeds -> one profile; else
# K <= min(6, n // 3, n - 1, floor(log2 n)), every cluster >= 2 seeds, and a split only when the
# best silhouette clears 0.10.
MAX_PROFILE_CLUSTERS = 6
MIN_PROFILE_SILHOUETTE = 0.10
MIN_PROFILE_CLUSTER_SIZE = 2
MIN_SEEDS_TO_CLUSTER = 4
# Vibe axes in the engine's order (location_recommender_utils.AXIS_DEFINITIONS); 0..100, missing -> 50.
AXIS_COLUMNS = (
    "axis_quiet_lively", "axis_work_social", "axis_day_night", "axis_casual_premium",
    "axis_drinks_food", "axis_local_tourist", "axis_cheap_expensive", "axis_traditional_experimental",
)
PRICE_AXIS = "axis_cheap_expensive"
AXIS_FILL = 50.0
QUALITY_SHRINKAGE_PRIOR = 25.0  # quality v2: Bayesian-shrunk rating, m = 25
MODE_FAVOURITES = "favourites"
MODE_POPULARITY = "popularity"

# Files of a saved TasteArtifacts directory (``<bundle>/interest/``).
TEXT_FILE = "text_f16.npy"
IMAGE_FILE = "image_f16.npy"
HAS_IMAGE_FILE = "has_image.npy"
DENSITY_FILE = "csls_density.npy"
FEATURES_FILE = "features.parquet"
META_FILE = "interest_meta.json"
ARTIFACT_FILES = (TEXT_FILE, IMAGE_FILE, HAS_IMAGE_FILE, DENSITY_FILE, FEATURES_FILE, META_FILE)
_CHUNK_ROWS = 4096              # float16 rows up-cast to float32 per matrix-product block


# --------------------------------------------------------------------------- #
# Cold start (dashboard_app._walk_interest_map without seeds) — the ONLY implementation of the
# formula: catalog.CityCatalog.cold_interest() / cold_start_scores() delegate here.
# --------------------------------------------------------------------------- #
# The popularity columns the cold start reads, in order of preference (review count, then the
# min-max fallbacks); a catalog with none of them has no cold-start signal.
COLD_START_COLUMNS = ("google_user_rating_count", "map_visibility_score", "google_rating")


def cold_start_available(rows: pd.DataFrame) -> bool:
    """Whether ``rows`` carry any cold-start signal (one of ``COLD_START_COLUMNS``). Without one,
    :func:`cold_start` is all zeros and the dashboard's interest map was empty."""
    return any(c in rows.columns for c in COLD_START_COLUMNS)


def cold_start(rows: pd.DataFrame) -> np.ndarray:
    """Cold-start interest per row of ``rows`` (float64, aligned with the rows) — exactly the
    dashboard's popularity proxy, normalised over ``rows`` (pass the whole city, closed places
    included). Without ``google_user_rating_count`` it falls back to a min-max of
    ``map_visibility_score`` / ``google_rating`` (NaN -> 0), and to zeros without either."""
    if "google_user_rating_count" in rows.columns:
        # How famous + how good. The catalog's percentile scores squash the top (a national museum
        # with 21k reviews and a parish church with 1.5k both ~0.97); log reviews keep them apart.
        n = pd.to_numeric(rows["google_user_rating_count"], errors="coerce").fillna(0.0).clip(lower=0)
        fame = np.log1p(n) / max(float(np.log1p(n.max())), 1e-9)
        rating_col = "bayesian_rating" if "bayesian_rating" in rows.columns else "google_rating"
        rating = pd.to_numeric(rows[rating_col], errors="coerce") if rating_col in rows.columns \
            else pd.Series(np.nan, index=rows.index)
        quality = (rating - 4.0).clip(0.0, 1.0).fillna(0.3)
        score = 0.7 * fame + 0.3 * quality
        return np.asarray(score, dtype=float)
    for col in COLD_START_COLUMNS[1:]:             # map_visibility_score, then google_rating
        if col in rows.columns:
            v = pd.to_numeric(rows[col], errors="coerce")
            lo, span = float(v.min()), (float(v.max()) - float(v.min())) or 1.0
            return np.array([((float(x) - lo) / span if pd.notna(x) else 0.0) for x in v], dtype=float)
    return np.zeros(len(rows), dtype=float)


def cold_start_map(rows: pd.DataFrame) -> dict[str, float]:
    """``{str(place_id): cold-start interest}`` for ``rows`` (the dashboard's dict form)."""
    return {str(pid): float(x) for pid, x in zip(rows["place_id"], cold_start(rows))}


# --------------------------------------------------------------------------- #
# Per-city taste artifacts
# --------------------------------------------------------------------------- #
@dataclass(eq=False)
class TasteArtifacts:
    """Precomputed per-city inputs of the taste model, one row per catalog place.

    ``text`` / ``image`` are L2-normalised float16 (zero rows = no embedding / no photo);
    ``csls_density`` is each place's mean text cosine to its ``CSLS_K`` nearest city neighbours;
    ``tags`` the place's tag names (``parse_tag_names``); ``axes`` the vibe axes named in
    ``axis_columns`` (missing values filled with 50, the engine's convention) and ``has_axes``
    whether the place had any; ``quality`` is quality v2. ``meta`` records versions / models /
    sources. Built by ``interest_build.build_taste_artifacts``; :meth:`save` / :meth:`load`
    persist it. Treat as read-only (lookup caches are built lazily, thread-safely).

    ``float32_cache`` (default on): the first request makes float32 working copies of ``text`` /
    ``image`` and keeps them, so a request is a few BLAS products on any numpy (+4 bytes per
    value, ~106 MB for Bucharest's 12,961 places). Off: the float16 (possibly memory-mapped)
    matrices are up-cast block by block on every request — less memory, slower where numpy's
    float16 casts are not vectorised (numpy 1.x: ~50 ms more per request for Bucharest).
    """

    place_ids: list[str]
    themes: list[str]
    text: np.ndarray
    image: Optional[np.ndarray]
    has_image: np.ndarray
    csls_density: np.ndarray
    tags: list[tuple[str, ...]]
    axes: np.ndarray
    has_axes: np.ndarray
    quality: np.ndarray
    meta: dict = field(default_factory=dict)
    has_text: Optional[np.ndarray] = None
    axis_columns: tuple[str, ...] = AXIS_COLUMNS
    float32_cache: bool = True
    _cache: dict = field(default_factory=dict, init=False, repr=False)
    _lock: Any = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.place_ids = [str(p) for p in self.place_ids]
        n = len(self.place_ids)
        self.themes = [_theme_str(t) for t in self.themes]
        if len(set(self.place_ids)) != n:
            seen: set[str] = set()
            dup = [p for p in self.place_ids if p in seen or seen.add(p)]
            raise ValueError(f"duplicate place_ids in taste artifacts: {dup[:10]}")
        if getattr(self.text, "ndim", 0) != 2 or self.text.shape[0] != n:
            raise ValueError(f"text must be (n={n}, d), got {getattr(self.text, 'shape', None)}")
        if self.image is not None and (self.image.ndim != 2 or self.image.shape[0] != n):
            raise ValueError(f"image must be (n={n}, d), got {self.image.shape}")
        if self.image is not None and self.image.shape[1] == 0:
            self.image = None
        self.has_image = np.asarray(self.has_image, dtype=bool).reshape(-1)
        if self.image is None:
            self.has_image = np.zeros(n, dtype=bool)
        self.csls_density = np.asarray(self.csls_density, dtype=np.float32).reshape(-1)
        self.tags = [tuple(str(t) for t in row) if row is not None else () for row in self.tags]
        self.axis_columns = tuple(str(c) for c in self.axis_columns)
        self.axes = np.asarray(self.axes, dtype=float).reshape(n, len(self.axis_columns))
        self.has_axes = np.asarray(self.has_axes, dtype=bool).reshape(-1)
        self.quality = np.asarray(self.quality, dtype=float).reshape(-1)
        if self.has_text is None:
            self.has_text = np.any(np.asarray(self.text) != 0, axis=1)
        self.has_text = np.asarray(self.has_text, dtype=bool).reshape(-1)
        for name in ("themes", "has_image", "csls_density", "tags", "has_axes", "quality", "has_text"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"{name} has {len(getattr(self, name))} rows, place_ids {n}")

    def __getstate__(self) -> dict:          # picklable: drop the lock and the lookup caches
        state = dict(self.__dict__)
        state.pop("_lock", None)
        state["_cache"] = {}
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()

    # ---- lookups -------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self.place_ids)

    @property
    def index(self) -> dict[str, int]:
        """``place_id -> row``."""
        idx = self._cache.get("index")
        if idx is None:
            with self._lock:
                idx = self._cache.get("index")
                if idx is None:
                    idx = {p: i for i, p in enumerate(self.place_ids)}
                    self._cache["index"] = idx
        return idx

    def row_of(self, place_id: str) -> Optional[int]:
        """Row of ``place_id``, or None when these artifacts don't know it."""
        return self.index.get(str(place_id))

    def _tag_index(self) -> tuple[dict[str, int], np.ndarray, np.ndarray, np.ndarray]:
        """Vectorised tag sets: (vocabulary, flat tag ids, owner row per flat entry, tags per row)."""
        cached = self._cache.get("tags")
        if cached is None:
            with self._lock:
                cached = self._cache.get("tags")
                if cached is None:
                    vocab: dict[str, int] = {}
                    flat: list[int] = []
                    counts = np.zeros(len(self.tags), dtype=np.int64)
                    for i, row in enumerate(self.tags):
                        uniq = sorted(set(row))
                        counts[i] = len(uniq)
                        flat.extend(vocab.setdefault(t, len(vocab)) for t in uniq)
                    owner = np.repeat(np.arange(len(self.tags), dtype=np.int64), counts)
                    cached = (vocab, np.asarray(flat, dtype=np.int64), owner, counts)
                    self._cache["tags"] = cached
        return cached

    def matrix(self, name: str) -> np.ndarray:
        """``text`` / ``image`` for computing: the cached float32 copy when ``float32_cache`` is
        on (built on first use), else the stored array (float16, up-cast per block)."""
        arr = getattr(self, name)
        if arr is None or arr.dtype == np.float32 or not self.float32_cache:
            return arr
        key = f"{name}_f32"
        cached = self._cache.get(key)
        if cached is None:
            with self._lock:
                cached = self._cache.get(key)
                if cached is None:
                    cached = np.ascontiguousarray(arr, dtype=np.float32)
                    self._cache[key] = cached
        return cached

    def align(self, place_ids: Sequence[str]) -> "TasteArtifacts":
        """These artifacts re-ordered / subset to ``place_ids`` (e.g. a bundle's catalog rows).
        Unknown ids get empty rows (no text, photo, tags or axes), so they are never scored.
        Density and quality keep the values computed over the original city catalog."""
        pids = [str(p) for p in place_ids]
        rows = np.array([self.index.get(p, -1) for p in pids], dtype=np.int64)
        ok = rows >= 0
        src = np.where(ok, rows, 0)

        def take(arr: np.ndarray, fill) -> np.ndarray:
            out = np.asarray(arr)[src]            # fancy indexing copies
            out[~ok] = fill
            return out

        return TasteArtifacts(
            place_ids=pids,
            themes=[self.themes[r] if r >= 0 else "" for r in rows.tolist()],
            text=take(self.text, 0),
            image=take(self.image, 0) if self.image is not None else None,
            has_image=take(self.has_image, False),
            csls_density=take(self.csls_density, 0.0),
            tags=[self.tags[r] if r >= 0 else () for r in rows.tolist()],
            axes=take(self.axes, AXIS_FILL),
            has_axes=take(self.has_axes, False),
            quality=take(self.quality, 0.0),
            meta=dict(self.meta),
            has_text=take(self.has_text, False),
            axis_columns=self.axis_columns,
            float32_cache=self.float32_cache,
        )

    # ---- identity / persistence ----------------------------------------- #
    def fingerprint(self) -> str:
        """sha256 (hex) of the content — ids, themes, every array, the tags; not ``meta``."""
        fp = self._cache.get("fingerprint")
        if fp is None:
            h = hashlib.sha256()
            h.update(json.dumps({"rows": len(self), "axis_columns": list(self.axis_columns)}).encode())
            for strings in (self.place_ids, self.themes):
                h.update("\n".join(strings).encode("utf-8"))
                h.update(b"\x00")
            arrays = [np.asarray(self.text, dtype=np.float16), self.has_text, self.has_image,
                      self.csls_density, self.axes, self.has_axes, self.quality]
            if self.image is not None:
                arrays.append(np.asarray(self.image, dtype=np.float16))
            for arr in arrays:
                h.update(f"{arr.dtype.str}{arr.shape}".encode())
                for start in range(0, len(arr), 8192):           # blocks: memory-map friendly
                    h.update(np.ascontiguousarray(arr[start:start + 8192]).tobytes())
            h.update(json.dumps([list(t) for t in self.tags], ensure_ascii=False).encode("utf-8"))
            fp = h.hexdigest()
            self._cache["fingerprint"] = fp
        return fp

    def save(self, directory: str | Path) -> dict[str, Path]:
        """Write the artifacts under ``directory`` (created if needed): text_f16.npy,
        image_f16.npy (shape (n, 0) without a photo store), has_image.npy, csls_density.npy,
        features.parquet (place_id, theme, has_text, has_axes, quality, axes, tags) and
        interest_meta.json. Returns ``{file name: path}``."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        n = len(self)
        paths = {name: out / name for name in ARTIFACT_FILES}
        np.save(paths[TEXT_FILE], np.ascontiguousarray(self.text, dtype=np.float16), allow_pickle=False)
        image = self.image if self.image is not None else np.zeros((n, 0), dtype=np.float16)
        np.save(paths[IMAGE_FILE], np.ascontiguousarray(image, dtype=np.float16), allow_pickle=False)
        np.save(paths[HAS_IMAGE_FILE], np.asarray(self.has_image, dtype=bool), allow_pickle=False)
        np.save(paths[DENSITY_FILE], np.asarray(self.csls_density, dtype=np.float32), allow_pickle=False)
        columns = {
            "place_id": pa.array(self.place_ids, type=pa.string()),
            "theme": pa.array(self.themes, type=pa.string()),
            "has_text": pa.array(np.asarray(self.has_text, dtype=bool)),
            "has_axes": pa.array(np.asarray(self.has_axes, dtype=bool)),
            "quality": pa.array(np.asarray(self.quality, dtype=float)),
        }
        for j, col in enumerate(self.axis_columns):
            columns[col] = pa.array(np.asarray(self.axes[:, j], dtype=float))
        columns["tags"] = pa.array([list(t) for t in self.tags], type=pa.list_(pa.string()))
        pq.write_table(pa.table(columns), paths[FEATURES_FILE], compression="zstd")
        meta = dict(self.meta)
        meta.update({
            "version": meta.get("version", INTEREST_VERSION),
            "rows": n,
            "text_dim": int(self.text.shape[1]),
            "image_dim": int(self.image.shape[1]) if self.image is not None else 0,
            "places_with_text": int(np.count_nonzero(self.has_text)),
            "places_with_image": int(np.count_nonzero(self.has_image)),
            "axis_columns": list(self.axis_columns),
            "fingerprint": self.fingerprint(),
            "files": [name for name in ARTIFACT_FILES if name != META_FILE],
        })
        paths[META_FILE].write_text(json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                                    encoding="utf-8")
        return paths

    @classmethod
    def load(cls, directory: str | Path, *, mmap: bool = True, verify: bool = False,
             float32_cache: bool = True) -> "TasteArtifacts":
        """Read artifacts written by :meth:`save`. The float16 matrices are memory-mapped
        read-only unless ``mmap=False``; ``verify=True`` recomputes the fingerprint and raises
        ``ValueError`` when it differs from the one recorded in ``interest_meta.json``;
        ``float32_cache``: see the class docstring."""
        src = Path(directory)
        meta = json.loads((src / META_FILE).read_text(encoding="utf-8"))
        mode = "r" if mmap else None
        text = np.load(src / TEXT_FILE, mmap_mode=mode, allow_pickle=False)
        image = np.load(src / IMAGE_FILE, mmap_mode=mode, allow_pickle=False)
        has_image = np.load(src / HAS_IMAGE_FILE, allow_pickle=False)
        density = np.load(src / DENSITY_FILE, allow_pickle=False)
        feats = pd.read_parquet(src / FEATURES_FILE)
        axis_columns = tuple(meta.get("axis_columns") or [c for c in AXIS_COLUMNS if c in feats.columns])
        n = len(feats)
        if text.shape[0] != n or image.shape[0] != n or len(has_image) != n or len(density) != n:
            raise ValueError(f"taste artifacts in {src} are not row-aligned "
                             f"(features {n}, text {text.shape}, image {image.shape})")
        art = cls(
            place_ids=feats["place_id"].astype(str).tolist(),
            themes=feats["theme"].tolist(),
            text=text,
            image=image if image.shape[1] > 0 else None,
            has_image=has_image,
            csls_density=density,
            tags=feats["tags"].tolist(),
            axes=feats[list(axis_columns)].to_numpy(dtype=float) if axis_columns else np.zeros((n, 0)),
            has_axes=feats["has_axes"].to_numpy(dtype=bool),
            quality=feats["quality"].to_numpy(dtype=float),
            meta=meta,
            has_text=feats["has_text"].to_numpy(dtype=bool),
            axis_columns=axis_columns,
            float32_cache=float32_cache,
        )
        if verify and meta.get("fingerprint") and art.fingerprint() != meta["fingerprint"]:
            raise ValueError(f"taste artifacts in {src} do not match their recorded fingerprint")
        return art


# --------------------------------------------------------------------------- #
# Taste (numpy port of the engine's per-profile blend)
# --------------------------------------------------------------------------- #
@dataclass
class TasteScores:
    """Taste of the city's places for one seed set (rows = ``TasteArtifacts`` rows).

    ``score``: the best profile's blended score, NaN outside ``pool``; ``profile``: that profile's
    index (-1 outside the pool); ``similar``: row of the seed of that profile whose text is the
    most similar (-1 outside the pool); ``profiles``: seed rows per profile; ``used`` /
    ``ignored``: see :func:`resolve_seeds`."""

    score: np.ndarray
    profile: np.ndarray
    similar: np.ndarray
    profiles: list[np.ndarray]
    seed_rows: np.ndarray
    seed_weights: np.ndarray
    used: list[str]
    ignored: list[str]
    pool: np.ndarray


def resolve_seeds(taste: TasteArtifacts, favourite_ids: Iterable[str] = (),
                  want_to_go_ids: Iterable[str] = (), max_seeds: int = MAX_SEEDS
                  ) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """``(seed rows, signal weights, used ids, ignored ids)``.

    Valid seed = known to ``taste`` and with a text embedding; weight 1.0 for a favourite, 0.55
    for a want-to-go place (a place in both lists is a favourite). At most ``max_seeds`` are used:
    favourites first, each list in its given order; ``used`` lists them in that order, ``ignored``
    the rest (unknown, no embedding, over the cap). Rows come in the engine's seed order
    (want-to-go list first), which only matters for float summation and clustering ties."""
    favs = _dedupe(favourite_ids)
    fav_set = set(favs)
    wtg = _dedupe(want_to_go_ids)
    wtg_set = set(wtg)
    index = taste.index
    used: list[str] = []
    ignored: list[str] = []
    for pid in favs + [p for p in wtg if p not in fav_set]:
        row = index.get(pid)
        if row is None or not taste.has_text[row] or len(used) >= max_seeds:
            ignored.append(pid)
        else:
            used.append(pid)
    used_set = set(used)
    # backend_recommender._build_seed_dataframe: dict insertion order = want_to_go, then favourites
    order = [p for p in wtg if p in used_set] + [p for p in favs if p in used_set and p not in wtg_set]
    rows = np.array([index[p] for p in order], dtype=np.int64)
    weights = np.array([FAVOURITE_WEIGHT if p in fav_set else WANT_TO_GO_WEIGHT for p in order], dtype=float)
    return rows, weights, used, ignored


def cluster_profiles(vectors: np.ndarray) -> np.ndarray:
    """Profile label per seed (``backend_recommender._cluster_seed_places``): fewer than 4 seeds ->
    one profile; else agglomerative clustering (cosine, average linkage) with
    K <= min(6, n // 3, n - 1, floor(log2 n)), clusters of >= 2 seeds, and a split only when the
    best silhouette is >= 0.10. Without scikit-learn every seed is in one profile.

    The engine passes ``metric="cosine"``, which makes scipy recompute the pairwise distances
    without BLAS for every K tried (~40 ms per fit at 200 seeds); here they are computed once
    (float64) and passed precomputed — the same average-linkage tree."""
    n = len(vectors)
    zeros = np.zeros(n, dtype=int)
    if n < MIN_SEEDS_TO_CLUSTER:
        return zeros
    try:
        from sklearn.metrics import silhouette_score
    except ImportError:            # optional dependency: degrade to a single profile
        return zeros
    log2_cap = max(1, int(math.floor(math.log2(n))))
    max_k = min(MAX_PROFILE_CLUSTERS, n // 3, n - 1, log2_cap)
    if max_k < 2:
        return zeros
    vectors = np.asarray(vectors, dtype=np.float32)
    dist = _cosine_distances(vectors)
    scores = []
    for k in range(2, max_k + 1):
        labels = _agglomerative_average(k).fit_predict(dist)
        if len(set(labels)) < 2:
            continue
        # reject splits that isolate a too-small (e.g. singleton outlier) cluster
        if int(np.bincount(labels).min()) < MIN_PROFILE_CLUSTER_SIZE:
            continue
        scores.append((k, float(silhouette_score(vectors, labels, metric="cosine"))))
    if not scores:
        return zeros
    best_k, best_score = max(scores, key=lambda item: item[1])
    if best_score < MIN_PROFILE_SILHOUETTE:
        return zeros
    return np.asarray(_agglomerative_average(best_k).fit_predict(dist), dtype=int)


def _cosine_distances(vectors: np.ndarray) -> np.ndarray:
    """Square cosine-distance matrix (float64, clipped to [0, 2] like scipy's ``cosine``)."""
    x = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(x, axis=1)
    x = x / np.where(norms > 0, norms, 1.0)[:, None]
    dist = np.clip(1.0 - x @ x.T, 0.0, 2.0)
    np.fill_diagonal(dist, 0.0)
    return dist


def _agglomerative_average(n_clusters: int):
    from sklearn.cluster import AgglomerativeClustering

    try:
        return AgglomerativeClustering(n_clusters=n_clusters, metric="precomputed", linkage="average")
    except TypeError:              # scikit-learn < 1.2
        return AgglomerativeClustering(n_clusters=n_clusters, affinity="precomputed", linkage="average")


def taste_scores(taste: TasteArtifacts, favourite_ids: Iterable[str] = (),
                 want_to_go_ids: Iterable[str] = (), *, pool: Optional[np.ndarray] = None,
                 density: Optional[np.ndarray] = None, weights: Optional[Mapping[str, float]] = None,
                 max_seeds: int = MAX_SEEDS) -> Optional[TasteScores]:
    """Taste score of the city's places for these seeds, or None when no seed is valid.

    ``pool`` (bool per artifact row) = the candidates the percentiles are computed over; default:
    every place with a text embedding. Seeds are always removed from it. (The engine also drops
    ``ai_confidence == "low"`` places — pass that pool to compare with it.) ``density`` overrides
    the precomputed CSLS density (e.g. one computed over ``pool`` only, as the engine does per
    request); ``weights`` overrides entries of :data:`TASTE_WEIGHTS`."""
    rows, sig_w, used, ignored = resolve_seeds(taste, favourite_ids, want_to_go_ids, max_seeds)
    if len(rows) == 0:
        return None
    return _taste_scores(taste, rows, sig_w, used, ignored, pool, density, weights)


def _taste_scores(taste: TasteArtifacts, rows: np.ndarray, sig_w: np.ndarray, used: list[str],
                  ignored: list[str], pool: Optional[np.ndarray], density: Optional[np.ndarray],
                  weights: Optional[Mapping[str, float]]) -> TasteScores:
    n = len(taste)
    if pool is None:
        pool = taste.has_text.copy()
    else:
        pool = np.asarray(pool, dtype=bool).reshape(-1)
        if len(pool) != n:
            raise ValueError(f"pool has {len(pool)} rows, artifacts {n}")
        pool = pool & taste.has_text
    pool[rows] = False                                    # seeds are never candidates
    dens = taste.csls_density if density is None else np.asarray(density, dtype=np.float32).reshape(-1)
    w = dict(TASTE_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items()})

    seed_text = _rows_f32(taste.text, rows)
    labels = cluster_profiles(seed_text)
    groups = [np.flatnonzero(labels == lab) for lab in np.unique(labels)]   # positions within `rows`
    profiles = [rows[g] for g in groups]

    # One pass over the text matrix: every profile centroid + every seed (for `similar`).
    centroids = [_centroid(seed_text[g], sig_w[g]) for g in groups]
    text_sims = _matvecs(taste.matrix("text"), centroids + list(seed_text))  # (n, K + n_seeds)
    image_sims = None
    image_on = [False] * len(groups)
    if taste.image is not None and w.get("image", 0.0) > 0:
        img_centroids = []
        for k, g in enumerate(groups):
            with_img = g[taste.has_image[rows[g]]]
            if len(with_img):
                image_on[k] = True
                img_centroids.append(_centroid(_rows_f32(taste.image, rows[with_img]), sig_w[with_img]))
            else:
                img_centroids.append(np.zeros(taste.image.shape[1], dtype=np.float32))
        if any(image_on):
            image_sims = _matvecs(taste.matrix("image"), img_centroids)     # (n, K)

    pool_idx = np.flatnonzero(pool)
    best = np.full(n, -np.inf)
    best_profile = np.full(n, -1, dtype=np.int64)
    if len(pool_idx):
        for k, g in enumerate(groups):
            s = _profile_score(taste, rows[g], sig_w[g], pool_idx, text_sims[pool_idx, k],
                               image_sims[pool_idx, k] if image_on[k] else None, dens, w)
            better = s > best[pool_idx]                # first (lowest-id) profile wins ties
            best[pool_idx[better]] = s[better]
            best_profile[pool_idx[better]] = k
    # the most similar seed (text cosine) of the place's best profile
    similar = np.full(n, -1, dtype=np.int64)
    if len(pool_idx):
        owner = np.empty(len(rows), dtype=np.int64)       # profile index of each seed
        for k, g in enumerate(groups):
            owner[g] = k
        masked = np.where(owner[None, :] == best_profile[pool_idx][:, None],
                          text_sims[pool_idx, len(groups):], -np.inf)
        similar[pool_idx] = rows[np.argmax(masked, axis=1)]
    score = np.where(pool, best, np.nan)
    return TasteScores(score=score, profile=best_profile, similar=similar, profiles=profiles, seed_rows=rows,
                       seed_weights=sig_w, used=used, ignored=ignored, pool=pool)


def _profile_score(taste: TasteArtifacts, prow: np.ndarray, pw: np.ndarray, pool_idx: np.ndarray,
                   text_sim: np.ndarray, image_sim: Optional[np.ndarray], dens: np.ndarray,
                   w: Mapping[str, float]) -> np.ndarray:
    """One profile's blended score for the pool rows (``_score_candidates_for_profile``)."""
    m = len(pool_idx)
    # text: centroid cosine minus the CSLS hub penalty, as a percentile over the pool
    text = _pct(text_sim - CSLS_PENALTY * dens[pool_idx])
    # photos: percentile over the pool places that have one; "zero" policy = a place without a
    # photo scores 0 and the photo weight stays in its blend
    image = np.zeros(m)
    if image_sim is not None:
        has = taste.has_image[pool_idx]
        if has.any():
            image[has] = _pct(image_sim[has])
    # tags: Jaccard of the place's tags with the union of the profile's tags (0 without tags)
    tag = _tag_jaccard(taste, prow, pool_idx)
    # vibe axes: 1 - mean |axis - weighted seed mean| / 100; places without axes -> the pool median
    cols = taste.axis_columns
    if cols:
        centre = np.average(taste.axes[prow], axis=0, weights=pw)
        axis = np.clip(1 - np.abs(taste.axes[pool_idx] - centre).mean(axis=1) / 100, 0, 1)
        has_axes = taste.has_axes[pool_idx]
        if not has_axes.all():
            axis[~has_axes] = float(np.median(axis[has_axes])) if has_axes.any() else 0.5
    else:
        axis = np.full(m, 0.5)
    # price: closeness on the cheap/expensive axis
    if PRICE_AXIS in cols:
        j = cols.index(PRICE_AXIS)
        centre_price = np.average(taste.axes[prow, j], weights=pw)
        price = np.clip(1 - np.abs(taste.axes[pool_idx, j] - centre_price) / 100, 0, 1)
    else:
        price = np.full(m, 0.5)
    quality = np.nan_to_num(taste.quality[pool_idx], nan=0.0)

    values = {"text": text, "image": image, "tag": tag, "axis": axis, "quality": quality, "price": price}
    raw = {c: max(float(w.get(c, 0.0)), 0.0) for c in _CHANNELS}
    if image_sim is None:
        raw["image"] = 0.0                     # no seed photo (or no photo store): channel off
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("taste weights must have a positive sum")
    num = np.zeros(m)
    den = 0.0
    for c in _CHANNELS:                        # weighted average over the active channels, engine order
        wc = raw[c] / total
        if wc <= 0:
            continue
        num += wc * values[c]
        den += wc
    return num / den


def _tag_jaccard(taste: TasteArtifacts, prow: np.ndarray, pool_idx: np.ndarray) -> np.ndarray:
    """|place tags & profile tags| / |place tags | profile tags| per pool row; 0 without tags."""
    vocab, flat, owner, counts = taste._tag_index()
    profile_tags: set[str] = set()
    for r in prow.tolist():
        profile_tags.update(taste.tags[r])
    if not profile_tags or len(flat) == 0:
        return np.zeros(len(pool_idx))
    member = np.zeros(len(vocab), dtype=bool)
    member[[vocab[t] for t in profile_tags]] = True
    inter = np.bincount(owner[member[flat]], minlength=len(taste.tags))[pool_idx]
    cnt = counts[pool_idx]
    union = cnt + len(profile_tags) - inter
    return np.where(cnt > 0, inter / np.maximum(union, 1), 0.0)


# --------------------------------------------------------------------------- #
# Interest map (cold start + taste, quantile-preserving rank blend)
# --------------------------------------------------------------------------- #
@dataclass
class InterestResult:
    """Interest of every requested place, plus diagnostics for the API / dashboard.

    ``map``: place_id -> interest, for every requested place. ``mode``: "favourites" when taste
    re-ordered the map, else "popularity" (then ``map`` is the cold start). ``used``: seed ids that
    shaped the map (favourites first; empty in "popularity" mode). ``ignored``: given ids that
    could not be used — unknown to the city artifacts, without a text embedding or over
    ``MAX_SEEDS`` (all given ids when there are no artifacts). ``profiles``: number of taste
    profiles (0 = no taste). ``taste_pct``: within-theme taste percentile of each personalised
    place (seeds 1.0). ``similar_to``: per personalised place, the seed of its best profile with
    the most similar description (seeds -> themselves). ``strength``: the blend weight applied."""

    map: dict[str, float]
    mode: str
    used: list[str]
    ignored: list[str]
    profiles: int
    taste_pct: Optional[dict[str, float]] = None
    similar_to: Optional[dict[str, str]] = None
    strength: float = DEFAULT_STRENGTH
    version: str = INTEREST_VERSION


def interest_map(place_ids: Sequence[str], themes: Sequence[str], cold: Mapping[str, float],
                 taste: Optional[TasteArtifacts], favourite_ids: Iterable[str] = (),
                 want_to_go_ids: Iterable[str] = (), strength: float = DEFAULT_STRENGTH) -> InterestResult:
    """Interest for every place in ``place_ids``; ``themes`` is row-aligned (the catalog theme)
    and ``cold`` the :func:`cold_start` value per place_id (missing -> 0).

    Without a valid seed, without ``taste`` or with ``strength == 0`` the map is the cold start.
    Otherwise, per theme, the places with taste (known to ``taste``, with a text embedding, not a
    seed) are ranked by ``(1 - strength) * pct(cold) + strength * pct(taste)`` (ties -> place_id)
    and receive the theme's cold values in that order; seeds and places without taste keep their
    cold value, so the multiset of values per theme equals the cold multiset. ``strength`` is
    clipped to [0, 1]; duplicate place_ids keep their first theme."""
    strength = float(strength)
    if not math.isfinite(strength):
        raise ValueError(f"strength must be a finite number, got {strength!r}")
    strength = min(max(strength, 0.0), 1.0)
    pids, pos_theme = _unique_places(place_ids, themes)
    if isinstance(cold, pd.Series):                # Series.get per key is slow
        cold = cold.to_dict()
    get_cold = cold.get
    cold_arr = np.fromiter((get_cold(p, 0.0) for p in pids), dtype=float, count=len(pids))

    def popularity(ignored: list[str]) -> InterestResult:
        return InterestResult(map=dict(zip(pids, cold_arr.tolist())), mode=MODE_POPULARITY, used=[],
                              ignored=ignored, profiles=0, strength=strength)

    if taste is None:
        favs = _dedupe(favourite_ids)
        fav_set = set(favs)
        return popularity(favs + [p for p in _dedupe(want_to_go_ids) if p not in fav_set])
    rows, sig_w, used, ignored = resolve_seeds(taste, favourite_ids, want_to_go_ids)
    if len(rows) == 0 or strength <= 0:
        return popularity(ignored)
    scores = _taste_scores(taste, rows, sig_w, used, ignored, None, None, None)

    get_row = taste.index.get
    art_row = np.fromiter((get_row(p, -1) for p in pids), dtype=np.int64, count=len(pids))
    known = art_row >= 0
    is_seed = known & np.isin(art_row, scores.seed_rows)
    taste_val = np.full(len(pids), np.nan)
    taste_val[known] = scores.score[art_row[known]]
    member = known & ~is_seed & ~np.isnan(taste_val)

    out = cold_arr.copy()
    tpct = np.full(len(pids), np.nan)
    theme_code, _ = pd.factorize(np.asarray(pos_theme, dtype=object))
    for code in range(int(theme_code.max()) + 1 if len(theme_code) else 0):
        m = np.flatnonzero(member & (theme_code == code))
        if len(m) == 0:
            continue
        pt = _pct(taste_val[m])
        tpct[m] = pt
        b = (1.0 - strength) * _pct(cold_arr[m]) + strength * pt
        order = np.argsort(b, kind="stable")
        sorted_b = b[order]
        if np.any(sorted_b[1:] == sorted_b[:-1]):  # ties -> place_id, independent of input order
            order = np.lexsort((_string_ranks([pids[i] for i in m.tolist()]), b))
        out[m[order]] = np.sort(cold_arr[m])      # the theme's cold values, ascending
    tpct[is_seed] = 1.0

    mem = np.flatnonzero(member)
    similar_to = dict(zip([pids[i] for i in mem.tolist()],
                          [taste.place_ids[r] for r in scores.similar[art_row[mem]].tolist()]))
    for i in np.flatnonzero(is_seed).tolist():
        similar_to[pids[i]] = pids[i]
    has_pct = np.flatnonzero(~np.isnan(tpct))
    taste_pct = dict(zip([pids[i] for i in has_pct.tolist()], tpct[has_pct].tolist()))
    return InterestResult(map=dict(zip(pids, out.tolist())), mode=MODE_FAVOURITES, used=scores.used,
                          ignored=scores.ignored, profiles=len(scores.profiles), taste_pct=taste_pct,
                          similar_to=similar_to, strength=strength)


def warmup(taste: Optional[TasteArtifacts] = None) -> None:
    """Pay one-off costs before the first request (call at service start-up): import
    scikit-learn (used for 4+ seeds; ~0.5 s) and, given artifacts, build their lookups and
    float32 working copies and run one tiny request."""
    try:
        import sklearn.cluster  # noqa: F401
        import sklearn.metrics  # noqa: F401
    except ImportError:
        pass
    if taste is not None and len(taste):
        taste.index
        taste._tag_index()
        taste.matrix("text")
        taste.matrix("image")
        probe = np.flatnonzero(taste.has_text)
        if len(probe):
            taste_scores(taste, [taste.place_ids[int(probe[0])]])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _theme_str(value) -> str:
    if isinstance(value, str):
        return value
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def _unique_places(place_ids: Sequence[str], themes: Sequence[str]) -> tuple[list[str], list[str]]:
    """str place ids + their themes, duplicates dropped (the first occurrence keeps its theme)."""
    if len(themes) != len(place_ids):
        raise ValueError(f"themes has {len(themes)} rows, place_ids {len(place_ids)}")
    pids = [p if isinstance(p, str) else str(p) for p in place_ids]
    ths = [_theme_str(t) for t in themes]
    if len(set(pids)) == len(pids):
        return pids, ths
    first: dict[str, str] = {}
    for p, t in zip(pids, ths):
        first.setdefault(p, t)
    return list(first), list(first.values())


def _dedupe(values: Optional[Iterable[str]]) -> list[str]:
    """str ids, order kept, empties / None / duplicates dropped (``_dedupe_preserve_order``);
    a bare string counts as one id."""
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for v in values or ():
        if v is None:
            continue
        v = str(v)
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _pct(values: np.ndarray) -> np.ndarray:
    """Average-rank percentile (rank / n; ties share their mean rank) — identical to
    ``pd.Series(values).rank(method="average", pct=True)`` for NaN-free input."""
    x = np.asarray(values, dtype=float).reshape(-1)
    n = x.size
    if n == 0:
        return x.copy()
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    new_group = np.empty(n, dtype=bool)
    new_group[0] = True
    np.not_equal(xs[1:], xs[:-1], out=new_group[1:])
    starts = np.flatnonzero(new_group)
    ends = np.append(starts[1:], n)
    ranks = np.empty(n)
    ranks[order] = np.repeat((starts + 1 + ends) / 2.0, ends - starts)
    return ranks / n


def _string_ranks(values: Sequence[str]) -> np.ndarray:
    """Rank of each string in code-point order (the tie-break key of the rank blend)."""
    order = np.argsort(np.array(values, dtype=str), kind="stable")
    ranks = np.empty(len(values), dtype=np.int64)
    ranks[order] = np.arange(len(values), dtype=np.int64)
    return ranks


def _rows_f32(matrix: np.ndarray, rows: np.ndarray) -> np.ndarray:
    return np.asarray(matrix[np.asarray(rows, dtype=np.int64)], dtype=np.float32)


def _centroid(vectors: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted mean of the (unit) vectors, re-normalised (``_weighted_centroid``)."""
    c = np.average(vectors, axis=0, weights=weights)
    norm = np.linalg.norm(c)
    return (c / norm if norm else c).astype(np.float32)


def _matvecs(matrix: np.ndarray, vectors: Sequence[np.ndarray]) -> np.ndarray:
    """``matrix @ stack(vectors).T`` in float32 for every row; a float16 (possibly memory-mapped)
    matrix is up-cast a block at a time, never whole. Returns (n_rows, len(vectors))."""
    q = np.ascontiguousarray(np.stack([np.asarray(v, dtype=np.float32) for v in vectors], axis=1))
    if matrix.dtype == np.float32:
        return np.asarray(matrix @ q, dtype=np.float32)
    out = np.empty((matrix.shape[0], q.shape[1]), dtype=np.float32)
    for start in range(0, matrix.shape[0], _CHUNK_ROWS):
        stop = min(start + _CHUNK_ROWS, matrix.shape[0])
        np.matmul(np.asarray(matrix[start:stop], dtype=np.float32), q, out=out[start:stop])
    return out
