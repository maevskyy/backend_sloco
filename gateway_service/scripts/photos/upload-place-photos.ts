import "dotenv/config";
import { existsSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { parse } from "csv-parse/sync";
import { createClient } from "@supabase/supabase-js";

const DEFAULT_BUCKET = "place-photos";
const DEFAULT_CONCURRENCY = 5;
const DEFAULT_SOURCE = "google";
const METADATA_BATCH_SIZE = 100;

type RawManifestRow = Record<string, string | undefined>;

type UploadOptions = {
  bucket: string;
  concurrency: number;
  datasetRoot: string;
  dryRun: boolean;
  ensureBucket: boolean;
  limit?: number;
  offset: number;
  overwrite: boolean;
  source: string;
};

type PreparedPhoto = {
  contentType: string | null;
  localPath: string;
  metadata: PlacePhotoMetadata;
  sizeBytes: number | null;
  storagePath: string;
};

type PlacePhotoMetadata = {
  place_source: string;
  place_source_id: string;
  photo_source: string;
  photo_item_id: string;
  storage_bucket: string;
  storage_path: string;
  public_url: string | null;
  source_url: string | null;
  original_file: string | null;
  bytes: number | null;
  content_type: string | null;
  width: number | null;
  height: number | null;
  review_id: string | null;
  review_rating: number | null;
  review_published_at: string | null;
  review_language: string | null;
  author_id: string | null;
  author_name: string | null;
  vibe_place_id: string | null;
  category: string | null;
  category_label: string | null;
  uploaded_by_owner: boolean | null;
  upload_date: string | null;
  photo_index: number | null;
  updated_at: string;
};

type UploadStats = {
  failed: number;
  metadataRows: number;
  skippedExisting: number;
  uploaded: number;
  validated: number;
};

type SupabaseClient = ReturnType<typeof createClient>;

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const manifestPath = path.join(options.datasetRoot, "photo_manifest.csv");
  const rows = await readManifestRows(manifestPath, options);

  if (options.dryRun) {
    const prepared = await preparePhotos(rows, options, null);
    const missingCount = prepared.filter((photo) => photo.sizeBytes === null).length;

    printDryRunSummary(prepared, missingCount, options);

    if (missingCount > 0) {
      process.exitCode = 1;
    }

    return;
  }

  const supabase = createSupabaseClient();

  if (options.ensureBucket) {
    await ensureBucket(supabase, options.bucket);
  }

  const prepared = await preparePhotos(rows, options, supabase);
  const stats = await uploadPhotos(prepared, options, supabase);

  printUploadSummary(stats, options);

  if (stats.failed > 0) {
    process.exitCode = 1;
  }
}

function parseArgs(argv: string[]): UploadOptions {
  const [datasetRootInput, ...rest] = argv;

  if (!datasetRootInput) {
    throw new Error(
      [
        "Usage:",
        "pnpm photos:upload <dataset-root> [--dry-run] [--limit N]",
        "  [--offset N] [--concurrency N] [--ensure-bucket] [--overwrite]"
      ].join(" ")
    );
  }

  const datasetRoot = path.resolve(datasetRootInput);

  return {
    bucket: readOption(rest, "--bucket") ?? DEFAULT_BUCKET,
    concurrency: readNumberOption(rest, "--concurrency") ?? DEFAULT_CONCURRENCY,
    datasetRoot,
    dryRun: rest.includes("--dry-run"),
    ensureBucket: rest.includes("--ensure-bucket"),
    limit: readNumberOption(rest, "--limit"),
    offset: readNumberOption(rest, "--offset") ?? 0,
    overwrite: rest.includes("--overwrite"),
    source: readOption(rest, "--source") ?? DEFAULT_SOURCE
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

  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }

  return value;
}

async function readManifestRows(filePath: string, options: UploadOptions) {
  if (!existsSync(filePath)) {
    throw new Error(`photo_manifest.csv not found: ${filePath}`);
  }

  const content = await readFile(filePath, "utf8");
  const rows = parse(content, {
    bom: true,
    columns: true,
    skip_empty_lines: true
  }) as RawManifestRow[];

  return rows.slice(options.offset, sliceEnd(options.offset, options.limit));
}

function sliceEnd(offset: number, limit: number | undefined) {
  return limit === undefined ? undefined : offset + limit;
}

