// Errors that mean "the database is saturated or too slow right now", not
// "this request is wrong": the pool could not hand out a connection within
// PG_POOL_CONNECTION_TIMEOUT_MS, a new connection could not be opened in time,
// or a query ran past PG_QUERY_TIMEOUT_MS (client-side read timeout, or the
// server-side statement_timeout). The HTTP layer answers these with 503 +
// Retry-After instead of 500.

const DB_UNAVAILABLE_MESSAGES = new Set([
  // pg-pool: no idle client and none freed within connectionTimeoutMillis.
  "timeout exceeded when trying to connect",
  // pg-pool: opening a new client took longer than connectionTimeoutMillis.
  "Connection terminated due to connection timeout",
  // pg: query_timeout elapsed before the server answered.
  "Query read timeout"
]);

// query_canceled — raised by the server when statement_timeout fires.
const QUERY_CANCELED_CODE = "57014";

const MAX_CAUSE_DEPTH = 5;

export function isDbUnavailableError(error: unknown): boolean {
  let current: unknown = error;

  // Stores may wrap the pg error; follow `cause` a few levels.
  for (let depth = 0; depth < MAX_CAUSE_DEPTH && current; depth += 1) {
    if (current instanceof Error) {
      if (DB_UNAVAILABLE_MESSAGES.has(current.message)) {
        return true;
      }

      if ((current as { code?: unknown }).code === QUERY_CANCELED_CODE) {
        return true;
      }
    }

    current =
      typeof current === "object" ? (current as { cause?: unknown }).cause : undefined;
  }

  return false;
}
