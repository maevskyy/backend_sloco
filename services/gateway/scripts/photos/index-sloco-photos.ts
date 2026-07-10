import "dotenv/config";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";

// Indexes the R2-hosted sloco photo set into public.place_photos.
//
// Input: a manifest produced on the box that holds the photos:
//   rclone lsf -R r2:sloco-photos/sloco_ai --files-only > sloco_photos_manifest.txt
// Each line is `<cid>/<NN>_<kind>.jpg` (relative to the sloco_ai/ prefix).
//
// The script does NOT touch storage — files are already in R2. It only upserts
// metadata rows keyed (place_source, place_source_id, photo_source, photo_item_id)
// with storage_path `sloco_ai/<cid>/<file>` and public_url built from
// PHOTO_BASE_URL, so serving stays storage-agnostic (see TASKS_33).

const SOURCE = "sloco_ai";
const PHOTO_SOURCE = "vibe";
const STORAGE_BUCKET = "sloco-photos";
const UPSERT_BATCH_SIZE = 500;

const MANIFEST_LINE = /^([^/]+)\/((\d+)_([a-z0-9]+))\.(jpe?g|png|webp)$/i;

type IndexOptions = {
  baseUrl: string;
  dryRun: boolean;
  limit?: number;
  manifestPath: string;
};

type PlacePhotoRow = {
  place_source: string;
  place_source_id: string;
  photo_source: string;
  photo_item_id: string;
  storage_bucket: string;
  storage_path: string;
  public_url: string;
  category: string | null;
  photo_index: number | null;
  updated_at: string;
};

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const lines = await readManifestLines(options.manifestPath, options.limit);

  const rows: PlacePhotoRow[] = [];
  const skipped: string[] = [];

  for (const line of lines) {
    const row = mapManifestLine(line, options.baseUrl);

    if (row === null) {
      skipped.push(line);
    } else {
      rows.push(row);
    }
  }

  const placeCount = new Set(rows.map((row) => row.place_source_id)).size;

  if (skipped.length > 0) {
    console.warn(`unrecognized manifest lines: ${skipped.length}`);
    for (const line of skipped.slice(0, 10)) {
      console.warn(`  skipped: ${line}`);
    }
  }

  if (options.dryRun) {
    console.log(
      JSON.stringify(
        {
          dryRun: true,
          manifestLines: lines.length,
          rows: rows.length,
          places: placeCount,
          skipped: skipped.length,
          sample: rows.slice(0, 3)
        },
        null,
        2
      )
    );
    return;
  }

  const supabase = createSupabaseClient();
  let upserted = 0;

  for (let index = 0; index < rows.length; index += UPSERT_BATCH_SIZE) {
    const batch = rows.slice(index, index + UPSERT_BATCH_SIZE);
    const { error } = await supabase.from("place_photos").upsert(batch, {
      onConflict: "place_source,place_source_id,photo_source,photo_item_id"
    });

    if (error) {
      throw new Error(
        `place_photos upsert failed at row ${index}: ${error.message}`
      );
    }

    upserted += batch.length;
    console.log(`upserted ${upserted}/${rows.length}`);
  }

  console.log(
    JSON.stringify(
      {
        dryRun: false,
        rows: upserted,
        places: placeCount,
        skipped: skipped.length,
        next: "run the primary photo backfill SQL (scripts/README.md, Sloco Photo Index)"
      },
      null,
      2
    )
  );
}

function parseArgs(argv: string[]): IndexOptions {
  const [manifestInput, ...rest] = argv;

  if (!manifestInput) {
    throw new Error(
      [
        "Usage:",
        "pnpm photos:index-sloco <manifest.txt>",
        "  [--base-url https://pub-....r2.dev] [--dry-run] [--limit N]"
      ].join(" ")
    );
  }

  const baseUrl = readOption(rest, "--base-url") ?? process.env.PHOTO_BASE_URL;

  if (!baseUrl) {
    throw new Error("Provide --base-url or set PHOTO_BASE_URL");
  }

  return {
    baseUrl: baseUrl.replace(/\/+$/, ""),
    dryRun: rest.includes("--dry-run"),
    limit: readNumberOption(rest, "--limit"),
    manifestPath: path.resolve(manifestInput)
  };
}

function readOption(argv: string[], name: string) {
  const index = argv.indexOf(name);
  if (index === -1) return undefined;

  const value = argv[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`Missing value for ${name}`);
  }

  return value;
}

function readNumberOption(argv: string[], name: string) {
  const raw = readOption(argv, name);
  if (raw === undefined) return undefined;

  const value = Number(raw);

  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }

  return value;
}

async function readManifestLines(filePath: string, limit?: number) {
  if (!existsSync(filePath)) {
    throw new Error(`manifest not found: ${filePath}`);
  }

  const content = await readFile(filePath, "utf8");
  const lines = content
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  return limit === undefined ? lines : lines.slice(0, limit);
}

function mapManifestLine(line: string, baseUrl: string): PlacePhotoRow | null {
  const match = MANIFEST_LINE.exec(line);

  if (!match) {
    return null;
  }

  const [, cid, stem, indexRaw, kind] = match;

  if (!cid || !stem || !indexRaw || !kind) {
    return null;
  }

  const storagePath = `${SOURCE}/${line}`;

  return {
    place_source: SOURCE,
    place_source_id: cid,
    photo_source: PHOTO_SOURCE,
    photo_item_id: stem,
    storage_bucket: STORAGE_BUCKET,
    storage_path: storagePath,
    public_url: `${baseUrl}/${storagePath}`,
    category: kind.toLowerCase(),
    photo_index: Number(indexRaw),
    updated_at: new Date().toISOString()
  };
}

function createSupabaseClient() {
  const supabaseUrl = process.env.SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for indexing"
    );
  }

  return createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false
    }
  });
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message);
  process.exit(1);
});
