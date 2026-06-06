import { readFile, writeFile } from "node:fs/promises";
import { parse } from "csv-parse/sync";
import { stringify } from "csv-stringify/sync";

export const UNKNOWN_TEXT = "others";

export const PLACE_CSV_COLUMNS = [
  "source",
  "source_id",
  "name",
  "country",
  "city",
  "category",
  "latitude",
  "longitude",
  "rating",
  "price_level",
  "reviews_count",
  "embedding_text",
  "attributes",
  "raw",
  "fetched_at"
] as const;

export type PlaceCsvColumn = (typeof PLACE_CSV_COLUMNS)[number];

export type JsonObject = Record<string, unknown>;

export type CanonicalPlaceRecord = {
  source: string;
  source_id: string;
  name: string;
  country: string;
  city: string;
  category: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_level: number | null;
  reviews_count: number | null;
  embedding_text: string | null;
  attributes: JsonObject;
  raw: JsonObject;
  fetched_at: string | null;
};

export type RawCsvRow = Record<string, string | undefined>;

export async function readCsvRows(filePath: string) {
  const content = await readFile(filePath, "utf8");

  return parse(content, {
    bom: true,
    columns: true,
    skip_empty_lines: true,
    trim: false
  }) as RawCsvRow[];
}

export async function writePlacesCsv(
  records: CanonicalPlaceRecord[],
  outputPath?: string
) {
  const csv = stringify(records.map(toCsvRow), {
    header: true,
    columns: PLACE_CSV_COLUMNS
  });

  if (outputPath) {
    await writeFile(outputPath, csv, "utf8");
    return;
  }

  process.stdout.write(csv);
}

export function parseCliArgs(argv: string[]) {
  const [inputPath, ...rest] = argv;

  if (!inputPath) {
    throw new Error("Usage: pnpm tsx <mapper> <input.csv> [--out output.csv]");
  }

  const outIndex = rest.indexOf("--out");
  const outputPath = outIndex >= 0 ? rest[outIndex + 1] : undefined;

  if (outIndex >= 0 && !outputPath) {
    throw new Error("Missing output path after --out");
  }

  return {
    inputPath,
    outputPath
  };
}

export function requiredText(value: string | undefined, fallback = UNKNOWN_TEXT) {
  const normalized = value?.trim();
  return normalized && normalized.length > 0 ? normalized : fallback;
}

export function optionalText(value: string | undefined) {
  const normalized = value?.trim();
  return normalized && normalized.length > 0 ? normalized : null;
}

export function parseOptionalNumber(value: string | undefined) {
  const normalized = value?.trim();

  if (!normalized) {
    return null;
  }

  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseRequiredNumber(value: string | undefined, fieldName: string) {
  const parsed = parseOptionalNumber(value);

  if (parsed === null) {
    throw new Error(`Missing or invalid numeric field: ${fieldName}`);
  }

  return parsed;
}

function toCsvRow(record: CanonicalPlaceRecord): Record<PlaceCsvColumn, string> {
  return {
    source: record.source,
    source_id: record.source_id,
    name: record.name,
    country: record.country,
    city: record.city,
    category: record.category,
    latitude: String(record.latitude),
    longitude: String(record.longitude),
    rating: nullableNumber(record.rating),
    price_level: nullableNumber(record.price_level),
    reviews_count: nullableNumber(record.reviews_count),
    embedding_text: record.embedding_text ?? "",
    attributes: JSON.stringify(record.attributes),
    raw: JSON.stringify(record.raw),
    fetched_at: record.fetched_at ?? ""
  };
}

function nullableNumber(value: number | null) {
  return value === null ? "" : String(value);
}
