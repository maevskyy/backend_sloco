import { writeFile } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import { stringify } from "csv-stringify/sync";

// Source: the data team's raw DataForSEO scrape (data_new/data/dataforseo/
// <city>/places.jsonl, one JSON object per line). Extracts the place-details
// fields the catalog pipeline dropped (TASKS_47): address, hours, phone,
// website, price — keyed by the numeric Google `cid`, which equals
// `public.places.source_id` for `source = 'sloco_ai'`.
//
// Usage:
//   pnpm details:dataforseo <places.jsonl> [more.jsonl ...] --out dumps/place_details_delta.csv
//
// Empty cells import as SQL NULL (same convention as the sloco mapper). The
// UPDATE that applies the delta is per-field NULL-guarded, so re-running the
// import never overwrites existing values.

const OUTPUT_COLUMNS = [
  "cid",
  "formatted_address",
  "short_formatted_address",
  "phone",
  "website_url",
  "price_level",
  "opening_hours"
] as const;

type OutputColumn = (typeof OUTPUT_COLUMNS)[number];
type DeltaRow = Record<OutputColumn, string>;

const DAY_ORDER = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday"
] as const;

// Same vocabulary as the sloco catalog mapper (Google integer semantics).
const PRICE_LEVEL_WORDS: Record<string, number> = {
  free: 0,
  inexpensive: 1,
  moderate: 2,
  expensive: 3,
  very_expensive: 4
};

type TimePoint = { hour?: unknown; minute?: unknown };
type TimeRange = { open?: TimePoint | null; close?: TimePoint | null };

async function main() {
  const { inputPaths, outputPath } = parseArgs(process.argv.slice(2));
  const rows = new Map<string, DeltaRow>();
  let records = 0;
  let noCid = 0;
  let duplicates = 0;

  for (const inputPath of inputPaths) {
    const lines = createInterface({
      input: createReadStream(inputPath, "utf8"),
      crlfDelay: Number.POSITIVE_INFINITY
    });

    for await (const line of lines) {
      const trimmed = line.trim();

      if (!trimmed) continue;

      records += 1;
      const record = JSON.parse(trimmed) as Record<string, unknown>;
      const cid = asText(record.cid);

      if (!cid || !/^\d+$/.test(cid)) {
        noCid += 1;
        continue;
      }

      if (rows.has(cid)) {
        duplicates += 1;
        continue;
      }

      rows.set(cid, mapRecord(cid, record));
    }
  }

  const csv = stringify([...rows.values()], {
    header: true,
    columns: [...OUTPUT_COLUMNS]
  });

  await writeFile(outputPath, csv, "utf8");

  const filled: Record<string, number> = {};
  for (const row of rows.values()) {
    for (const column of OUTPUT_COLUMNS) {
      if (column !== "cid" && row[column] !== "") {
        filled[column] = (filled[column] ?? 0) + 1;
      }
    }
  }

  process.stderr.write(
    `records=${records} written=${rows.size} noCid=${noCid} duplicates=${duplicates}\n` +
      `filled: ${JSON.stringify(filled)}\n`
  );
}

function mapRecord(cid: string, record: Record<string, unknown>): DeltaRow {
  const addressInfo = asObject(record.address_info);

  return {
    cid,
    formatted_address: asText(record.address) ?? "",
    short_formatted_address: asText(addressInfo?.address) ?? "",
    phone: asText(record.phone) ?? "",
    website_url: websiteUrl(record),
    price_level: priceLevelCell(record.price_level),
    opening_hours: openingHoursCell(record.work_time)
  };
}

// `url` is usually the venue website, but for places without one DataForSEO
// points it at Google Maps — fall back to `domain` there, else emit nothing.
function websiteUrl(record: Record<string, unknown>): string {
  const url = asText(record.url);

  if (url && /^https?:\/\//.test(url) && !/(^|\.)google\./.test(hostOf(url))) {
    return url;
  }

  const domain = asText(record.domain);

  if (domain && !/(^|\.)google\./.test(domain)) {
    return `https://${domain.replace(/^https?:\/\//, "")}`;
  }

  return "";
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

function priceLevelCell(value: unknown): string {
  const normalized = asText(value)?.toLowerCase();

  if (!normalized) return "";

  const mapped = PRICE_LEVEL_WORDS[normalized];

  return mapped === undefined ? "" : String(mapped);
}

// work_time.work_hours.timetable -> the schema's opening_hours jsonb with
// Google-format weekdayDescriptions ("Monday: 10:00 AM – 12:00 AM", "Closed",
// "Open 24 hours"). The scrape's current_status is a stale snapshot and is
// deliberately dropped — the client derives open/closed from the descriptions.
function openingHoursCell(workTime: unknown): string {
  const timetable = asObject(asObject(asObject(workTime)?.work_hours)?.timetable);

  if (!timetable) return "";

  const descriptions: string[] = [];
  let hasAnyDay = false;

  for (const day of DAY_ORDER) {
    const label = day.charAt(0).toUpperCase() + day.slice(1);
    const ranges = asRanges(timetable[day]);

    if (ranges.length === 0) {
      descriptions.push(`${label}: Closed`);
      continue;
    }

    hasAnyDay = true;
    const parts = ranges.map(formatRange).filter((part): part is string => part !== null);

    if (parts.length === 0) {
      descriptions.push(`${label}: Closed`);
      continue;
    }

    descriptions.push(`${label}: ${parts.join(", ")}`);
  }

  if (!hasAnyDay) return "";

  return JSON.stringify({ weekdayDescriptions: descriptions });
}

function asRanges(value: unknown): TimeRange[] {
  return Array.isArray(value) ? (value as TimeRange[]) : [];
}

function formatRange(range: TimeRange): string | null {
  const open = clockMinutes(range.open);
  const close = clockMinutes(range.close);

  if (open === null || close === null) return null;

  if (open === close) return "Open 24 hours";

  return `${formatClock(open)} – ${formatClock(close)}`;
}

function clockMinutes(point: TimePoint | null | undefined): number | null {
  if (!point || typeof point !== "object") return null;

  const hour = asNumber(point.hour);
  const minute = asNumber(point.minute) ?? 0;

  if (hour === null || hour < 0 || hour > 24 || minute < 0 || minute > 59) {
    return null;
  }

  return (hour % 24) * 60 + minute;
}

function formatClock(totalMinutes: number): string {
  const hour = Math.floor(totalMinutes / 60);
  const minute = totalMinutes % 60;
  const period = hour < 12 ? "AM" : "PM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;

  return `${hour12}:${String(minute).padStart(2, "0")} ${period}`;
}

// --- small narrowing helpers ---------------------------------------------------

function asText(value: unknown): string | null {
  if (typeof value !== "string") return null;

  const trimmed = value.trim();

  return trimmed.length > 0 ? trimmed : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function parseArgs(argv: string[]): { inputPaths: string[]; outputPath: string } {
  const inputPaths: string[] = [];
  let outputPath: string | null = null;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === "--out") {
      outputPath = argv[index + 1] ?? null;
      index += 1;
      continue;
    }

    inputPaths.push(arg);
  }

  if (inputPaths.length === 0 || !outputPath) {
    throw new Error(
      "Usage: details-delta <places.jsonl> [more.jsonl ...] --out <delta.csv>"
    );
  }

  return { inputPaths, outputPath };
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
