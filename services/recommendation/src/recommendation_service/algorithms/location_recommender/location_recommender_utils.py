from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_EMBEDDING_INPUTS_PER_BATCH = 10_000
RANDOM_STATE = 42

GENERIC_TYPES = {
    "point_of_interest",
    "establishment",
    "store",
    "food",
    "food_store",
    "service",
}

PRICE_RELATED_TAGS = {
    "cheap",
    "good_value",
    "premium",
    "expensive",
    "overpriced",
}

AXIS_DEFINITIONS = {
    "axis_quiet_lively": ("quiet/calm", "balanced energy", "lively/energetic"),
    "axis_work_social": ("work/focus friendly", "balanced work/social", "social/group friendly"),
    "axis_day_night": ("daytime", "day-to-evening", "nightlife/late-night"),
    "axis_casual_premium": ("casual/simple", "smart casual", "premium/polished"),
    "axis_drinks_food": ("drinks-focused", "balanced drinks/food", "food-focused"),
    "axis_local_tourist": ("local/neighborhood", "mixed local/tourist", "tourist/visitor friendly"),
    "axis_cheap_expensive": ("cheap/budget", "moderate", "expensive/premium"),
    "axis_traditional_experimental": ("traditional/classic", "balanced", "experimental/creative"),
}


def find_project_root(start: str | Path | None = None) -> Path:
    start_path = Path(start or os.getcwd()).resolve()
    for candidate in [start_path, *start_path.parents]:
        if (candidate / ".env").exists():
            return candidate
    for candidate in [start_path, *start_path.parents]:
        if (candidate / "artifacts").exists() and (candidate / "recommendation_system").exists():
            return candidate
    for candidate in [start_path, *start_path.parents]:
        if (candidate / "recommendation_system").exists():
            return candidate
    return start_path if start_path.is_dir() else start_path.parent


PROJECT_ROOT = find_project_root(Path(__file__).resolve())
SOURCE_CSV_PATH = PROJECT_ROOT / "data_scraping" / "output" / "ai_location_summaries" / "final" / "final_ai_dataframe_with_map_scores_latest.csv"
RECOMMENDER_ROOT = PROJECT_ROOT / "recommendation_system" / "ai_location_recommender"
DATA_DIR = RECOMMENDER_ROOT / "data"
EMBEDDING_REQUEST_DIR = DATA_DIR / "embedding_requests"
BATCH_MANIFEST_DIR = DATA_DIR / "batch_manifests"
BATCH_OUTPUT_DIR = DATA_DIR / "batch_outputs"
EMBEDDING_STORE_DIR = DATA_DIR / "embedding_store"
EXPORT_DIR = DATA_DIR / "exports"
BATCH_MANIFEST_PATH = BATCH_MANIFEST_DIR / "openai_location_embedding_batches_manifest.json"


def ensure_artifact_dirs() -> dict[str, Path]:
    dirs = {
        "data": DATA_DIR,
        "embedding_requests": EMBEDDING_REQUEST_DIR,
        "batch_manifests": BATCH_MANIFEST_DIR,
        "batch_outputs": BATCH_OUTPUT_DIR,
        "embedding_store": EMBEDDING_STORE_DIR,
        "exports": EXPORT_DIR,
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def parse_listish(value) -> list[str]:
    if _is_missing(value):
        return []
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, tuple) or isinstance(value, set):
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


def parse_dictish(value) -> dict:
    if _is_missing(value):
        return {}
    if isinstance(value, dict):
        return value
    raw = str(value).strip()
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_ai_tags_json(value) -> list[dict]:
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
                tags.append(
                    {
                        "tag": tag,
                        "confidence": clean_text(item.get("confidence")).lower() or "unknown",
                        "polarity": clean_text(item.get("polarity")).lower() or "neutral",
                    }
                )
    return tags


def parse_tag_names(row, include_low_confidence: bool = False, include_negative: bool = False) -> list[str]:
    tags = parse_ai_tags_json(row.get("ai_tags_json"))
    if not tags:
        tags = [{"tag": tag, "confidence": "unknown", "polarity": "neutral"} for tag in parse_listish(row.get("ai_tags_csv"))]

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


def clean_types(value) -> list[str]:
    return [item for item in parse_listish(value) if item and item not in GENERIC_TYPES]


def clean_features(value) -> list[str]:
    parsed = parse_dictish(value)
    if parsed:
        return [key for key, enabled in parsed.items() if enabled is True]
    return parse_listish(value)


def value_to_float(value) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def axis_bucket(column: str, value) -> str:
    number = value_to_float(value)
    if number is None:
        return ""
    low, mid, high = AXIS_DEFINITIONS[column]
    if number <= 32:
        label = low
    elif number <= 66:
        label = mid
    else:
        label = high
    return f"{column.replace('axis_', '').replace('_', ' ')}: {label} ({int(round(number))}/100)"


def price_bucket(row) -> str:
    price_level = clean_text(row.get("price_level"))
    axis_value = value_to_float(row.get("axis_cheap_expensive"))
    min_price = value_to_float(row.get("price_min_ron"))
    max_price = value_to_float(row.get("price_max_ron"))

    if "VERY_EXPENSIVE" in price_level:
        bucket = "very expensive"
    elif "EXPENSIVE" in price_level and "INEXPENSIVE" not in price_level:
        bucket = "expensive"
    elif "INEXPENSIVE" in price_level:
        bucket = "inexpensive"
    elif "MODERATE" in price_level:
        bucket = "moderate"
    elif axis_value is not None:
        if axis_value <= 25:
            bucket = "budget"
        elif axis_value <= 55:
            bucket = "moderate"
        else:
            bucket = "premium"
    else:
        bucket = ""

    range_part = ""
    if min_price is not None and max_price is not None:
        range_part = f", observed range {min_price:g}-{max_price:g} RON"
    elif min_price is not None:
        range_part = f", observed minimum {min_price:g} RON"
    elif max_price is not None:
        range_part = f", observed maximum {max_price:g} RON"

    return f"{bucket}{range_part}".strip(", ")


def format_ai_tags_for_embedding(row) -> str:
    tags = parse_ai_tags_json(row.get("ai_tags_json"))
    if not tags:
        tag_names = parse_listish(row.get("ai_tags_csv"))
        return ", ".join(tag_names)

    strong = []
    possible = []
    downsides = []
    price_tags = []
    for item in tags:
        tag = clean_text(item.get("tag"))
        if not tag:
            continue
        confidence = item.get("confidence", "unknown")
        polarity = item.get("polarity", "neutral")
        if tag in PRICE_RELATED_TAGS:
            price_tags.append(tag)
        if polarity == "negative":
            downsides.append(tag)
        elif confidence == "low":
            possible.append(tag)
        else:
            strong.append(tag)

    parts = []
    if strong:
        parts.append(f"strong tags: {', '.join(sorted(set(strong)))}")
    if price_tags:
        parts.append(f"price/value tags: {', '.join(sorted(set(price_tags)))}")
    if downsides:
        parts.append(f"downside tags: {', '.join(sorted(set(downsides)))}")
    if possible:
        parts.append(f"possible low-confidence tags: {', '.join(sorted(set(possible)))}")
    return "; ".join(parts)


def build_axis_bucket_text(row) -> str:
    labels = [axis_bucket(column, row.get(column)) for column in AXIS_DEFINITIONS]
    return "; ".join(label for label in labels if label)


