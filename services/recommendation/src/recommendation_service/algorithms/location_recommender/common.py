"""Shared, dependency-light helpers used across the recommender modules.

Single source of truth for primitives that were previously copy-pasted into
``backend_recommender``, ``direct_image_embedding_utils`` and
``visual_photo_profile_utils``. Pure functions only — no I/O, no Streamlit.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np


def normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize each row of ``matrix``.

    Accepts a 1-D vector (treated as a single row) or a 2-D matrix. Rows with
    zero norm are left as zeros (norm clamped to 1.0 to avoid division by zero).
    Always returns a float32 2-D array.
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def utc_run_id(prefix: str) -> str:
    """Return a sortable, timezone-safe run id of the form ``<prefix>_<UTC stamp>``.

    The stamp is ``YYYYmmddTHHMMSSZ`` in UTC.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}"
