from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import numpy as np
import numpy.typing as npt

ALGORITHM_VERSION = "embedding_recommender_v1"


class ScoreComponents(TypedDict):
    similarity: float


class RecommendationResult(TypedDict):
    rank: int
    place_id: str
    score: float
    similarity: float


class InputSummary(TypedDict):
    favourites_count: int
    want_to_go_count: int
    valid_input_count: int
    invalid_place_ids: list[str]
    candidate_count: int


class RecommendationPayload(TypedDict):
    algorithm_version: str
    embedding_run_id: str
    input_summary: InputSummary
    recommendations: list[RecommendationResult]


@dataclass(frozen=True)
class EmbeddingMetadataRow:
    place_id: str
    embedding_row: int
    has_embedding: bool


def _parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _normalize_matrix(matrix: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.asarray(matrix / norms, dtype=np.float32)


def _normalize_vector(vector: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    norm = np.linalg.norm(vector)
    if norm == 0 or math.isnan(float(norm)):
        return vector
    return np.asarray(vector / norm, dtype=np.float32)


def _dedupe_preserve_order(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        place_id = str(value).strip()
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)
        result.append(place_id)
    return result


def _round_float(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


class EmbeddingRecommender:
    def __init__(
        self,
        normalized_embeddings: npt.NDArray[np.float32],
        place_id_to_row: dict[str, int],
        candidate_place_ids: list[str],
        candidate_rows: npt.NDArray[np.int_],
        embedding_run_id: str,
        favorites_weight: float = 1.0,
        want_to_go_weight: float = 0.55,
    ) -> None:
        self.normalized_embeddings = normalized_embeddings
        self.place_id_to_row = place_id_to_row
        self.candidate_place_ids = candidate_place_ids
        self.candidate_rows = candidate_rows
        self.embedding_run_id = embedding_run_id
        self.favorites_weight = favorites_weight
        self.want_to_go_weight = want_to_go_weight

    @classmethod
    def from_artifacts(
        cls,
        npy_path: str | Path,
        metadata_csv_path: str | Path,
        embedding_run_id: str,
        favorites_weight: float = 1.0,
        want_to_go_weight: float = 0.55,
    ) -> EmbeddingRecommender:
        matrix = np.asarray(np.load(npy_path), dtype=np.float32)
        metadata_rows = _load_metadata(metadata_csv_path)
        _validate_metadata(matrix, metadata_rows)

        place_id_to_row: dict[str, int] = {}
        candidate_place_ids: list[str] = []
        candidate_rows: list[int] = []

        for row in metadata_rows:
            if not row.has_embedding:
                continue
            place_id_to_row[row.place_id] = row.embedding_row
            candidate_place_ids.append(row.place_id)
            candidate_rows.append(row.embedding_row)

        return cls(
            normalized_embeddings=_normalize_matrix(matrix),
            place_id_to_row=place_id_to_row,
            candidate_place_ids=candidate_place_ids,
            candidate_rows=np.asarray(candidate_rows, dtype=int),
            embedding_run_id=embedding_run_id,
            favorites_weight=favorites_weight,
            want_to_go_weight=want_to_go_weight,
        )

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_place_ids)

    def recommend(
        self,
        favourites_place_ids: list[str] | None,
        want_to_go_place_ids: list[str] | None,
        limit: int,
        exclude_input_places: bool = True,
    ) -> RecommendationPayload:
        limit = max(int(limit), 0)
        favourites = _dedupe_preserve_order(favourites_place_ids)
        want_to_go = _dedupe_preserve_order(want_to_go_place_ids)
        input_place_ids = _dedupe_preserve_order(favourites + want_to_go)
        weights_by_place = self._build_weights(favourites, want_to_go)

        seed_rows: list[int] = []
        seed_weights: list[float] = []
        invalid_place_ids: list[str] = []

        for place_id in input_place_ids:
            row = self.place_id_to_row.get(place_id)
            if row is None:
                invalid_place_ids.append(place_id)
                continue
            seed_rows.append(row)
            seed_weights.append(weights_by_place[place_id])

        if not seed_rows or limit == 0:
            return {
                "algorithm_version": ALGORITHM_VERSION,
                "embedding_run_id": self.embedding_run_id,
                "input_summary": {
                    "favourites_count": len(favourites),
                    "want_to_go_count": len(want_to_go),
                    "valid_input_count": len(seed_rows),
                    "invalid_place_ids": invalid_place_ids,
                    "candidate_count": self._candidate_count_after_exclusions(
                        set(input_place_ids),
                        exclude_input_places,
                    ),
                },
                "recommendations": [],
            }

        centroid = self._centroid(seed_rows, seed_weights)
        excluded = set(input_place_ids) if exclude_input_places else set()
        recommendations = self._rank_candidates(centroid, excluded, limit)

        return {
            "algorithm_version": ALGORITHM_VERSION,
            "embedding_run_id": self.embedding_run_id,
            "input_summary": {
                "favourites_count": len(favourites),
                "want_to_go_count": len(want_to_go),
                "valid_input_count": len(seed_rows),
                "invalid_place_ids": invalid_place_ids,
                "candidate_count": self._candidate_count_after_exclusions(
                    excluded,
                    exclude_input_places,
                ),
            },
            "recommendations": recommendations,
        }

    def _build_weights(
        self,
        favourites: list[str],
        want_to_go: list[str],
    ) -> dict[str, float]:
        weights: dict[str, float] = {}
        for place_id in want_to_go:
            weights[place_id] = max(weights.get(place_id, 0.0), self.want_to_go_weight)
        for place_id in favourites:
            weights[place_id] = max(weights.get(place_id, 0.0), self.favorites_weight)
        return weights

    def _centroid(
        self,
        seed_rows: list[int],
        seed_weights: list[float],
    ) -> npt.NDArray[np.float32]:
        vectors = self.normalized_embeddings[np.asarray(seed_rows, dtype=int)]
        weights = np.asarray(seed_weights, dtype=np.float32)
        centroid = np.average(vectors, axis=0, weights=weights).astype(np.float32)
        return _normalize_vector(centroid)

    def _rank_candidates(
        self,
        centroid: npt.NDArray[np.float32],
        excluded_place_ids: set[str],
        limit: int,
    ) -> list[RecommendationResult]:
        candidate_vectors = self.normalized_embeddings[self.candidate_rows]
        similarities = candidate_vectors @ centroid
        rows: list[tuple[str, float]] = []

        for place_id, similarity in zip(
            self.candidate_place_ids,
            similarities,
            strict=True,
        ):
            if place_id in excluded_place_ids:
                continue
            rows.append((place_id, float(similarity)))

        rows.sort(key=lambda item: (-item[1], item[0]))
        return [
            {
                "rank": rank,
                "place_id": place_id,
                "score": _round_float((similarity + 1) / 2),
                "similarity": _round_float(similarity),
            }
            for rank, (place_id, similarity) in enumerate(rows[:limit], start=1)
        ]

    def _candidate_count_after_exclusions(
        self,
        excluded_place_ids: set[str],
        exclude_input_places: bool,
    ) -> int:
        if not exclude_input_places:
            return self.candidate_count
        embedded_excluded = sum(
            1 for place_id in excluded_place_ids if place_id in self.place_id_to_row
        )
        return self.candidate_count - embedded_excluded


def _load_metadata(metadata_csv_path: str | Path) -> list[EmbeddingMetadataRow]:
    rows: list[EmbeddingMetadataRow] = []
    with Path(metadata_csv_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"place_id", "embedding_row", "has_embedding"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Embedding metadata is missing columns: {sorted(missing)}"
            )

        for line_number, raw in enumerate(reader, start=2):
            place_id = str(raw.get("place_id") or "").strip()
            if not place_id:
                raise ValueError(f"Missing place_id on metadata line {line_number}")
            try:
                embedding_row = int(str(raw.get("embedding_row") or "").strip())
            except ValueError as exc:
                raise ValueError(
                    f"Invalid embedding_row for {place_id!r} on line {line_number}"
                ) from exc
            rows.append(
                EmbeddingMetadataRow(
                    place_id=place_id,
                    embedding_row=embedding_row,
                    has_embedding=_parse_bool(raw.get("has_embedding")),
                )
            )
    return rows


def _validate_metadata(
    matrix: npt.NDArray[np.float32],
    metadata_rows: list[EmbeddingMetadataRow],
) -> None:
    if len(matrix) != len(metadata_rows):
        raise ValueError(
            f"Embedding matrix rows ({len(matrix)}) do not match metadata rows "
            f"({len(metadata_rows)})"
        )

    seen: set[str] = set()
    duplicates: list[str] = []
    for row in metadata_rows:
        if row.place_id in seen:
            duplicates.append(row.place_id)
        seen.add(row.place_id)

        if row.has_embedding and not 0 <= row.embedding_row < len(matrix):
            raise ValueError(
                f"Invalid embedding_row for {row.place_id!r}: {row.embedding_row}"
            )

    if duplicates:
        raise ValueError(f"Duplicate place_id values in metadata: {duplicates[:10]}")