async function preparePhotos(
  rows: RawManifestRow[],
  options: UploadOptions,
  supabase: SupabaseClient | null
) {
  const publicUrlCache = new Map<string, string>();
  const photos: PreparedPhoto[] = [];

  for (const row of rows) {
    const localPath = path.join(
      options.datasetRoot,
      requiredText(row.bundle_relative_file, "bundle_relative_file")
    );
    const storagePath = buildStoragePath(row, options);
    const sizeBytes = await getFileSize(localPath);
    const publicUrl = supabase
      ? getPublicUrl(supabase, options.bucket, storagePath, publicUrlCache)
      : null;

    photos.push({
      contentType: optionalText(row.content_type),
      localPath,
      metadata: {
        place_source: options.source,
        place_source_id: requiredText(row.place_id, "place_id"),
        photo_source: requiredText(row.photo_source, "photo_source"),
        photo_item_id: requiredText(row.photo_item_id, "photo_item_id"),
        storage_bucket: options.bucket,
        storage_path: storagePath,
        public_url: publicUrl,
        source_url: optionalText(row.source_url),
        original_file: optionalText(row.original_file),
        bytes: parseOptionalInteger(row.bytes),
        content_type: optionalText(row.content_type),
        width: parseOptionalInteger(row.width),
        height: parseOptionalInteger(row.height),
        review_id: optionalText(row.review_id),
        review_rating: parseOptionalNumber(row.review_rating),
        review_published_at: optionalDate(row.review_published_at),
        review_language: optionalText(row.review_language),
        author_id: optionalText(row.author_id),
        author_name: optionalText(row.author_name),
        vibe_place_id: optionalText(row.vibe_place_id),
        category: optionalText(row.category),
        category_label: optionalText(row.category_label),
        uploaded_by_owner: parseOptionalBoolean(row.uploaded_by_owner),
        upload_date: optionalDate(row.upload_date),
        photo_index: parseOptionalInteger(row.photo_index),
        updated_at: new Date().toISOString()
      },
      sizeBytes,
      storagePath
    });
  }

  return photos;
}

function buildStoragePath(row: RawManifestRow, options: UploadOptions) {
  const sourceId = safePathSegment(requiredText(row.place_id, "place_id"));
  const photoSource = safePathSegment(requiredText(row.photo_source, "photo_source"));
  const photoItemId = safePathSegment(requiredText(row.photo_item_id, "photo_item_id"));
  const extension = path.extname(requiredText(row.bundle_relative_file, "bundle_relative_file"));
  const safeExtension = extension && extension.length <= 10 ? extension : ".jpg";

  return `${options.source}/${sourceId}/${photoSource}/${photoItemId}${safeExtension}`;
}

function safePathSegment(value: string) {
  return value.replace(/[^a-zA-Z0-9._-]/g, "_");
}

async function getFileSize(filePath: string) {
  try {
    const result = await stat(filePath);
    return result.size;
  } catch {
    return null;
  }
}

function getPublicUrl(
  supabase: SupabaseClient,
  bucket: string,
  storagePath: string,
  cache: Map<string, string>
) {
  const key = `${bucket}/${storagePath}`;
  const cached = cache.get(key);
  if (cached) return cached;

  const { data } = supabase.storage.from(bucket).getPublicUrl(storagePath);
  cache.set(key, data.publicUrl);
  return data.publicUrl;
}

