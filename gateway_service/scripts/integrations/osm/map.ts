import {
  parseCliArgs,
  parseRequiredNumber,
  readCsvRows,
  requiredText,
  UNKNOWN_TEXT,
  writePlacesCsv,
  type CanonicalPlaceRecord,
  type JsonObject,
  type RawCsvRow
} from "../_shared/place-record.js";
import { parsePythonLiteral } from "../_shared/python-literal.js";

async function main() {
  const { inputPath, outputPath } = parseCliArgs(process.argv.slice(2));
  const rows = await readCsvRows(inputPath);
  const records = rows.map(mapOsmRow);

  await writePlacesCsv(records, outputPath);
}

function mapOsmRow(row: RawCsvRow): CanonicalPlaceRecord {
  const tags = parseObjectLiteral(row.tags, "tags");
  const osmMeta = parseObjectLiteral(row.osm_meta, "osm_meta");
  const raw = parseObjectLiteral(row.raw, "raw");

  return {
    source: "osm",
    source_id: requiredText(row.place_id),
    name: requiredText(row.name),
    country: getText(tags["addr:country"]) ?? "Romania",
    city: getText(tags["addr:city"]) ?? "Bucharest",
    category: parseCategory(row.primary_type, tags),
    latitude: parseRequiredNumber(row.latitude, "latitude"),
    longitude: parseRequiredNumber(row.longitude, "longitude"),
    rating: null,
    price_level: null,
    reviews_count: null,
    embedding_text: null,
    attributes: {
      ...tags,
      formatted_address: row.formatted_address ?? null,
      primary_type: row.primary_type ?? null,
      osm_meta: osmMeta
    },
    raw,
    fetched_at: row.fetched_at?.trim() || null
  };
}

function parseCategory(
  primaryType: string | undefined,
  tags: Record<string, unknown>
) {
  const normalizedPrimary = primaryType?.trim();

  if (normalizedPrimary) {
    const [, value] = normalizedPrimary.split("=");
    return value?.trim() || normalizedPrimary;
  }

  return getText(tags.amenity) ?? getText(tags.shop) ?? UNKNOWN_TEXT;
}

function parseObjectLiteral(value: string | undefined, fieldName: string) {
  const parsed = parsePythonLiteral(value);

  if (!isJsonObject(parsed)) {
    throw new Error(`${fieldName} must be a Python dict literal`);
  }

  return parsed;
}

function getText(value: unknown) {
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : null;
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
