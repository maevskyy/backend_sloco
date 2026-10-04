import { env } from "../config/env.js";
import { measureDependencyMetric } from "../observability/metrics.js";
import { Semaphore } from "./semaphore.js";

// HTTP client of the private walk-planner service (services/walk-planner,
// SLO-67). It is a transport: it does not interpret walk payloads, it returns
// the service's status + JSON body as they are (the service's own 4xx/5xx carry
// the `{error: {code, message, params}}` envelope the app handles).
//
// Timeouts and retries follow services/walk-planner/docs/INTEGRATION.md §3.5:
// one deadline per call covers every attempt. A plan is CPU work that keeps
// running on the server after the client gives up, so it is never re-sent
// after the request went out — only on a refused connection or a 503 that says
// "not started" (busy / not_ready). Edits, search, place and config are pure
// functions and may be re-sent once.
//
// Plan, schedule and insert also pass a gateway-side semaphore sized like the
// service's load guard (WEB_CONCURRENCY × WALK_MAX_CONCURRENT_PLANS = 2 × 2):
// a burst waits here up to 5 s for a slot instead of making the service answer
// 503 busy round trips (INTEGRATION.md §3.6). Past the wait: WalkPlannerBusyError.

export type WalkPlannerOperation =
  | "config"
  | "plan"
  | "schedule"
  | "insert"
  | "search"
  | "place";

export type WalkPlannerQuery = Record<
  string,
  string | number | boolean | undefined
>;

export type WalkPlannerReply = {
  status: number;
  body: unknown;
  // Seconds, as the service sent it with 503 busy / not_ready.
  retryAfter?: string;
};

export type WalkPlannerCallContext = {
  // Echoed by the service in its response header and every log line of the
  // request, when it matches ^[A-Za-z0-9._:\-]{1,128}$.
  requestId?: string;
};

export type WalkPlannerClient = {
  config(
    query: WalkPlannerQuery,
    context?: WalkPlannerCallContext
  ): Promise<WalkPlannerReply>;
  plan(
    body: unknown,
    query: WalkPlannerQuery,
    context?: WalkPlannerCallContext
  ): Promise<WalkPlannerReply>;
  schedule(
    body: unknown,
    query: WalkPlannerQuery,
    context?: WalkPlannerCallContext
  ): Promise<WalkPlannerReply>;
  insert(
    body: unknown,
    query: WalkPlannerQuery,
    context?: WalkPlannerCallContext
  ): Promise<WalkPlannerReply>;
  searchPlaces(
    query: WalkPlannerQuery,
    context?: WalkPlannerCallContext
  ): Promise<WalkPlannerReply>;
  place(
    sourceId: string,
    query: WalkPlannerQuery,
    context?: WalkPlannerCallContext
  ): Promise<WalkPlannerReply>;
};

/** The service could not be reached (refused / reset after the retry, or not configured). */
export class WalkPlannerUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WalkPlannerUnavailableError";
  }
}

/** No gateway-side slot for a plan / edit freed up within the queue wait. */
export class WalkPlannerBusyError extends Error {
  constructor(readonly operation: WalkPlannerOperation) {
    super(`walk-planner ${operation}: no free slot`);
    this.name = "WalkPlannerBusyError";
  }
}

/** The call's deadline passed before the service answered. */
export class WalkPlannerTimeoutError extends Error {
  constructor(
    readonly operation: WalkPlannerOperation,
    readonly timeoutMs: number
  ) {
    super(`walk-planner ${operation} timed out after ${timeoutMs} ms`);
    this.name = "WalkPlannerTimeoutError";
  }
}

type CallPolicy = {
  timeoutMs: number;
  // false: a plan — after the request went out it is never re-sent.
  resendable: boolean;
  // Retries of 503 busy (each after Retry-After + jitter, within the deadline).
  busyRetries: number;
  // Counts against the service's load guard → takes a semaphore slot.
  guarded: boolean;
};

// A plan is 0.3–4 s of CPU (6.5 s when two share a worker) plus up to 6 s of
// street routing; an edit is milliseconds plus the same routing budget.
const CALL_POLICY: Record<WalkPlannerOperation, CallPolicy> = {
  plan: { timeoutMs: 20_000, resendable: false, busyRetries: 2, guarded: true },
  schedule: { timeoutMs: 10_000, resendable: true, busyRetries: 1, guarded: true },
  insert: { timeoutMs: 10_000, resendable: true, busyRetries: 1, guarded: true },
  config: { timeoutMs: 5_000, resendable: true, busyRetries: 1, guarded: false },
  search: { timeoutMs: 5_000, resendable: true, busyRetries: 1, guarded: false },
  place: { timeoutMs: 5_000, resendable: true, busyRetries: 1, guarded: false }
};

