import "dotenv/config";
import { z } from "zod";

const optionalNonEmptyString = (schema: z.ZodString) =>
  z.preprocess((value) => (value === "" ? undefined : value), schema.optional());

const envSchema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
  HOST: z.string().default("0.0.0.0"),
  PORT: z.coerce.number().int().positive().default(3000),
  SUPABASE_URL: optionalNonEmptyString(z.string().url()),
  SUPABASE_SERVICE_ROLE_KEY: optionalNonEmptyString(z.string().min(1)),
  SUPABASE_DB_URL: optionalNonEmptyString(z.string().url()),
  // Direct Postgres pool (src/lib/db.ts). Fail fast under saturation: a
  // request that cannot get a connection or whose query runs past the
  // deadline answers 503 instead of hanging (SLO-32).
  PG_POOL_MAX: z.coerce.number().int().positive().default(20),
  PG_POOL_CONNECTION_TIMEOUT_MS: z.coerce.number().int().positive().default(2000),
  PG_POOL_IDLE_TIMEOUT_MS: z.coerce.number().int().min(0).default(30000),
  // Same as PostgREST's authenticator statement_timeout (8 s).
  PG_QUERY_TIMEOUT_MS: z.coerce.number().int().positive().default(8000),
  RECOMMENDATION_SERVICE_URL: optionalNonEmptyString(z.string().url()),
  // Private walk-planner service (SLO-67); unset → /v1/walks/* answer 503.
  WALK_PLANNER_URL: optionalNonEmptyString(z.string().url()),
  // Plans / edits in flight per gateway process = the service's load guard
  // (its WEB_CONCURRENCY × WALK_MAX_CONCURRENT_PLANS, 2 × 2 by default).
  WALK_PLANNER_MAX_CONCURRENT: z.coerce.number().int().min(1).default(4),
  REDIS_URL: optionalNonEmptyString(z.string().url()),
  PLACE_CACHE_TTL_SECONDS: z.coerce.number().int().min(0).default(3600),
  MAP_TILE_CACHE_TTL_SECONDS: z.coerce.number().int().min(0).default(604800),
  MAP_TILE_VERSION: z.coerce.number().int().min(1).default(1)
});

export const env = envSchema.parse(process.env);
