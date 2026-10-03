import pg from "pg";
import { env } from "../config/env.js";
import { isDbUnavailableError } from "./db-errors.js";
import { pgTypeParsers } from "./pg-type-parsers.js";

const { Pool } = pg;

// The single database seam (SLO-49): every store talks to Postgres through a
// `Db` — one direct node-pg pool against the Supabase pooler (Supavisor,
// transaction mode, SUPABASE_DB_URL). Stores take it via constructor; tests
// pass a fake.

export type DbQueryResult<T> = {
  rows: T[];
  rowCount: number | null;
};

export type DbExecutor = {
  query<T = Record<string, unknown>>(
    text: string,
    params?: readonly unknown[]
  ): Promise<DbQueryResult<T>>;
};

export type Db = DbExecutor & {
  // BEGIN/COMMIT on one pooled client; ROLLBACK when `fn` throws. Every
  // statement inside must go through `tx`, not the outer Db. A failed
  // statement aborts the whole transaction even if caught, and the commit
  // then throws — handle expected duplicates with ON CONFLICT (or a
  // SAVEPOINT), not try/catch on 23505.
  transaction<T>(fn: (tx: DbExecutor) => Promise<T>): Promise<T>;
};

export type DbPoolStats = {
  total: number;
  idle: number;
  waiting: number;
};

export type PgPoolConfig = {
  connectionString: string;
  max: number;
  connectionTimeoutMillis: number;
  idleTimeoutMillis: number;
  queryTimeoutMillis: number;
};

export function createPgPool(config: PgPoolConfig): pg.Pool {
  return new Pool({
    connectionString: config.connectionString,
    max: config.max,
    // Wait for a free client (or a new connection) at most this long; past it
    // the call fails fast and the HTTP layer answers 503 instead of queueing.
    connectionTimeoutMillis: config.connectionTimeoutMillis,
    idleTimeoutMillis: config.idleTimeoutMillis,
    // Client-side deadline: the promise rejects with "Query read timeout" and
    // the client is discarded. It does NOT cancel the statement on the server.
    query_timeout: config.queryTimeoutMillis,
    // No `statement_timeout` startup parameter on purpose: Supavisor in
    // transaction mode shares server connections, so it would not reach the
    // backend, and a pooler that rejects unknown startup parameters (PgBouncer
    // does) would fail every connection. A statement abandoned by
    // query_timeout keeps running on the server until the role's
    // statement_timeout (see docs/DEPLOYMENT.md); transaction() sets its own.
    types: pgTypeParsers
  });
}

class PgDb implements Db {
  constructor(
    private readonly resolvePool: () => pg.Pool,
    private readonly statementTimeoutMillis: number
  ) {}

  // Not pool.query(): that releases the client with the query error, and
  // pg-pool destroys any client released with an error — so every caught
  // 23505/23503 would cost a fresh TLS connection to the pooler.
  async query<T = Record<string, unknown>>(
    text: string,
    params: readonly unknown[] = []
  ): Promise<DbQueryResult<T>> {
    const lease = await acquire(this.resolvePool());
    let releaseError: Error | undefined;

    try {
      return await runQuery<T>(lease.client, text, params);
    } catch (error) {
      releaseError = connectionUnusable(error);
      throw error;
    } finally {
      lease.release(releaseError);
    }
  }

  async transaction<T>(fn: (tx: DbExecutor) => Promise<T>): Promise<T> {
    const lease = await acquire(this.resolvePool());
    const { client } = lease;
    const tx: DbExecutor = {
      query: (text, params = []) => runQuery(client, text, params)
    };
    let releaseError: Error | undefined;

    try {
      await client.query("begin");
      // Server-side deadline for this transaction. Inside a transaction the
      // pooler keeps one server connection, so SET LOCAL applies (single
      // statements outside a transaction have no such guard, see createPgPool).
      await client.query("select set_config('statement_timeout', $1, true)", [
        String(this.statementTimeoutMillis)
      ]);
      const result = await fn(tx);
      // A statement that failed inside `fn` aborts the transaction even if
      // `fn` caught it; COMMIT then does not throw, it answers ROLLBACK.
      const commit = await client.query("commit");
      if (commit.command === "ROLLBACK") {
        throw new Error(
          "transaction aborted: a statement inside it failed and was caught"
        );
      }
      return result;
    } catch (error) {
      releaseError = await rollback(client, error);
      throw error;
    } finally {
      lease.release(releaseError);
    }
  }
}