// The container refuses connections for ~5–10 s during a restart or a bundle
// switch: one more try after a short pause.
const REFUSED_RETRY_DELAY_MS = 2_000;
const BUSY_JITTER_MS = 500;
const DEFAULT_RETRY_AFTER_SECONDS = 2;
const QUEUE_WAIT_MS = 5_000;

export type WalkPlannerClientOptions = {
  baseUrl?: string;
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  random?: () => number;
  // Gateway-side slots for plan / schedule / insert, and how long one waits.
  maxConcurrent?: number;
  queueWaitMs?: number;
};

export function createWalkPlannerClient(
  options: WalkPlannerClientOptions = {}
): WalkPlannerClient {
  const baseUrl =
    "baseUrl" in options ? options.baseUrl : env.WALK_PLANNER_URL;
  const transport = {
    fetch: options.fetch ?? fetch,
    sleep: options.sleep ?? defaultSleep,
    now: options.now ?? Date.now,
    random: options.random ?? Math.random
  };
  const guard = new Semaphore(
    options.maxConcurrent ?? env.WALK_PLANNER_MAX_CONCURRENT
  );
  const queueWaitMs = options.queueWaitMs ?? QUEUE_WAIT_MS;

  const call = (
    operation: WalkPlannerOperation,
    method: "GET" | "POST",
    path: string,
    query: WalkPlannerQuery,
    body: unknown,
    context: WalkPlannerCallContext | undefined
  ) =>
    measureDependencyMetric(
      {
        dependency: "walk-planner",
        operation: "http",
        name: `walks_${operation}`
      },
      async () => {
        const httpCall = {
          method,
          path,
          query,
          body,
          requestId: context?.requestId
        };

        if (!CALL_POLICY[operation].guarded) {
          return callWithPolicy(transport, baseUrl, operation, httpCall);
        }

        if (!(await guard.acquire(queueWaitMs))) {
          throw new WalkPlannerBusyError(operation);
        }

        try {
          return await callWithPolicy(transport, baseUrl, operation, httpCall);
        } finally {
          guard.release();
        }
      }
    );

  return {
    config: (query, context) =>
      call("config", "GET", "/v1/walks/config", query, undefined, context),
    plan: (body, query, context) =>
      call("plan", "POST", "/v1/walks/plan", query, body, context),
    schedule: (body, query, context) =>
      call("schedule", "POST", "/v1/walks/schedule", query, body, context),
    insert: (body, query, context) =>
      call("insert", "POST", "/v1/walks/insert", query, body, context),
    searchPlaces: (query, context) =>
      call(
        "search",
        "GET",
        "/v1/walks/places/search",
        query,
        undefined,
        context
      ),
    place: (sourceId, query, context) =>
      call(
        "place",
        "GET",
        `/v1/walks/places/${encodeURIComponent(sourceId)}`,
        query,
        undefined,
        context
      )
  };
}

export const walkPlannerClient = createWalkPlannerClient();

type Transport = Required<
  Pick<WalkPlannerClientOptions, "fetch" | "sleep" | "now" | "random">
>;

type HttpCall = {
  method: "GET" | "POST";
  path: string;
  query: WalkPlannerQuery;
  body: unknown;
  requestId?: string;
};

type AttemptOutcome =
  | { kind: "reply"; reply: WalkPlannerReply }
  | { kind: "refused" }
  | { kind: "reset" }
  | { kind: "timeout" };

