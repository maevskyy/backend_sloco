import pg from "pg";

// Per-pool result parsers (passed as the Pool `types` option — the global
// pg.types registry is never mutated). They make node-pg rows match what
// PostgREST returned, so stores moved off supabase-js keep the exact value
// types their mappers and the HTTP schemas already expect:
// - numeric / int8 (and their arrays) are JSON numbers in PostgREST, strings
//   in node-pg by default → numbers here. int8 above 2^53 loses precision,
//   exactly like JSON.parse on the PostgREST response did;
// - timestamptz / timestamp come back as strings in to_json format
//   ("2026-10-03T04:25:16.123456+00:00"), not JS Date objects; date stays
//   "YYYY-MM-DD".
// Everything else keeps node-pg defaults (json/jsonb parsed, bytea → Buffer).

const { types } = pg;

const Oid = {
  int8: 20,
  numeric: 1700,
  date: 1082,
  timestamp: 1114,
  timestamptz: 1184,
  int8Array: 1016,
  numericArray: 1231,
  dateArray: 1182,
  timestampArray: 1115,
  timestamptzArray: 1185,
  textArray: 1009
} as const;

type TextParser = (value: string) => unknown;
type NestedArray<T> = Array<T | null | NestedArray<T>>;

// text[] has no TypeId enum member in @types/pg, hence the loose lookup.
const getDefaultParser = types.getTypeParser as (
  oid: number,
  format?: "text" | "binary"
) => (value: string) => unknown;

const parseTextArray = getDefaultParser(Oid.textArray) as (
  value: string
) => NestedArray<string>;

const toNumber = (value: string) => Number(value);

const keepDate = (value: string) => value;

// Postgres ISO text output → to_json output: "T" separator, and the UTC
// offset always carries minutes ("+00" → "+00:00", "+05:30" unchanged).
// "infinity" / "-infinity" are identical in both forms.
function toJsonTimestamptz(value: string): string {
  const iso = value.replace(" ", "T");
  return /[+-]\d{2}$/.test(iso) ? `${iso}:00` : iso;
}

function toJsonTimestamp(value: string): string {
  return value.replace(" ", "T");
}

function arrayOf(parseItem: (value: string) => unknown): TextParser {
  const mapItems = (items: NestedArray<string>): unknown[] =>
    items.map((item) => {
      if (item === null) {
        return null;
      }
      return Array.isArray(item) ? mapItems(item) : parseItem(item);
    });

  return (value) => mapItems(parseTextArray(value));
}

const textParsers = new Map<number, TextParser>([
  [Oid.int8, toNumber],
  [Oid.numeric, toNumber],
  [Oid.date, keepDate],
  [Oid.timestamp, toJsonTimestamp],
  [Oid.timestamptz, toJsonTimestamptz],
  [Oid.int8Array, arrayOf(toNumber)],
  [Oid.numericArray, arrayOf(toNumber)],
  [Oid.dateArray, arrayOf(keepDate)],
  [Oid.timestampArray, arrayOf(toJsonTimestamp)],
  [Oid.timestamptzArray, arrayOf(toJsonTimestamptz)]
]);

export const pgTypeParsers: pg.CustomTypesConfig = {
  getTypeParser: ((oid: number, format?: "text" | "binary") => {
    if (format === undefined || format === "text") {
      const parser = textParsers.get(oid);

      if (parser) {
        return parser;
      }
    }

    return getDefaultParser(oid, format);
  }) as pg.CustomTypesConfig["getTypeParser"]
};
