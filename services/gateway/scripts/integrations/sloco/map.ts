import { writeFile } from "node:fs/promises";
import { stringify } from "csv-stringify/sync";
import {
  optionalText,
  parseCliArgs,
  parseOptionalNumber,
  parseRequiredNumber,
  readCsvRows,
  requiredText,
  UNKNOWN_TEXT,
  type JsonObject,
  type RawCsvRow
} from "../_shared/place-record.js";
import { parsePythonLiteral } from "../_shared/python-literal.js";

// Source: the data team's AI catalog (e.g. locations_combined_food_ttd.csv).
// Maps one enriched venue row -> the v2 `public.places` import shape, keyed by
// the numeric Google `cid` in `place_id` -> `source_id`. Self-contained: does not
// share the base canonical record used by the tripadvisor/osm mappers.

const SOURCE = "sloco_ai";

const CITY_COUNTRY: Record<string, string> = {
  bucharest: "romania",
  tbilisi: "georgia"
};

// Output columns = the `public.places` columns this catalog populates. jsonb
// columns are JSON strings; text[] columns are Postgres array literals ({a,b}).
const SLOCO_COLUMNS = [
  "source",
  "source_id",
  "name",
  "country",
  "city",
  "category",
  "latitude",
  "longitude",
  "primary_type",
  "types",
  "google_rating",
  "google_user_rating_count",
  "apify_review_count",
  "apify_rating_avg",
  "rating_count_for_score",
  "bayesian_rating",
  "rating_score_0_100",
  "popularity_score_0_100",
  "rating_confidence_0_100",
  "price_level",
  "price_min_ron",
  "price_max_ron",
  "ai_card_summary",
  "ai_place_type_summary",
  "ai_vibe",
  "ai_what_to_expect",
  "ai_food_and_drinks",
  "ai_price",
  "ai_service",
  "ai_the_move",
  "ai_watch_out",
  "ai_tags",
  "ai_tags_json",
  "ai_confidence",
  "axis_quiet_lively",
  "axis_work_social",
  "axis_day_night",
  "axis_casual_premium",
  "axis_drinks_food",
  "axis_local_tourist",
  "axis_cheap_expensive",
  "axis_traditional_experimental",
  "map_visibility_score",
  "map_visibility_rank",
  "map_min_zoom_global",
  "serves",
  "features",
  "attributes",
  "raw"
] as const;

type SlocoColumn = (typeof SLOCO_COLUMNS)[number];
type SlocoCsvRow = Record<SlocoColumn, string>;

// Columns already mapped to a dedicated place column; everything else goes to `attributes`.
const MAPPED_SOURCE_KEYS = new Set([
  "place_id",
  "name",
  "city",
  "primary_type",
  "types",
  "google_rating",
  "google_user_rating_count",
  "apify_review_count",
  "apify_rating_avg",
  "rating_count_for_score",
  "bayesian_rating",
  "rating_score_0_100",
  "popularity_score_0_100",
  "rating_confidence_0_100",
  "price_level",
  "price_min_ron",
  "price_max_ron",
  "ai_card_summary",
  "ai_place_type_summary",
  "ai_vibe",
  "ai_what_to_expect",
  "ai_food_and_drinks",
  "ai_price",
  "ai_service",
  "ai_the_move",
  "ai_watch_out",
  "ai_tags",
  "ai_tags_json",
  "ai_confidence",
  "axis_quiet_lively",
  "axis_work_social",
  "axis_day_night",
  "axis_casual_premium",
  "axis_drinks_food",
  "axis_local_tourist",
  "axis_cheap_expensive",
  "axis_traditional_experimental",
  "map_visibility_score",
  "map_visibility_rank",
  "map_min_zoom_global",
  "serves",
  "features",
  "latitude",
  "longitude"
]);

async function main() {
  const { inputPath, outputPath } = parseCliArgs(process.argv.slice(2));
  const rows = await readCsvRows(inputPath);
  const records = rows.map(mapSlocoRow);

  const csv = stringify(records, { header: true, columns: [...SLOCO_COLUMNS] });

  if (outputPath) {
    await writeFile(outputPath, csv, "utf8");
    return;
  }

  process.stdout.write(csv);
}

