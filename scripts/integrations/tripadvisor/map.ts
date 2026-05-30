import {
  optionalText,
  parseCliArgs,
  parseOptionalNumber,
  parseRequiredNumber,
  readCsvRows,
  requiredText,
  UNKNOWN_TEXT,
  writePlacesCsv,
  type CanonicalPlaceRecord,
  type JsonObject,
  type RawCsvRow
} from "../_shared/place-record.js";

const CITY_COUNTRY: Record<string, string> = {
  Berlin: "Germany",
  Bucharest: "Romania",
  Kyiv: "Ukraine",
  Tbilisi: "Georgia"
};

async function main() {
  const { inputPath, outputPath } = parseCliArgs(process.argv.slice(2));
  const rows = await readCsvRows(inputPath);
  const records = rows.map(mapTripAdvisorRow);

  await writePlacesCsv(records, outputPath);
}

function mapTripAdvisorRow(row: RawCsvRow): CanonicalPlaceRecord {
  const city = requiredText(row.city);

  return {
    source: "tripadvisor",
    source_id: requiredText(row.tripadvisor_id),
    name: requiredText(row.name),
    country: CITY_COUNTRY[city] ?? UNKNOWN_TEXT,
    city,
    category: "restaurant",
    latitude: parseRequiredNumber(row.latitude, "latitude"),
    longitude: parseRequiredNumber(row.longitude, "longitude"),
    rating: parseOptionalNumber(row.rating),
    price_level: mapPriceLevel(row.price_range),
    reviews_count: parseOptionalNumber(row.number_of_reviews),
    embedding_text: optionalText(row.embedding_text),
    attributes: buildAttributes(row),
    raw: compactObject(row),
    fetched_at: null
  };
}

function buildAttributes(row: RawCsvRow): JsonObject {
  return compactObject({
    source_row_index: row.source_row_index,
    raw_cuisine_style: row.raw_cuisine_style,
    ranking: row.ranking,
    tripadvisor_url: row.tripadvisor_url,
    raw_reviews: row.raw_reviews
  });
}

function mapPriceLevel(value: string | undefined) {
  const normalized = value?.trim();

  if (!normalized) {
    return null;
  }

  if (normalized === "$") {
    return 1;
  }

  if (normalized === "$$ - $$$") {
    return 2;
  }

  if (normalized === "$$$") {
    return 3;
  }

  if (normalized === "$$$$") {
    return 4;
  }

  return null;
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

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