def build_location_embedding_text(row) -> str:
    useful_types = clean_types(row.get("types"))
    serves = parse_listish(row.get("serves"))
    features = clean_features(row.get("features"))
    tag_text = format_ai_tags_for_embedding(row)
    axis_text = build_axis_bucket_text(row)
    price_text = price_bucket(row)

    parts = [
        ("Venue name", clean_text(row.get("name"))),
        ("Primary type", clean_text(row.get("primary_type"))),
        ("AI place type", clean_text(row.get("ai_place_type_summary"))),
        ("Useful Google types", ", ".join(useful_types)),
        ("Core description", clean_text(row.get("ai_card_summary"))),
        ("Vibe and atmosphere", clean_text(row.get("ai_vibe"))),
        ("What to expect", clean_text(row.get("ai_what_to_expect"))),
        ("Food and drinks", clean_text(row.get("ai_food_and_drinks"))),
        ("Price and value", " ".join(part for part in [clean_text(row.get("ai_price")), price_text] if part)),
        ("Service", clean_text(row.get("ai_service"))),
        ("Best move", clean_text(row.get("ai_the_move"))),
        ("Watch out", clean_text(row.get("ai_watch_out"))),
        ("Semantic tags", tag_text),
        ("Serves", ", ".join(serves)),
        ("Features", ", ".join(features)),
        ("Vibe axes", axis_text),
    ]
    return "\n".join(f"{label}: {text}" for label, text in parts if text)


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def add_embedding_preparation_columns(df: pd.DataFrame) -> pd.DataFrame:
    prepared = df.copy()
    prepared["source_row_index"] = np.arange(len(prepared), dtype=int)
    prepared["useful_types"] = prepared["types"].apply(clean_types)
    prepared["parsed_serves"] = prepared["serves"].apply(parse_listish)
    prepared["parsed_features"] = prepared["features"].apply(clean_features)
    prepared["recommendation_tags"] = prepared.apply(parse_tag_names, axis=1)
    prepared["price_bucket"] = prepared.apply(price_bucket, axis=1)
    prepared["axis_bucket_text"] = prepared.apply(build_axis_bucket_text, axis=1)
    prepared["embedding_text"] = prepared.apply(build_location_embedding_text, axis=1)
    prepared["embedding_text_hash"] = prepared["embedding_text"].apply(stable_text_hash)
    prepared["semantic_info_score"] = prepared.apply(compute_semantic_info_score, axis=1)
    return prepared


def compute_semantic_info_score(row) -> float:
    score = 0.0
    weighted_text_fields = {
        "ai_card_summary": 2.0,
        "ai_vibe": 2.0,
        "ai_what_to_expect": 1.5,
        "ai_food_and_drinks": 1.2,
        "ai_price": 1.0,
        "ai_service": 0.8,
        "ai_the_move": 0.8,
        "ai_watch_out": 0.8,
    }
    for column, weight in weighted_text_fields.items():
        if clean_text(row.get(column)):
            score += weight
    if clean_text(row.get("primary_type")):
        score += 0.8
    score += min(len(clean_types(row.get("types"))), 5) * 0.25
    score += min(len(parse_listish(row.get("serves"))), 5) * 0.25
    score += min(len(clean_features(row.get("features"))), 8) * 0.15
    score += min(len(parse_tag_names(row, include_low_confidence=False, include_negative=True)), 12) * 0.25

    confidence = clean_text(row.get("ai_confidence")).lower()
    if confidence == "high":
        score += 2.0
    elif confidence == "medium":
        score += 1.0

    review_count = value_to_float(row.get("apify_review_count")) or 0
    score += min(review_count, 50) / 50
    if value_to_float(row.get("google_rating")) is not None:
        score += 0.5
    if value_to_float(row.get("google_user_rating_count")) is not None:
        score += 0.5
    return round(score, 3)


def load_source_dataframe(source_csv_path: str | Path = SOURCE_CSV_PATH) -> pd.DataFrame:
    return pd.read_csv(source_csv_path)