async function callWithPolicy(
  transport: Transport,
  baseUrl: string | undefined,
  operation: WalkPlannerOperation,
  httpCall: HttpCall
): Promise<WalkPlannerReply> {
  if (!baseUrl) {
    throw new WalkPlannerUnavailableError("WALK_PLANNER_URL is not configured");
  }

  const policy = CALL_POLICY[operation];
  const deadline = transport.now() + policy.timeoutMs;
  const url = buildUrl(baseUrl, httpCall.path, httpCall.query);
  let connectionRetried = false;
  let busyRetries = 0;
  let notReadyRetried = false;

  for (;;) {
    const outcome = await attempt(
      transport,
      url,
      httpCall,
      deadline - transport.now()
    );

    if (outcome.kind === "timeout") {
      throw new WalkPlannerTimeoutError(operation, policy.timeoutMs);
    }

    let delayMs: number | undefined;

    if (outcome.kind === "refused" || outcome.kind === "reset") {
      // A reset may come after the service got the request: only an operation
      // that may be re-sent tries again.
      const mayRetry =
        !connectionRetried &&
        (outcome.kind === "refused" || policy.resendable);

      if (mayRetry) {
        connectionRetried = true;
        delayMs = REFUSED_RETRY_DELAY_MS;
      }
    } else {
      const { reply } = outcome;
      const code = errorCode(reply.body);

      if (reply.status === 503 && code === "busy") {
        if (busyRetries < policy.busyRetries) {
          busyRetries += 1;
          delayMs =
            retryAfterMs(reply.retryAfter) +
            Math.round(transport.random() * BUSY_JITTER_MS);
        }
      } else if (reply.status === 503 && code === "not_ready") {
        if (!notReadyRetried) {
          notReadyRetried = true;
          delayMs = retryAfterMs(reply.retryAfter);
        }
      }

      // Nothing to retry, retries used up, or no time left for the pause:
      // the app gets the service's answer (busy passes through with Retry-After).
      if (delayMs === undefined || delayMs >= deadline - transport.now()) {
        return reply;
      }
    }

    if (delayMs === undefined || delayMs >= deadline - transport.now()) {
      throw new WalkPlannerUnavailableError(
        `walk-planner ${operation}: connection ${outcome.kind}`
      );
    }

    await transport.sleep(delayMs);
  }
}

async function attempt(
  transport: Transport,
  url: URL,
  httpCall: HttpCall,
  remainingMs: number
): Promise<AttemptOutcome> {
  if (remainingMs <= 0) {
    return { kind: "timeout" };
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), remainingMs);

  try {
    const headers: Record<string, string> = { accept: "application/json" };

    if (httpCall.body !== undefined) {
      headers["content-type"] = "application/json";
    }

    if (httpCall.requestId) {
      headers["x-request-id"] = httpCall.requestId;
    }

    const response = await transport.fetch(url, {
      method: httpCall.method,
      headers,
      body:
        httpCall.body === undefined ? undefined : JSON.stringify(httpCall.body),
      signal: controller.signal
    });
    const text = await response.text();
    let body: unknown;

    try {
      body = JSON.parse(text);
    } catch {
      throw new WalkPlannerUnavailableError(
        `walk-planner answered ${response.status} with a non-JSON body`
      );
    }

    return {
      kind: "reply",
      reply: {
        status: response.status,
        body,
        retryAfter: response.headers.get("retry-after") ?? undefined
      }
    };
  } catch (error) {
    if (error instanceof WalkPlannerUnavailableError) {
      throw error;
    }

    if (controller.signal.aborted) {
      return { kind: "timeout" };
    }

    return isConnectionRefused(error) ? { kind: "refused" } : { kind: "reset" };
  } finally {
    clearTimeout(timeout);
  }
}

function buildUrl(baseUrl: string, path: string, query: WalkPlannerQuery) {
  const url = new URL(path, baseUrl);

  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  return url;
}

function errorCode(body: unknown) {
  if (typeof body !== "object" || body === null) return undefined;
  const error = (body as { error?: unknown }).error;
  if (typeof error !== "object" || error === null) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

function retryAfterMs(retryAfter: string | undefined) {
  const seconds = Number(retryAfter);
  return (
    (Number.isFinite(seconds) && seconds >= 0
      ? seconds
      : DEFAULT_RETRY_AFTER_SECONDS) * 1000
  );
}

// The request never left: refused, or the compose DNS name does not resolve
// (container down / being recreated).
const NOT_CONNECTED_CODES = new Set(["ECONNREFUSED", "ENOTFOUND", "EAI_AGAIN"]);

// undici (Node's fetch) wraps socket errors: TypeError("fetch failed") with
// the system error as `cause`.
function isConnectionRefused(error: unknown) {
  const cause = (error as { cause?: { code?: unknown } } | null)?.cause;
  return typeof cause?.code === "string" && NOT_CONNECTED_CODES.has(cause.code);
}

function defaultSleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}