async function uploadPhotos(
  photos: PreparedPhoto[],
  options: UploadOptions,
  supabase: SupabaseClient
): Promise<UploadStats> {
  const stats: UploadStats = {
    failed: 0,
    metadataRows: 0,
    skippedExisting: 0,
    uploaded: 0,
    validated: photos.length
  };
  const metadataRows: PlacePhotoMetadata[] = [];

  await runWithConcurrency(photos, options.concurrency, async (photo, index) => {
    if (photo.sizeBytes === null) {
      stats.failed += 1;
      console.error(`missing file: ${photo.localPath}`);
      return;
    }

    const file = await readFile(photo.localPath);
    const { error } = await supabase.storage
      .from(options.bucket)
      .upload(photo.storagePath, file, {
        contentType: photo.contentType ?? undefined,
        upsert: options.overwrite
      });

    if (error) {
      if (isAlreadyExistsError(error) && !options.overwrite) {
        stats.skippedExisting += 1;
      } else {
        stats.failed += 1;
        console.error(`upload failed: ${photo.storagePath} - ${error.message}`);
        return;
      }
    } else {
      stats.uploaded += 1;
    }

    metadataRows.push(photo.metadata);

    if ((index + 1) % 100 === 0 || index + 1 === photos.length) {
      console.log(`processed ${index + 1}/${photos.length}`);
    }
  });

  for (let index = 0; index < metadataRows.length; index += METADATA_BATCH_SIZE) {
    const batch = metadataRows.slice(index, index + METADATA_BATCH_SIZE);
    const { error } = await supabase.from("place_photos").upsert(batch, {
      onConflict: "place_source,place_source_id,photo_source,photo_item_id"
    });

    if (error) {
      throw new Error(`place_photos upsert failed: ${error.message}`);
    }

    stats.metadataRows += batch.length;
  }

  return stats;
}

async function runWithConcurrency<T>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<void>
) {
  let nextIndex = 0;

  async function runWorker() {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;

      const item = items[index];
      if (item !== undefined) {
        await worker(item, index);
      }
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, runWorker)
  );
}

async function ensureBucket(supabase: SupabaseClient, bucket: string) {
  const { data, error } = await supabase.storage.getBucket(bucket);

  if (data) {
    return;
  }

  if (error && !isBucketNotFoundError(error)) {
    throw new Error(`bucket check failed: ${error.message}`);
  }

  const { error: createError } = await supabase.storage.createBucket(bucket, {
    public: true
  });

  if (createError && !isAlreadyExistsError(createError)) {
    throw new Error(`bucket create failed: ${createError.message}`);
  }
}

function createSupabaseClient() {
  const supabaseUrl = process.env.SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for real upload"
    );
  }

  return createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false
    }
  });
}

function requiredText(value: string | undefined, fieldName: string) {
  const normalized = value?.trim();

  if (!normalized) {
    throw new Error(`Missing required manifest field: ${fieldName}`);
  }

  return normalized;
}

function optionalText(value: string | undefined) {
  const normalized = value?.trim();
  return normalized ? normalized : null;
}

function parseOptionalInteger(value: string | undefined) {
  const normalized = optionalText(value);
  if (normalized === null) return null;

  const parsed = Number(normalized);
  return Number.isInteger(parsed) ? parsed : null;
}

function parseOptionalNumber(value: string | undefined) {
  const normalized = optionalText(value);
  if (normalized === null) return null;

  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseOptionalBoolean(value: string | undefined) {
  const normalized = optionalText(value)?.toLowerCase();
  if (normalized === null || normalized === undefined) return null;
  if (["true", "1", "yes"].includes(normalized)) return true;
  if (["false", "0", "no"].includes(normalized)) return false;
  return null;
}

function optionalDate(value: string | undefined) {
  const normalized = optionalText(value);
  if (normalized === null) return null;

  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function isAlreadyExistsError(error: { message?: string; statusCode?: string }) {
  return (
    error.statusCode === "409" ||
    error.message?.toLowerCase().includes("already exists") === true ||
    error.message?.toLowerCase().includes("duplicate") === true
  );
}

function isBucketNotFoundError(error: { message?: string; statusCode?: string }) {
  return (
    error.statusCode === "404" ||
    error.message?.toLowerCase().includes("not found") === true
  );
}

function printDryRunSummary(
  photos: PreparedPhoto[],
  missingCount: number,
  options: UploadOptions
) {
  const totalBytes = photos.reduce((sum, photo) => sum + (photo.sizeBytes ?? 0), 0);

  console.log(
    JSON.stringify(
      {
        bucket: options.bucket,
        dryRun: true,
        missingCount,
        sample: photos.slice(0, 5).map((photo) => ({
          localPath: photo.localPath,
          storagePath: photo.storagePath,
          sizeBytes: photo.sizeBytes
        })),
        totalBytes,
        totalRows: photos.length
      },
      null,
      2
    )
  );
}

function printUploadSummary(stats: UploadStats, options: UploadOptions) {
  console.log(
    JSON.stringify(
      {
        bucket: options.bucket,
        dryRun: false,
        ...stats
      },
      null,
      2
    )
  );
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message);
  process.exit(1);
});