function mapSlocoRow(row: RawCsvRow): SlocoCsvRow {
  const cityRaw = optionalText(row.city);
  const country = cityRaw ? (CITY_COUNTRY[cityRaw.toLowerCase()] ?? "others") : "others";
  const category =
    optionalText(row.primary_type) ?? optionalText(row.theme_group) ?? UNKNOWN_TEXT;

  return {
    source: SOURCE,
    source_id: requiredText(row.place_id),
    name: requiredText(row.name),
    country,
    city: cityRaw ?? "others",
    category,
    latitude: numberCell(parseRequiredNumber(row.latitude, "latitude")),
    longitude: numberCell(parseRequiredNumber(row.longitude, "longitude")),
    primary_type: textCell(row.primary_type),
    types: pgTextArray(parseStringList(row.types)),
    google_rating: optionalNumberCell(row.google_rating),
    google_user_rating_count: optionalNumberCell(row.google_user_rating_count),
    apify_review_count: optionalNumberCell(row.apify_review_count),
    apify_rating_avg: optionalNumberCell(row.apify_rating_avg),
    rating_count_for_score: optionalNumberCell(row.rating_count_for_score),
    bayesian_rating: optionalNumberCell(row.bayesian_rating),
    rating_score_0_100: optionalNumberCell(row.rating_score_0_100),
    popularity_score_0_100: optionalNumberCell(row.popularity_score_0_100),
    rating_confidence_0_100: optionalNumberCell(row.rating_confidence_0_100),
    price_level: optionalNumberCell(row.price_level),
    price_min_ron: optionalNumberCell(row.price_min_ron),
    price_max_ron: optionalNumberCell(row.price_max_ron),
    ai_card_summary: textCell(row.ai_card_summary),
    ai_place_type_summary: textCell(row.ai_place_type_summary),
    ai_vibe: textCell(row.ai_vibe),
    ai_what_to_expect: textCell(row.ai_what_to_expect),
    ai_food_and_drinks: textCell(row.ai_food_and_drinks),
    ai_price: textCell(row.ai_price),
    ai_service: textCell(row.ai_service),
    ai_the_move: textCell(row.ai_the_move),
    ai_watch_out: textCell(row.ai_watch_out),
    ai_tags: pgTextArray(parseStringList(row.ai_tags)),
    ai_tags_json: JSON.stringify(parseStringList(row.ai_tags_json ?? row.ai_tags)),
    ai_confidence: numericOrNullCell(parseAiConfidence(row.ai_confidence)),
    axis_quiet_lively: optionalNumberCell(row.axis_quiet_lively),
    axis_work_social: optionalNumberCell(row.axis_work_social),
    axis_day_night: optionalNumberCell(row.axis_day_night),
    axis_casual_premium: optionalNumberCell(row.axis_casual_premium),
    axis_drinks_food: optionalNumberCell(row.axis_drinks_food),
    axis_local_tourist: optionalNumberCell(row.axis_local_tourist),
    axis_cheap_expensive: optionalNumberCell(row.axis_cheap_expensive),
    axis_traditional_experimental: optionalNumberCell(row.axis_traditional_experimental),
    map_visibility_score: numberCell(parseOptionalNumber(row.map_visibility_score) ?? 0),
    map_visibility_rank: optionalNumberCell(row.map_visibility_rank),
    map_min_zoom_global: optionalNumberCell(row.map_min_zoom_global),
    serves: toJsonb(row.serves, "[]"),
    features: toJsonb(row.features, "{}"),
    attributes: JSON.stringify(buildAttributes(row)),
    raw: JSON.stringify(compactObject(row))
  };
}

function buildAttributes(row: RawCsvRow): JsonObject {
  const attributes: JsonObject = {};

  for (const [key, value] of Object.entries(row)) {
    if (MAPPED_SOURCE_KEYS.has(key)) {
      continue;
    }

    const normalized = value?.trim();

    if (normalized) {
      attributes[key] = normalized;
    }
  }

  return attributes;
}

function compactObject(row: RawCsvRow): JsonObject {
  const result: JsonObject = {};

  for (const [key, value] of Object.entries(row)) {
    const normalized = value?.trim();

    if (normalized) {
      result[key] = normalized;
    }
  }

  return result;
}

// --- cell formatters (empty string imports as SQL NULL) ------------------------

function textCell(value: string | undefined): string {
  return optionalText(value) ?? "";
}

function numberCell(value: number): string {
  return String(value);
}

function optionalNumberCell(value: string | undefined): string {
  const parsed = parseOptionalNumber(value);
  return parsed === null ? "" : String(parsed);
}

function numericOrNullCell(value: number | null): string {
  return value === null ? "" : String(value);
}

// analyst `ai_confidence` is categorical (high/medium/low) but the column is
// numeric; map it, and pass through if it is already a number.
function parseAiConfidence(value: string | undefined): number | null {
  const normalized = value?.trim().toLowerCase();

  if (!normalized) {
    return null;
  }

  const asNumber = Number(normalized);

  if (Number.isFinite(asNumber)) {
    return asNumber;
  }

  const scale: Record<string, number> = { high: 1, medium: 0.5, low: 0 };
  return scale[normalized] ?? null;
}

function parseStringList(value: string | undefined): string[] {
  const normalized = value?.trim();

  if (!normalized) {
    return [];
  }

  let parsed: unknown;

  try {
    parsed = parsePythonLiteral(normalized);
  } catch {
    try {
      parsed = JSON.parse(normalized);
    } catch {
      return [];
    }
  }

  if (!Array.isArray(parsed)) {
    return [];
  }

  return parsed
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

// Postgres text[] literal, e.g. {"cozy","specialty coffee"}.
function pgTextArray(items: string[]): string {
  if (items.length === 0) {
    return "{}";
  }

  const escaped = items.map(
    (item) => `"${item.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`
  );

  return `{${escaped.join(",")}}`;
}

function toJsonb(value: string | undefined, fallback: string): string {
  const normalized = value?.trim();

  if (!normalized) {
    return fallback;
  }

  let parsed: unknown;

  try {
    parsed = parsePythonLiteral(normalized);
  } catch {
    try {
      parsed = JSON.parse(normalized);
    } catch {
      return fallback;
    }
  }

  return JSON.stringify(parsed);
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