def load_openai_api_key(env_path: str | Path | None = None) -> str:
    env_path = Path(env_path or PROJECT_ROOT / ".env")
    if os.getenv("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    if not env_path.exists():
        raise FileNotFoundError(f".env file not found: {env_path}")

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "OPENAI_API_KEY":
            value = value.strip().strip('"').strip("'")
            os.environ["OPENAI_API_KEY"] = value
            return value
    raise ValueError("OPENAI_API_KEY was not found in .env")


def get_embedding_token_encoder(model: str = DEFAULT_EMBEDDING_MODEL):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_embedding_tokens(texts: Iterable[str], model: str = DEFAULT_EMBEDDING_MODEL) -> int:
    encoder = get_embedding_token_encoder(model)
    return sum(len(encoder.encode(str(text))) for text in texts)


def estimate_embedding_batch_cost_usd(total_tokens: int, model: str = DEFAULT_EMBEDDING_MODEL) -> float:
    batch_price_per_1m = {
        "text-embedding-3-small": 0.01,
        "text-embedding-3-large": 0.065,
    }.get(model, 0.01)
    return total_tokens / 1_000_000 * batch_price_per_1m


def summarize_embedding_token_usage(
    df_source: pd.DataFrame,
    text_col: str = "embedding_text",
    model: str = DEFAULT_EMBEDDING_MODEL,
    max_inputs_per_batch: int = MAX_EMBEDDING_INPUTS_PER_BATCH,
) -> dict:
    texts = df_source[text_col].fillna("").astype(str).tolist()
    token_counts = [count_embedding_tokens([text], model=model) for text in texts]
    total_tokens = int(sum(token_counts))
    return {
        "rows": len(texts),
        "total_tokens": total_tokens,
        "avg_tokens_per_row": total_tokens / max(len(texts), 1),
        "max_tokens_per_row": max(token_counts) if token_counts else 0,
        "estimated_batch_cost_usd": estimate_embedding_batch_cost_usd(total_tokens, model=model),
        "model": model,
        "max_inputs_per_batch": max_inputs_per_batch,
        "estimated_batches": math.ceil(len(texts) / max_inputs_per_batch),
    }


def safe_custom_id(value) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")[:90]


def iter_batch_slices(df_source: pd.DataFrame, max_inputs_per_batch: int = MAX_EMBEDDING_INPUTS_PER_BATCH):
    for start in range(0, len(df_source), max_inputs_per_batch):
        end = min(start + max_inputs_per_batch, len(df_source))
        yield start, end, df_source.iloc[start:end]


def write_embedding_batch_jsonl(
    df_batch: pd.DataFrame,
    output_path: str | Path,
    text_col: str = "embedding_text",
    model: str = DEFAULT_EMBEDDING_MODEL,
    dimensions: int | None = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for idx, row in df_batch.iterrows():
            source_row_index = int(row.get("source_row_index", idx))
            place_id = safe_custom_id(row.get("place_id", "missing_place_id"))
            body = {"model": model, "input": str(row[text_col])}
            if dimensions is not None:
                body["dimensions"] = int(dimensions)
            request = {
                "custom_id": f"location_{source_row_index}_{place_id}",
                "method": "POST",
                "url": "/v1/embeddings",
                "body": body,
            }
            f.write(json.dumps(request, ensure_ascii=False) + "\n")
    return output_path


def validate_embedding_batch_jsonl(path: str | Path) -> dict:
    path = Path(path)
    custom_ids = []
    models = set()
    urls = set()
    line_count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line_count += 1
            item = json.loads(line)
            custom_ids.append(item["custom_id"])
            urls.add(item.get("url"))
            models.add((item.get("body") or {}).get("model"))
            if item.get("method") != "POST":
                raise ValueError(f"Invalid method on line {line_count}: {item.get('method')}")
            if not (item.get("body") or {}).get("input"):
                raise ValueError(f"Missing input on line {line_count}")
    return {
        "path": str(path),
        "line_count": line_count,
        "unique_custom_ids": len(set(custom_ids)),
        "duplicate_custom_ids": line_count - len(set(custom_ids)),
        "models": sorted(models),
        "urls": sorted(urls),
        "size_mb": path.stat().st_size / 1024 / 1024,
    }


def load_batch_manifest(manifest_path: str | Path = BATCH_MANIFEST_PATH) -> list[dict]:
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return []
    return json.loads(manifest_path.read_text())


def save_batch_manifest(records: list[dict], manifest_path: str | Path = BATCH_MANIFEST_PATH) -> Path:
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return manifest_path


def submit_embedding_batches(
    df_source: pd.DataFrame,
    text_col: str = "embedding_text",
    model: str = DEFAULT_EMBEDDING_MODEL,
    max_inputs_per_batch: int = MAX_EMBEDDING_INPUTS_PER_BATCH,
    batch_dir: str | Path = EMBEDDING_REQUEST_DIR,
    manifest_path: str | Path = BATCH_MANIFEST_PATH,
    source_csv_path: str | Path = SOURCE_CSV_PATH,
    dimensions: int | None = None,
) -> list[dict]:
    from openai import OpenAI

    ensure_artifact_dirs()
    load_openai_api_key()
    client = OpenAI()
    manifest = load_batch_manifest(manifest_path)
    created_records = []
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for batch_number, (start, end, df_batch) in enumerate(iter_batch_slices(df_source, max_inputs_per_batch), start=1):
        jsonl_path = Path(batch_dir) / f"location_embedding_requests_{run_id}_part_{batch_number:03d}_{start}_{end}.jsonl"
        write_embedding_batch_jsonl(df_batch, jsonl_path, text_col=text_col, model=model, dimensions=dimensions)

        with jsonl_path.open("rb") as batch_file:
            input_file = client.files.create(file=batch_file, purpose="batch")
        batch = client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/embeddings",
            completion_window="24h",
            metadata={
                "project": "ai_location_recommender",
                "model": model,
                "row_start": str(start),
                "row_end": str(end),
            },
        )

        estimated_tokens = count_embedding_tokens(df_batch[text_col].fillna("").astype(str).tolist(), model=model)
        record = {
            "run_id": run_id,
            "batch_number": batch_number,
            "batch_id": batch.id,
            "input_file_id": input_file.id,
            "jsonl_path": str(jsonl_path),
            "row_start": start,
            "row_end": end,
            "n_inputs": len(df_batch),
            "estimated_input_tokens": estimated_tokens,
            "estimated_batch_cost_usd": estimate_embedding_batch_cost_usd(estimated_tokens, model=model),
            "model": model,
            "dimensions": dimensions,
            "endpoint": "/v1/embeddings",
            "source_csv_path": str(source_csv_path),
            "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        manifest.append(record)
        created_records.append(record)
        save_batch_manifest(manifest, manifest_path)
        print(f"Submitted batch {batch_number}: {batch.id} ({start}:{end}, {len(df_batch)} inputs)")

    return created_records


def batch_to_dict(batch) -> dict:
    return batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)


def check_embedding_batch_statuses(manifest_path: str | Path = BATCH_MANIFEST_PATH) -> pd.DataFrame:
    from openai import OpenAI

    load_openai_api_key()
    client = OpenAI()
    manifest = load_batch_manifest(manifest_path)
    if not manifest:
        print(f"No batch manifest found at {manifest_path}")
        return pd.DataFrame()

    rows = []
    for record in manifest:
        batch = batch_to_dict(client.batches.retrieve(record["batch_id"]))
        counts = batch.get("request_counts") or {}
        rows.append(
            {
                "run_id": record.get("run_id"),
                "batch_number": record.get("batch_number"),
                "batch_id": record.get("batch_id"),
                "status": batch.get("status"),
                "total": counts.get("total"),
                "completed": counts.get("completed"),
                "failed": counts.get("failed"),
                "output_file_id": batch.get("output_file_id"),
                "error_file_id": batch.get("error_file_id"),
                "n_inputs": record.get("n_inputs"),
                "estimated_input_tokens": record.get("estimated_input_tokens"),
                "estimated_batch_cost_usd": record.get("estimated_batch_cost_usd"),
                "submitted_at_utc": record.get("submitted_at_utc"),
            }
        )
    return pd.DataFrame(rows).sort_values(["run_id", "batch_number"]).reset_index(drop=True)


def get_latest_run_id(manifest_path: str | Path = BATCH_MANIFEST_PATH) -> str:
    manifest = load_batch_manifest(manifest_path)
    if not manifest:
        raise ValueError(f"No batch records found in {manifest_path}")
    return sorted({record["run_id"] for record in manifest})[-1]


def output_path_for_batch(record: dict, run_id: str, output_dir: str | Path = BATCH_OUTPUT_DIR) -> Path:
    return Path(output_dir) / f"location_embedding_output_{run_id}_part_{record['batch_number']:03d}_{record['row_start']}_{record['row_end']}.jsonl"


def count_jsonl_lines(path: str | Path) -> int:
    with Path(path).open("rb") as f:
        return sum(1 for _ in f)


def output_file_is_complete(output_path: str | Path, expected_rows: int) -> bool:
    output_path = Path(output_path)
    if not output_path.exists() or output_path.stat().st_size == 0:
        return False
    return count_jsonl_lines(output_path) == expected_rows


def download_file_with_retries(client, file_id: str, output_path: str | Path, max_retries: int = 6, base_sleep_seconds: int = 8):
    output_path = Path(output_path)
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    for attempt in range(1, max_retries + 1):
        try:
            if tmp_path.exists():
                tmp_path.unlink()
            content = client.files.content(file_id)
            content.write_to_file(tmp_path)
            tmp_path.replace(output_path)
            return True
        except Exception:
            if attempt == max_retries:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise
            time.sleep(base_sleep_seconds * attempt)


def download_completed_embedding_batch_outputs(
    run_id: str | None = None,
    manifest_path: str | Path = BATCH_MANIFEST_PATH,
    output_dir: str | Path = BATCH_OUTPUT_DIR,
    max_retries: int = 6,
    raise_on_error: bool = False,
) -> dict[str, list[dict]]:
    from openai import OpenAI

    ensure_artifact_dirs()
    load_openai_api_key()
    client = OpenAI(max_retries=3, timeout=180)
    manifest = load_batch_manifest(manifest_path)
    if run_id is None:
        run_id = get_latest_run_id(manifest_path)

    selected = [record for record in manifest if record.get("run_id") == run_id]
    downloaded = []
    skipped = []
    failed = []

    for record in selected:
        batch = batch_to_dict(client.batches.retrieve(record["batch_id"]))
        status = batch.get("status")
        output_file_id = batch.get("output_file_id")
        output_path = output_path_for_batch(record, run_id, output_dir)

        if status != "completed" or not output_file_id:
            skipped.append(
                {
                    "batch_id": record["batch_id"],
                    "batch_number": record["batch_number"],
                    "status": status,
                    "reason": "not completed yet or no output_file_id",
                }
            )
            continue

        try:
            if output_file_is_complete(output_path, record["n_inputs"]):
                print(f"Already downloaded: batch {record['batch_number']} -> {output_path.name}")
            else:
                print(f"Downloading batch {record['batch_number']} ({record['n_inputs']} rows) -> {output_path.name}")
                download_file_with_retries(client, output_file_id, output_path, max_retries=max_retries)
                if not output_file_is_complete(output_path, record["n_inputs"]):
                    raise ValueError(f"Downloaded file failed row-count check: {output_path}")
            downloaded.append({**record, "status": status, "output_file_id": output_file_id, "output_jsonl_path": str(output_path)})
        except Exception as exc:
            failed_record = {
                "batch_id": record["batch_id"],
                "batch_number": record["batch_number"],
                "status": status,
                "output_file_id": output_file_id,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
            failed.append(failed_record)
            print(f"Failed batch {record['batch_number']}: {type(exc).__name__}: {str(exc)[:200]}")
            if raise_on_error:
                raise

    return {"run_id": run_id, "downloaded": downloaded, "skipped": skipped, "failed": failed}


def parse_source_row_index_from_custom_id(custom_id: str) -> int:
    match = re.match(r"^location_(\d+)_", custom_id or "")
    if not match:
        raise ValueError(f"Unexpected custom_id format: {custom_id}")
    return int(match.group(1))


def iter_embedding_output_items(output_jsonl_paths: Iterable[str | Path]):
    for output_jsonl_path in output_jsonl_paths:
        output_jsonl_path = Path(output_jsonl_path)
        with output_jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                custom_id = item.get("custom_id")
                response = item.get("response") or {}
                status_code = response.get("status_code")
                body = response.get("body") or {}

                if status_code != 200:
                    yield {
                        "custom_id": custom_id,
                        "source_row_index": parse_source_row_index_from_custom_id(custom_id) if custom_id else None,
                        "ok": False,
                        "error": body,
                        "embedding": None,
                        "usage": None,
                    }
                    continue

                data = body.get("data") or []
                embedding = data[0].get("embedding") if data else None
                yield {
                    "custom_id": custom_id,
                    "source_row_index": parse_source_row_index_from_custom_id(custom_id),
                    "ok": embedding is not None,
                    "error": None,
                    "embedding": embedding,
                    "usage": body.get("usage"),
                }


def assemble_location_embedding_store(
    df_source: pd.DataFrame,
    downloaded_outputs: list[dict] | None = None,
    run_id: str | None = None,
    embeddings_dir: str | Path = EMBEDDING_STORE_DIR,
) -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame, Path, Path]:
    embeddings_dir = Path(embeddings_dir)
    embeddings_dir.mkdir(parents=True, exist_ok=True)
    if downloaded_outputs is None:
        if run_id is None:
            run_id = get_latest_run_id()
        output_paths = sorted(BATCH_OUTPUT_DIR.glob(f"location_embedding_output_{run_id}_part_*.jsonl"))
    else:
        output_paths = [Path(record["output_jsonl_path"]) for record in downloaded_outputs]
        if run_id is None and downloaded_outputs:
            run_id = downloaded_outputs[0]["run_id"]

    if not output_paths:
        raise FileNotFoundError("No batch output JSONL files found. Run download_completed_embedding_batch_outputs() first.")

    records = []
    embeddings = []
    for item in iter_embedding_output_items(output_paths):
        source_row_index = item["source_row_index"]
        if not item["ok"]:
            records.append(
                {
                    "source_row_index": source_row_index,
                    "place_id": df_source.iloc[source_row_index].get("place_id") if source_row_index is not None else None,
                    "custom_id": item["custom_id"],
                    "embedding_row": None,
                    "has_embedding": False,
                    "embedding_text_hash": df_source.iloc[source_row_index].get("embedding_text_hash") if source_row_index is not None else None,
                    "error": json.dumps(item["error"], ensure_ascii=False),
                    "usage_total_tokens": None,
                }
            )
            continue

        embedding_row = len(embeddings)
        embeddings.append(item["embedding"])
        usage = item.get("usage") or {}
        records.append(
            {
                "source_row_index": source_row_index,
                "place_id": df_source.iloc[source_row_index].get("place_id"),
                "custom_id": item["custom_id"],
                "embedding_row": embedding_row,
                "has_embedding": True,
                "embedding_text_hash": df_source.iloc[source_row_index].get("embedding_text_hash"),
                "error": None,
                "usage_total_tokens": usage.get("total_tokens"),
            }
        )

    embedding_meta = pd.DataFrame(records).sort_values("source_row_index").reset_index(drop=True)
    duplicate_count = embedding_meta["source_row_index"].duplicated().sum()
    if duplicate_count:
        raise ValueError(f"Found duplicate source_row_index values in embedding outputs: {duplicate_count}")

    embedding_matrix = np.asarray(embeddings, dtype=np.float32)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    matrix_path = embeddings_dir / f"location_embeddings_{run_id}.npy"
    meta_path = embeddings_dir / f"location_embeddings_{run_id}_metadata.parquet"
    csv_meta_path = embeddings_dir / f"location_embeddings_{run_id}_metadata.csv"

    np.save(matrix_path, embedding_matrix)
    try:
        embedding_meta.to_parquet(meta_path, index=False)
        saved_meta_path = meta_path
    except Exception as exc:
        embedding_meta.to_csv(csv_meta_path, index=False)
        saved_meta_path = csv_meta_path
        print(f"Could not save parquet metadata, saved CSV instead: {exc}")

    df_with_refs = attach_embedding_refs(df_source, embedding_meta)
    print(f"embedding_matrix shape: {embedding_matrix.shape}")
    print(f"saved matrix: {matrix_path}")
    print(f"saved metadata: {saved_meta_path}")
    print(f"rows with embeddings: {int(df_with_refs['has_embedding'].sum())} / {len(df_with_refs)}")
    return df_with_refs, embedding_matrix, embedding_meta, matrix_path, saved_meta_path


def load_location_embedding_store(
    run_id: str | None = None,
    embeddings_dir: str | Path = EMBEDDING_STORE_DIR,
) -> tuple[np.ndarray, pd.DataFrame]:
    embeddings_dir = Path(embeddings_dir)
    if run_id is None:
        candidates = sorted(embeddings_dir.glob("location_embeddings_*.npy"))
        if not candidates:
            raise FileNotFoundError(f"No location embedding matrices found in {embeddings_dir}")
        matrix_path = candidates[-1]
        run_id = matrix_path.stem.replace("location_embeddings_", "")
    else:
        matrix_path = embeddings_dir / f"location_embeddings_{run_id}.npy"

    parquet_meta_path = embeddings_dir / f"location_embeddings_{run_id}_metadata.parquet"
    csv_meta_path = embeddings_dir / f"location_embeddings_{run_id}_metadata.csv"
    embedding_matrix = np.load(matrix_path)
    if parquet_meta_path.exists():
        embedding_meta = pd.read_parquet(parquet_meta_path)
    elif csv_meta_path.exists():
        embedding_meta = pd.read_csv(csv_meta_path)
    else:
        raise FileNotFoundError(f"No metadata found for run_id={run_id}")
    return embedding_matrix, embedding_meta


def attach_embedding_refs(df_source: pd.DataFrame, embedding_meta: pd.DataFrame) -> pd.DataFrame:
    attached = df_source.copy()
    meta_cols = ["source_row_index", "embedding_row", "has_embedding", "custom_id"]
    available = [col for col in meta_cols if col in embedding_meta.columns]
    attached = attached.merge(
        embedding_meta[available],
        on="source_row_index",
        how="left",
        suffixes=("", "_embedding_meta"),
    )
    attached["has_embedding"] = attached["has_embedding"].fillna(False).astype(bool)
    return attached


def axis_columns(df: pd.DataFrame) -> list[str]:
    return [column for column in AXIS_DEFINITIONS if column in df.columns]


def get_rows_with_embeddings(df_source: pd.DataFrame) -> pd.DataFrame:
    return df_source[df_source["has_embedding"] == True].copy()


# --------------------------------------------------------------------------- #
# Venue subtype + chain detection (wrong_category / too_chain_like fixes)
# --------------------------------------------------------------------------- #

# Coarse venue taxonomy. The labeling sprint showed the dominant failure mode is
# crossing these boundaries (gastropub recommended for bistro seeds, bar for pub
# seeds), which no embedding/tag component can express — they all smear category.
VENUE_SUBTYPES = (
    "coffee_shop",
    "cafe",
    "bar",
    "pub",
    "club",
    "bistro",
    "restaurant",
    "fast_food",
    "bakery_dessert",
    "other",
)

# primary_type (Google taxonomy) -> subtype. Checked against the live catalog's
# top primary_type values; anything unmapped falls through to keyword rules.
_PRIMARY_TYPE_TO_SUBTYPE = {
    "coffee_shop": "coffee_shop",
    "cafe": "cafe",
    "internet_cafe": "cafe",
    "tea_house": "cafe",
    "brunch_restaurant": "cafe",
    "breakfast_restaurant": "cafe",
    "bar": "bar",
    "wine_bar": "bar",
    "cocktail_bar": "bar",
    "lounge_bar": "bar",
    "hookah_bar": "bar",
    "bar_and_grill": "bar",
    "pub": "pub",
    "gastropub": "pub",
    "brewpub": "pub",
    "sports_bar": "pub",
    "night_club": "club",
    "karaoke_bar": "club",
    "bistro": "bistro",
    "restaurant": "restaurant",
    "fast_food_restaurant": "fast_food",
    "sandwich_shop": "fast_food",
    "hamburger_restaurant": "fast_food",
    "bakery": "bakery_dessert",
    "dessert_shop": "bakery_dessert",
    "dessert_restaurant": "bakery_dessert",
    "ice_cream_shop": "bakery_dessert",
    "confectionery": "bakery_dessert",
    "patisserie": "bakery_dessert",
    "candy_store": "bakery_dessert",
    "chocolate_shop": "bakery_dessert",
}

# Keyword fallbacks over ai_place_type_summary / name, checked IN ORDER (more
# specific words first: "gastropub" must win over "pub" inside it, "pub" over "bar").
_SUBTYPE_KEYWORDS = (
    ("gastropub", "pub"),
    ("brewpub", "pub"),
    ("bistro", "bistro"),
    ("coffee", "coffee_shop"),
    ("espresso", "coffee_shop"),
    ("night club", "club"),
    ("nightclub", "club"),
    ("club", "club"),
    ("pub", "pub"),
    ("cocktail", "bar"),
    ("wine", "bar"),
    ("lounge", "bar"),
    ("bar", "bar"),
    ("bakery", "bakery_dessert"),
    ("patisserie", "bakery_dessert"),
    ("pastry", "bakery_dessert"),
    ("dessert", "bakery_dessert"),
    ("gelato", "bakery_dessert"),
    ("ice cream", "bakery_dessert"),
    ("fast food", "fast_food"),
    ("kebab", "fast_food"),
    ("shawarma", "fast_food"),
    ("pizzeria", "restaurant"),
    ("restaurant", "restaurant"),
    ("cafe", "cafe"),
    ("café", "cafe"),
    ("kiosk", "coffee_shop"),
)

# Symmetric relatedness between subtypes (diagonal = 1.0 implicitly; unlisted
# pairs = 0.0). Tuned to the boundaries the labels actually complained about:
# pub≠bar but related; bistro between cafe and restaurant; club next to bar.
_SUBTYPE_AFFINITY = {
    frozenset({"coffee_shop", "cafe"}): 0.8,
    frozenset({"coffee_shop", "bakery_dessert"}): 0.5,
    frozenset({"cafe", "bakery_dessert"}): 0.6,
    frozenset({"cafe", "bistro"}): 0.6,
    frozenset({"bistro", "restaurant"}): 0.6,
    frozenset({"cafe", "restaurant"}): 0.4,
    frozenset({"bar", "pub"}): 0.7,
    frozenset({"bar", "club"}): 0.6,
    frozenset({"pub", "club"}): 0.4,
    frozenset({"pub", "restaurant"}): 0.4,
    frozenset({"bar", "restaurant"}): 0.3,
    frozenset({"coffee_shop", "bistro"}): 0.4,
    frozenset({"fast_food", "restaurant"}): 0.4,
}


def subtype_affinity(a: str, b: str) -> float:
    """Relatedness of two venue subtypes in [0, 1]; unknown ('other') pairs -> 0."""
    if a == b and a in VENUE_SUBTYPES and a != "other":
        return 1.0
    return _SUBTYPE_AFFINITY.get(frozenset({a, b}), 0.0)


def derive_venue_subtype(row: pd.Series | dict) -> str:
    """Normalize a place into the coarse VENUE_SUBTYPES taxonomy.

    Resolution order: primary_type mapping -> keywords in ai_place_type_summary
    -> google `types` list -> keywords in the name -> 'other'.
    """
    getter = row.get if hasattr(row, "get") else lambda k, d=None: d
    primary = str(getter("primary_type") or "").strip().lower()
    if primary in _PRIMARY_TYPE_TO_SUBTYPE:
        return _PRIMARY_TYPE_TO_SUBTYPE[primary]

    summary = str(getter("ai_place_type_summary") or "").lower()
    for keyword, subtype in _SUBTYPE_KEYWORDS:
        if keyword in summary:
            return subtype

    for gtype in parse_listish(getter("types")):
        mapped = _PRIMARY_TYPE_TO_SUBTYPE.get(str(gtype).strip().lower())
        if mapped:
            return mapped

    name = str(getter("name") or "").lower()
    for keyword, subtype in _SUBTYPE_KEYWORDS:
        if keyword in name:
            return subtype
    return "other"


# Brands present in the Bucharest catalog plus global staples; prefix-matched on
# the normalized name. The duplicate-name count below catches the rest.
KNOWN_CHAIN_PREFIXES = (
    "starbucks",
    "5 to go",
    "ted's coffee",
    "teds coffee",
    "tucano coffee",
    "gloria jean",
    "mcdonald",
    "mc cafe",
    "mccafe",
    "kfc",
    "subway",
    "paul ",
    "salad box",
    "gregory's",
    "luca ",
    "narcoffee",
    "coffee 2 go",
)


def _normalize_chain_name(name: Any) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def compute_is_chain(df_source: pd.DataFrame, min_locations: int = 3) -> pd.Series:
    """Chain flag: known brand prefix OR >= min_locations same-named places.

    v1 heuristic — exact normalized-name duplicates undercount branches with
    location suffixes; the brand prefix list covers the big offenders the
    feedback flagged (`too_chain_like`).
    """
    names = df_source.get("name", pd.Series("", index=df_source.index)).map(_normalize_chain_name)
    counts = names.map(names.value_counts())
    by_count = counts >= min_locations
    by_brand = names.map(lambda n: any(n.startswith(prefix.strip()) or n == prefix.strip() for prefix in KNOWN_CHAIN_PREFIXES))
    return (by_count | by_brand).fillna(False).astype(bool)


def compute_quality_score(df_source: pd.DataFrame) -> pd.Series:
    score = pd.Series(0.0, index=df_source.index)
    if "map_visibility_score" in df_source.columns:
        score += df_source["map_visibility_score"].fillna(0).clip(0, 100) / 100 * 0.60
    if "google_rating" in df_source.columns:
        score += ((df_source["google_rating"].fillna(0).clip(0, 5) / 5) * 0.25)
    if "google_user_rating_count" in df_source.columns:
        score += (np.log1p(df_source["google_user_rating_count"].fillna(0)) / np.log1p(max(df_source["google_user_rating_count"].fillna(0).max(), 1))) * 0.10
    if "ai_confidence" in df_source.columns:
        confidence_map = {"high": 1.0, "medium": 0.65, "low": 0.20}
        score += df_source["ai_confidence"].map(confidence_map).fillna(0.20) * 0.05
    return score.clip(0, 1)


def compute_quality_score_v2(
    df_source: pd.DataFrame,
    shrinkage_prior: float = 25.0,
    catalog_mean: float | None = None,
) -> pd.Series:
    """Bayesian-shrunk rating in [0, 1] (Task 2.2).

    ``q = (v/(v+m))*R + (m/(v+m))*C`` where R = rating, v = review count,
    C = catalog mean rating, m = shrinkage prior. A 5.0 place with 2 reviews is
    pulled toward C; a 4.5 place with 300 reviews keeps most of its rating.
    Popularity (review count) only governs how much we trust the rating — it is
    not added as its own term. Falls back to 0.5 when ratings are unavailable.
    """
    if "google_rating" not in df_source.columns:
        return pd.Series(0.5, index=df_source.index)
    m = float(shrinkage_prior)
    rating = pd.to_numeric(df_source["google_rating"], errors="coerce").clip(0, 5)
    if catalog_mean is None:
        catalog_mean = float(rating.mean()) if rating.notna().any() else 0.0
    rating = rating.fillna(catalog_mean)
    if "google_user_rating_count" in df_source.columns:
        votes = pd.to_numeric(df_source["google_user_rating_count"], errors="coerce").fillna(0).clip(lower=0)
    else:
        votes = pd.Series(0.0, index=df_source.index)
    denom = votes + m
    shrunk = (votes / denom) * rating + (m / denom) * catalog_mean
    return (shrunk / 5.0).clip(0, 1)


def build_candidate_dataset(
    df_source: pd.DataFrame,
    min_map_visibility_score: float = 20.0,
    exclude_low_confidence: bool = True,
) -> pd.DataFrame:
    candidates = get_rows_with_embeddings(df_source)
    if exclude_low_confidence and "ai_confidence" in candidates.columns:
        candidates = candidates[candidates["ai_confidence"].fillna("").str.lower() != "low"]
    if "map_visibility_score" in candidates.columns:
        candidates = candidates[candidates["map_visibility_score"].fillna(0) >= min_map_visibility_score]
    candidates = candidates.copy()
    candidates["quality_score"] = compute_quality_score(candidates)
    return candidates


def compact_embedding_matrix_for_rows(
    df_source: pd.DataFrame,
    embedding_matrix: np.ndarray,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Return a dataframe whose embedding_row points into a compact matrix."""
    compact = df_source.copy()
    compact["full_embedding_row"] = compact["embedding_row"].astype(int)
    compact_matrix = embedding_matrix[compact["full_embedding_row"].to_numpy()]
    compact["embedding_row"] = np.arange(len(compact), dtype=int)
    return compact, compact_matrix


def _matrix_for_rows(df_rows: pd.DataFrame, embedding_matrix: np.ndarray) -> np.ndarray:
    return embedding_matrix[df_rows["embedding_row"].astype(int).to_numpy()]


def sample_embeddings_for_clustering(
    embedding_matrix: np.ndarray,
    max_sample_size: int = 2_500,
    random_state: int = RANDOM_STATE,
) -> tuple[np.ndarray, np.ndarray]:
    n_rows = len(embedding_matrix)
    if n_rows <= max_sample_size:
        rows = np.arange(n_rows)
    else:
        rng = np.random.default_rng(random_state)
        rows = np.sort(rng.choice(n_rows, size=max_sample_size, replace=False))
    return rows, embedding_matrix[rows]


def evaluate_kmeans_clusters(
    embedding_matrix: np.ndarray,
    k_values=range(3, 13),
    sample_size: int = 2_500,
    random_state: int = RANDOM_STATE,
) -> pd.DataFrame:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import normalize

    _, sample_matrix = sample_embeddings_for_clustering(embedding_matrix, sample_size, random_state)
    sample_matrix_norm = normalize(sample_matrix)
    results = []
    for k in k_values:
        if k >= len(sample_matrix_norm):
            continue
        model = KMeans(n_clusters=k, random_state=random_state, n_init=10)
        labels = model.fit_predict(sample_matrix_norm)
        score = silhouette_score(sample_matrix_norm, labels, metric="cosine")
        results.append({"k": k, "silhouette_cosine": score})
    return pd.DataFrame(results).sort_values("silhouette_cosine", ascending=False).reset_index(drop=True)


def fit_global_location_clusters(
    embedding_matrix: np.ndarray,
    n_clusters: int,
    random_state: int = RANDOM_STATE,
):
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import normalize

    model = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10)
    labels = model.fit_predict(normalize(embedding_matrix))
    return model, labels


def add_global_clusters(df_source: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    clustered = df_source.copy()
    clustered["global_cluster"] = labels.astype(int)
    return clustered


def _top_values(series: pd.Series, n: int = 8) -> str:
    counter = Counter()
    for value in series.dropna():
        if isinstance(value, list):
            counter.update(value)
        else:
            counter.update(parse_listish(value))
    return ", ".join(f"{key} ({count})" for key, count in counter.most_common(n))


def summarize_global_clusters(df_clustered: pd.DataFrame, examples_per_cluster: int = 5) -> pd.DataFrame:
    rows = []
    for cluster_id, group in df_clustered.groupby("global_cluster"):
        axis_medians = {f"median_{col.replace('axis_', '')}": group[col].median() for col in axis_columns(group)}
        examples = group.sort_values("map_visibility_score", ascending=False)["name"].head(examples_per_cluster).tolist()
        rows.append(
            {
                "global_cluster": cluster_id,
                "rows": len(group),
                "top_primary_types": _top_values(group["primary_type"], n=6),
                "top_tags": _top_values(group["recommendation_tags"], n=10) if "recommendation_tags" in group else "",
                "examples": "; ".join(examples),
                **axis_medians,
            }
        )
    return pd.DataFrame(rows).sort_values("global_cluster").reset_index(drop=True)


def create_embedding_projection(
    df_source: pd.DataFrame,
    embedding_matrix: np.ndarray,
    max_points: int | None = None,
    random_state: int = RANDOM_STATE,
) -> pd.DataFrame:
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import normalize

    embedded = get_rows_with_embeddings(df_source)
    if max_points is not None and len(embedded) > max_points:
        embedded = embedded.sample(n=max_points, random_state=random_state).sort_index()
    matrix_rows = embedded["embedding_row"].astype(int).to_numpy()
    projection = PCA(n_components=2, random_state=random_state).fit_transform(normalize(embedding_matrix[matrix_rows]))
    viz_df = embedded.copy()
    viz_df["x"] = projection[:, 0]
    viz_df["y"] = projection[:, 1]
    viz_df["location_label"] = (
        viz_df["name"].astype(str)
        + " | " + viz_df["ai_place_type_summary"].fillna(viz_df["primary_type"]).astype(str)
        + " | " + viz_df["ai_tags_csv"].fillna("").astype(str).str.slice(0, 120)
    )
    return viz_df


def plot_global_clusters(viz_df: pd.DataFrame):
    import plotly.express as px

    fig = px.scatter(
        viz_df,
        x="x",
        y="y",
        color="global_cluster" if "global_cluster" in viz_df.columns else None,
        hover_name="name",
        hover_data=[
            col
            for col in ["primary_type", "ai_place_type_summary", "ai_tags_csv", "google_rating", "map_visibility_score"]
            if col in viz_df.columns
        ],
        title="AI location embedding clusters (PCA projection)",
        opacity=0.75,
        height=720,
    )
    fig.update_traces(marker={"size": 7})
    return fig


def find_locations_by_name(query: str, df_source: pd.DataFrame, limit: int = 100) -> pd.DataFrame:
    mask = df_source["name"].astype(str).str.contains(query, case=False, na=False)
    cols = [
        "place_id",
        "name",
        "primary_type",
        "ai_place_type_summary",
        "ai_card_summary",
        "ai_tags_csv",
        "google_rating",
        "map_visibility_score",
        "global_cluster",
    ]
    return df_source.loc[mask, [col for col in cols if col in df_source.columns]].head(limit)


def get_favorite_rows(
    df_source: pd.DataFrame,
    favorite_place_ids: list[str] | None = None,
    favorite_indices: list[int] | None = None,
) -> pd.DataFrame:
    if favorite_place_ids is not None:
        favorites = df_source[df_source["place_id"].isin(favorite_place_ids)].copy()
    elif favorite_indices is not None:
        favorites = df_source.loc[list(favorite_indices)].copy()
    else:
        raise ValueError("Pass favorite_place_ids or favorite_indices")
    favorites = favorites[favorites["has_embedding"] == True].copy()
    if len(favorites) < 2:
        raise ValueError("Choose at least 2 favorite locations with embeddings")
    return favorites


def _agglomerative_cosine(n_clusters: int):
    from sklearn.cluster import AgglomerativeClustering

    try:
        return AgglomerativeClustering(n_clusters=n_clusters, metric="cosine", linkage="average")
    except TypeError:
        return AgglomerativeClustering(n_clusters=n_clusters, affinity="cosine", linkage="average")


def choose_profile_cluster_count(
    favorite_vectors: np.ndarray,
    max_clusters: int = 4,
    min_silhouette: float = 0.03,
) -> int:
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import normalize

    n = len(favorite_vectors)
    if n < 4:
        return 1
    max_k = min(max_clusters, n - 1)
    vectors_norm = normalize(favorite_vectors)
    scores = []
    for k in range(2, max_k + 1):
        labels = _agglomerative_cosine(k).fit_predict(vectors_norm)
        if len(set(labels)) < 2:
            continue
        scores.append((k, silhouette_score(vectors_norm, labels, metric="cosine")))
    if not scores:
        return 1
    best_k, best_score = max(scores, key=lambda item: item[1])
    return best_k if best_score >= min_silhouette else 1


def cluster_user_favorites(
    favorites: pd.DataFrame,
    embedding_matrix: np.ndarray,
    max_profile_clusters: int = 4,
) -> pd.DataFrame:
    from sklearn.preprocessing import normalize

    favorite_vectors = _matrix_for_rows(favorites, embedding_matrix)
    n_clusters = choose_profile_cluster_count(favorite_vectors, max_clusters=max_profile_clusters)
    if n_clusters == 1:
        labels = np.zeros(len(favorites), dtype=int)
    else:
        labels = _agglomerative_cosine(n_clusters).fit_predict(normalize(favorite_vectors))
    result = favorites.copy()
    result["profile_cluster"] = labels.astype(int)
    return result


def axis_similarity(candidate_rows: pd.DataFrame, favorite_group: pd.DataFrame) -> pd.Series:
    cols = axis_columns(candidate_rows)
    if not cols:
        return pd.Series(0.5, index=candidate_rows.index)
    centroid = favorite_group[cols].astype(float).mean()
    distances = candidate_rows[cols].astype(float).sub(centroid, axis=1).abs().mean(axis=1)
    return (1 - distances / 100).clip(0, 1)


def tag_overlap_score(candidate_rows: pd.DataFrame, favorite_group: pd.DataFrame) -> pd.Series:
    favorite_tags = set()
    for tags in favorite_group.get("recommendation_tags", []):
        if isinstance(tags, list):
            favorite_tags.update(tags)
        else:
            favorite_tags.update(parse_listish(tags))
    if not favorite_tags:
        return pd.Series(0.0, index=candidate_rows.index)

    scores = []
    for tags in candidate_rows.get("recommendation_tags", [[] for _ in range(len(candidate_rows))]):
        candidate_tags = set(tags if isinstance(tags, list) else parse_listish(tags))
        if not candidate_tags:
            scores.append(0.0)
        else:
            scores.append(len(candidate_tags & favorite_tags) / len(candidate_tags | favorite_tags))
    return pd.Series(scores, index=candidate_rows.index)


def price_match_score(candidate_rows: pd.DataFrame, favorite_group: pd.DataFrame) -> pd.Series:
    if "axis_cheap_expensive" not in candidate_rows.columns:
        return pd.Series(0.5, index=candidate_rows.index)
    centroid = favorite_group["axis_cheap_expensive"].astype(float).mean()
    distances = candidate_rows["axis_cheap_expensive"].astype(float).sub(centroid).abs()
    return (1 - distances / 100).clip(0, 1)


HYBRID_TEXT_WEIGHTS = {
    "semantic_similarity": 0.72,
    "tag_overlap": 0.10,
    "axis_similarity": 0.08,
    "quality_score": 0.07,
    "price_match": 0.03,
}
PURE_TEXT_WEIGHTS = {
    "semantic_similarity": 1.0,
    "tag_overlap": 0.0,
    "axis_similarity": 0.0,
    "quality_score": 0.0,
    "price_match": 0.0,
}


def _compact_text_recommender(df_candidates: pd.DataFrame, embedding_matrix: np.ndarray, weights: dict[str, float]):
    """Build a text-only ``LocationRecommender`` scoped to exactly ``df_candidates``.

    The notebook flow keeps ``embedding_row`` pointing into a full catalog matrix
    while ``df_candidates`` may be a filtered subset. The recommender's scoring
    path expects a self-consistent (locations, matrix, metadata) triple, so we
    compact the matrix to the rows referenced by ``df_candidates`` and re-index
    ``embedding_row`` positionally. The original ``embedding_row`` is preserved by
    the caller for downstream plotting.
    """
    from .backend_recommender import (
        LocationRecommender,
        RecommenderConfig,
    )

    locations = df_candidates.reset_index(drop=True).copy()
    # _prepare_locations requires these columns; supply neutral defaults if absent.
    if "name" not in locations.columns:
        locations["name"] = locations["place_id"].astype(str)
    if "ai_tags_json" not in locations.columns:
        locations["ai_tags_json"] = "[]"
    if "map_visibility_score" not in locations.columns:
        locations["map_visibility_score"] = 100.0
    if "ai_confidence" not in locations.columns:
        locations["ai_confidence"] = "high"

    rows = locations["embedding_row"].astype(int).to_numpy()
    compact_matrix = np.asarray(embedding_matrix)[rows]
    # metadata owns these columns; drop them from locations to avoid a merge clash.
    locations = locations.drop(columns=[c for c in ("embedding_row", "has_embedding") if c in locations.columns])
    metadata = pd.DataFrame(
        {
            "place_id": locations["place_id"].to_numpy(),
            "embedding_row": np.arange(len(locations)),
            "has_embedding": True,
        }
    )
    cfg = RecommenderConfig(
        min_saved_for_personalization=1,
        min_map_visibility_score=0.0,
        exclude_low_confidence=False,
        weights=weights,
    )
    return LocationRecommender(locations, compact_matrix, metadata, config=cfg), cfg


def recommend_for_profile_clusters(
    df_candidates: pd.DataFrame,
    embedding_matrix: np.ndarray,
    favorites: pd.DataFrame,
    total_n: int = 100,
    top_n_per_cluster: int | None = None,
    mode: str = "hybrid",
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Notebook-facing recommender — a thin wrapper over the single scoring path.

    Delegates to ``LocationRecommender._score_candidates_for_profile`` (via
    ``_personalized_recommend``) so the notebook and the backend share one
    implementation. ``favorites`` must carry a ``profile_cluster`` column; those
    labels are honored as profile ids (favorites are not re-clustered here).

    ``mode="pure"`` is the ``PURE_TEXT_WEIGHTS`` preset (semantic only);
    ``mode="hybrid"`` uses ``weights`` (default ``HYBRID_TEXT_WEIGHTS``).
    ``top_n_per_cluster`` is accepted for backward compatibility but no longer
    used: ranking is global, matching the backend.
    """
    if mode == "pure":
        score_weights = dict(PURE_TEXT_WEIGHTS)
    elif mode == "hybrid":
        score_weights = dict(weights) if weights is not None else dict(HYBRID_TEXT_WEIGHTS)
    else:
        raise ValueError("mode must be 'pure' or 'hybrid'")

    candidates = df_candidates.drop(index=favorites.index, errors="ignore")
    if candidates.empty or favorites.empty:
        return pd.DataFrame()

    original_embedding_row = (
        df_candidates.drop_duplicates("place_id").set_index("place_id")["embedding_row"]
    )
    recommender, cfg = _compact_text_recommender(df_candidates, embedding_matrix, score_weights)

    favorite_ids = favorites["place_id"].tolist()
    seed_rows = []
    for _, fav in favorites.iterrows():
        idx = recommender.place_id_to_index.get(fav["place_id"])
        if idx is None:
            continue
        seed_row = recommender.locations.iloc[idx].to_dict()
        seed_row["profile_id"] = int(fav["profile_cluster"])
        seed_row["signal_weight"] = 1.0
        seed_row["source_list"] = "seed"
        seed_rows.append(seed_row)
    if not seed_rows:
        return pd.DataFrame()
    seed_df = pd.DataFrame(seed_rows)

    candidate_pool = recommender._candidate_pool(cfg)
    candidate_pool = candidate_pool[~candidate_pool["place_id"].isin(favorite_ids)].copy()
    payloads = recommender._personalized_recommend(seed_df, candidate_pool, total_n, cfg, debug=False)
    if not payloads:
        return pd.DataFrame()

    ordered_ids = [p["place_id"] for p in payloads]
    components = pd.DataFrame([p["score_components"] for p in payloads])
    result = recommender.locations.set_index("place_id").loc[ordered_ids].reset_index()
    result["recommendation_score"] = [p["score"] for p in payloads]
    result["profile_cluster"] = [int(p["profile_id"]) for p in payloads]
    result["similarity"] = components["similarity"].to_numpy()
    result["similarity_norm"] = components["semantic_similarity_norm"].to_numpy()
    result["tag_overlap"] = components["tag_overlap"].to_numpy()
    result["axis_similarity"] = components["axis_similarity"].to_numpy()
    result["quality_score"] = components["quality_score"].to_numpy()
    result["price_match"] = components["price_match"].to_numpy()
    result["recommendation_rank"] = np.arange(1, len(result) + 1)

    favorites_by_cluster = favorites.groupby("profile_cluster")["name"].apply(
        lambda names: "; ".join(names.astype(str).head(8))
    )
    result["based_on_favorites"] = result["profile_cluster"].map(favorites_by_cluster)
    # Restore the catalog-indexed embedding_row so downstream plotting still works.
    result["embedding_row"] = result["place_id"].map(original_embedding_row).astype(int)
    return result.sort_values(["profile_cluster", "recommendation_rank"]).reset_index(drop=True)


def display_recommendation_columns(df_recommendations: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "recommendation_rank",
        "profile_cluster",
        "recommendation_score",
        "similarity",
        "tag_overlap",
        "axis_similarity",
        "quality_score",
        "price_match",
        "name",
        "ai_place_type_summary",
        "ai_card_summary",
        "ai_tags_csv",
        "google_rating",
        "google_user_rating_count",
        "map_visibility_score",
        "based_on_favorites",
        "place_id",
    ]
    return df_recommendations[[col for col in cols if col in df_recommendations.columns]]


def plot_profile_recommendations(
    df_source: pd.DataFrame,
    embedding_matrix: np.ndarray,
    favorites: pd.DataFrame,
    recommendations: pd.DataFrame,
    background_sample: int = 2_000,
    random_state: int = RANDOM_STATE,
):
    import plotly.express as px
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import normalize

    background = get_rows_with_embeddings(df_source)
    if len(background) > background_sample:
        background = background.sample(n=background_sample, random_state=random_state)
    background = background.copy()
    background["point_type"] = "background"
    background["profile_cluster"] = np.nan

    fav_plot = favorites.copy()
    fav_plot["point_type"] = "favorites"
    rec_plot = recommendations.copy()
    rec_plot["point_type"] = "recommendations"

    plot_df = pd.concat([background, fav_plot, rec_plot], sort=False)
    matrix_rows = plot_df["embedding_row"].astype(int).to_numpy()
    projection = PCA(n_components=2, random_state=random_state).fit_transform(normalize(embedding_matrix[matrix_rows]))
    plot_df["x"] = projection[:, 0]
    plot_df["y"] = projection[:, 1]

    fig = px.scatter(
        plot_df,
        x="x",
        y="y",
        color="point_type",
        symbol="profile_cluster",
        hover_name="name",
        hover_data=[
            col
            for col in ["profile_cluster", "ai_place_type_summary", "ai_tags_csv", "similarity", "recommendation_score", "map_visibility_score"]
            if col in plot_df.columns
        ],
        title="Favorites and recommendations in embedding space",
        opacity=0.72,
        height=760,
    )
    fig.update_traces(marker={"size": 7})
    return fig


def save_recommender_exports(
    df_model_all: pd.DataFrame,
    df_candidates_default: pd.DataFrame,
    pure_recommendations: pd.DataFrame | None = None,
    hybrid_recommendations: pd.DataFrame | None = None,
    export_dir: str | Path = EXPORT_DIR,
    run_label: str | None = None,
) -> dict[str, Path]:
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    run_label = run_label or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    paths = {
        "model_all": export_dir / f"df_model_all_{run_label}.csv",
        "candidates_default": export_dir / f"df_candidates_default_{run_label}.csv",
    }
    df_model_all.to_csv(paths["model_all"], index=False)
    df_candidates_default.to_csv(paths["candidates_default"], index=False)
    if pure_recommendations is not None:
        paths["pure_recommendations"] = export_dir / f"pure_recommendations_{run_label}.csv"
        pure_recommendations.to_csv(paths["pure_recommendations"], index=False)
    if hybrid_recommendations is not None:
        paths["hybrid_recommendations"] = export_dir / f"hybrid_recommendations_{run_label}.csv"
        hybrid_recommendations.to_csv(paths["hybrid_recommendations"], index=False)
    return paths


def run_recommender_smoke_test(
    df_candidates: pd.DataFrame,
    embedding_matrix: np.ndarray,
    n_favorites: int = 12,
    total_n: int = 50,
    random_state: int = 7,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if len(df_candidates) < n_favorites + total_n:
        raise ValueError("Not enough candidate rows for smoke test")
    favorite_indices = df_candidates.sample(n=n_favorites, random_state=random_state).index.tolist()
    favorites = get_favorite_rows(df_candidates, favorite_indices=favorite_indices)
    favorites = cluster_user_favorites(favorites, embedding_matrix, max_profile_clusters=4)
    pure_recs = recommend_for_profile_clusters(df_candidates, embedding_matrix, favorites, total_n=total_n, mode="pure")
    hybrid_recs = recommend_for_profile_clusters(df_candidates, embedding_matrix, favorites, total_n=total_n, mode="hybrid")
    favorite_place_ids = set(favorites["place_id"])
    if favorite_place_ids & set(pure_recs["place_id"]):
        raise AssertionError("Pure recommendations contain favorites")
    if favorite_place_ids & set(hybrid_recs["place_id"]):
        raise AssertionError("Hybrid recommendations contain favorites")
    if len(pure_recs) < min(20, total_n) or len(hybrid_recs) < min(20, total_n):
        raise AssertionError("Smoke test returned too few recommendations")
    return favorites, pure_recs, hybrid_recs