type Lease = {
  client: pg.PoolClient;
  // An error here destroys the client instead of returning it to the pool.
  release(error?: Error): void;
};

// pg-pool removes its idle 'error' listener from a checked-out client, and a
// pg client emits 'error' when its connection drops even with no query in
// flight. Without a listener of our own that is an uncaughtException that
// takes the process down; with it, the in-flight query is rejected anyway and
// the broken client is destroyed on release.
async function acquire(pool: pg.Pool): Promise<Lease> {
  const client = await pool.connect();
  let connectionError: Error | undefined;
  const onError = (error: Error) => {
    connectionError = error;
  };
  client.on("error", onError);

  return {
    client,
    release(error) {
      client.removeListener("error", onError);
      client.release(connectionError ?? error);
    }
  };
}

// A server error (SQLSTATE, e.g. 23505 or P0001) leaves the connection idle
// and usable. Timeouts, socket errors and anything else without a SQLSTATE
// mean the connection state is unknown — drop it.
function connectionUnusable(error: unknown): Error | undefined {
  if (error instanceof pg.DatabaseError && !isDbUnavailableError(error)) {
    return undefined;
  }

  return error instanceof Error ? error : new Error("db query failed");
}

async function runQuery<T>(
  client: pg.PoolClient,
  text: string,
  params: readonly unknown[]
): Promise<DbQueryResult<T>> {
  const result = await client.query(text, [...params]);
  return { rows: result.rows as T[], rowCount: result.rowCount };
}

// Returns an error when the client must not go back to the pool.
async function rollback(
  client: pg.PoolClient,
  error: unknown
): Promise<Error | undefined> {
  // After a timeout the connection still has a query in flight; drop it (the
  // server rolls back on disconnect) rather than queue ROLLBACK behind it.
  if (isDbUnavailableError(error)) {
    return error instanceof Error ? error : new Error("db unavailable");
  }

  try {
    await client.query("rollback");
    return undefined;
  } catch (rollbackError) {
    return rollbackError instanceof Error
      ? rollbackError
      : new Error("rollback failed");
  }
}

export function createDb(
  pool: pg.Pool,
  statementTimeoutMillis: number = env.PG_QUERY_TIMEOUT_MS
): Db {
  return new PgDb(() => pool, statementTimeoutMillis);
}

// --- process-wide default --------------------------------------------------
// One pool per process, built from env on first use (so importing a module in
// tests without SUPABASE_DB_URL never throws). buildApp passes this same Db to
// every module, and module-level default singletons resolve to it too — there
// is never a second pool.

let defaultPool: pg.Pool | null = null;
let poolErrorListener: (error: Error) => void = () => {};

function getDefaultPool(): pg.Pool {
  if (defaultPool) {
    return defaultPool;
  }

  if (!env.SUPABASE_DB_URL) {
    throw new Error("SUPABASE_DB_URL is required for direct Postgres access");
  }

  defaultPool = createPgPool({
    connectionString: env.SUPABASE_DB_URL,
    max: env.PG_POOL_MAX,
    connectionTimeoutMillis: env.PG_POOL_CONNECTION_TIMEOUT_MS,
    idleTimeoutMillis: env.PG_POOL_IDLE_TIMEOUT_MS,
    queryTimeoutMillis: env.PG_QUERY_TIMEOUT_MS
  });
  // An idle client losing its connection emits on the pool; without a
  // listener that would crash the process.
  defaultPool.on("error", (error) => poolErrorListener(error));

  return defaultPool;
}

const defaultDb: Db = new PgDb(getDefaultPool, env.PG_QUERY_TIMEOUT_MS);

export function getDb(): Db {
  return defaultDb;
}

export function onDbPoolError(listener: (error: Error) => void) {
  poolErrorListener = listener;
}

export function getDbPoolStats(): DbPoolStats {
  return {
    total: defaultPool?.totalCount ?? 0,
    idle: defaultPool?.idleCount ?? 0,
    waiting: defaultPool?.waitingCount ?? 0
  };
}

// Ends the default pool; a later query lazily opens a fresh one (tests build
// and close several apps per process).
export async function closeDb() {
  const pool = defaultPool;
  defaultPool = null;
  await pool?.end();
}
