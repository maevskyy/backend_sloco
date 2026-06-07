import pg from "pg";
import { env } from "../config/env.js";

const { Pool } = pg;

let pool: pg.Pool | null = null;

export function getPgPool(): pg.Pool {
  if (pool) {
    return pool;
  }

  if (!env.SUPABASE_DB_URL) {
    throw new Error("SUPABASE_DB_URL is required for direct Postgres access");
  }

  pool = new Pool({
    connectionString: env.SUPABASE_DB_URL,
    max: 5,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000
  });

  return pool;
}
