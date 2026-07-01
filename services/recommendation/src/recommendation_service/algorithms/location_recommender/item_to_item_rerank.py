"""Item-to-item recommendation: config and pure rerank primitives.

This module has no dependency on LocationRecommender and is independently
testable. All functions are deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --- weight presets (Tier 1) -------------------------------------------------

WEIGHTS_SIGNAL_BALANCED = {
    "semantic": 0.60,
    "axis": 0.15,
    "tag": 0.15,
    "price": 0.05,
    "quality": 0.05,
}
WEIGHTS_SEMANTIC_LED = {  # production parity, for A/B
    "semantic": 0.72,
    "tag": 0.10,
    "axis": 0.08,
    "quality": 0.07,
    "price": 0.03,
}

CONFIDENCE_WEIGHTS = {"high": 1.0, "medium": 0.6, "low": 0.3, "unknown": 0.3}


# --- Tier 2a: hubness --------------------------------------------------------

def hubness_density(normalized_embeddings: np.ndarray, rows: np.ndarray, k: int) -> np.ndarray:
    """Mean cosine of each row to its k nearest neighbours within `rows`.

    `normalized_embeddings` must be L2-normalized. Returns an array aligned to
    `rows`. Self-similarity is excluded.
    """
    rows = np.asarray(rows, dtype=int)
    sub = np.asarray(normalized_embeddings)[rows]
    sims = sub @ sub.T
    np.fill_diagonal(sims, -np.inf)
    k_eff = min(int(k), sims.shape[1] - 1)
    if k_eff <= 0:
        return np.zeros(len(rows), dtype=float)
    topk = np.partition(sims, -k_eff, axis=1)[:, -k_eff:]
    return topk.mean(axis=1)


# --- Tier 2b: diversity ------------------------------------------------------

def mmr_select(relevance: np.ndarray, candidate_vectors: np.ndarray, lambda_: float, k: int) -> list[int]:
    """Greedy MMR selection. Returns a list of local indices into the arrays.

    `candidate_vectors` must be L2-normalized so that dot product is cosine.
    Ties are broken by lowest index (deterministic).
    """
    relevance = np.asarray(relevance, dtype=float)
    vectors = np.asarray(candidate_vectors)
    n = len(relevance)
    k = min(int(k), n)
    selected: list[int] = []
    remaining = list(range(n))
    while remaining and len(selected) < k:
        if not selected:
            best = max(remaining, key=lambda i: (relevance[i], -i))
        else:
            sel = vectors[selected]
            def mmr_score(i: int) -> tuple[float, int]:
                redundancy = float(np.max(vectors[i] @ sel.T))
                return (lambda_ * relevance[i] - (1.0 - lambda_) * redundancy, -i)
            best = max(remaining, key=mmr_score)
        selected.append(best)
        remaining.remove(best)
    return selected


def cluster_cap(ordered_indices: list[int], labels: np.ndarray, cap: int) -> list[int]:
    """Keep items in order, dropping any beyond `cap` occurrences of a label.

    `labels` is indexed by the values in `ordered_indices`.
    """
    labels = np.asarray(labels)
    counts: dict = {}
    kept: list[int] = []
    for idx in ordered_indices:
        label = labels[idx]
        key = label.item() if hasattr(label, "item") else label
        seen = counts.get(key, 0)
        if seen < cap:
            kept.append(idx)
            counts[key] = seen + 1
    return kept


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class ItemToItemConfig:
    weights: dict = field(default_factory=lambda: dict(WEIGHTS_SIGNAL_BALANCED))
    tag_overlap_version: str = "v1"        # "v1" = recommendation_tags Jaccard; "v1.1" = polarity-weighted
    hubness_method: str = "csls"           # "csls" | "mutual_knn" | "none"
    hubness_k: int = 10
    hubness_penalty: float = 0.5
    diversity_enabled: bool = True
    mmr_lambda: float = 0.7
    cluster_cap: int = 3
    cluster_col: str = "global_cluster"
    candidate_topN: int = 200


def _positive_weights(tags: list[dict]) -> dict:
    weights: dict = {}
    for item in tags or []:
        if item.get("polarity") == "negative":
            continue
        name = item.get("tag")
        if not name:
            continue
        conf = CONFIDENCE_WEIGHTS.get(item.get("confidence", "unknown"), 0.3)
        weights[name] = max(weights.get(name, 0.0), conf)
    return weights


def polarity_weighted_tag_overlap(seed_tags: list[dict], candidate_tags: list[dict]) -> float:
    """Weighted Jaccard over positive tags, weighted by confidence.

    Each input is a list of {"tag", "confidence", "polarity"} dicts
    (as produced by parse_ai_tags_json). Negative-polarity tags are ignored.
    """
    a = _positive_weights(seed_tags)
    b = _positive_weights(candidate_tags)
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    inter = sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    union = sum(max(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    return float(inter / union) if union else 0.0
