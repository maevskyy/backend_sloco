import { AsyncLocalStorage } from "node:async_hooks";
import { performance } from "node:perf_hooks";
import type {
  FastifyBaseLogger,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { LogEvent, LogEventType } from "../config/log-events.js";
import type { CacheMetricInput } from "../lib/cache/cache-store.js";

type RequestMetricContext = {
  log: FastifyBaseLogger;
  method: string;
  path: string;
  requestId: string;
};

type DependencyMetricInput = {
  dependency: "supabase" | "ml-service" | "storage" | string;
  operation: "auth" | "delete" | "http" | "insert" | "rpc" | "select" | "update" | "upsert" | string;
  name: string;
  rowsCount?: number;
};

const requestMetricContext = new AsyncLocalStorage<RequestMetricContext>();

export function enterRequestMetricContext(request: FastifyRequest) {
  requestMetricContext.enterWith({
    log: request.log,
    method: request.method,
    path: getRequestPath(request.url),
    requestId: request.id
  });
}

export function logHttpRequestMetric(
  request: FastifyRequest,
  reply: FastifyReply
) {
  const path = getRequestPath(request.url);
  const responseBytes = getResponseBytes(reply);

  request.log.info(
    {
      eventType: LogEventType.Metric,
      event: LogEvent.HttpRequestMetric,
      metricType: "http_request",
      method: request.method,
      path,
      route: path,
      statusCode: reply.statusCode,
      durationMs: Math.round(reply.elapsedTime),
      responseBytes,
      requestId: request.id
    },
    `METRIC http_request ${request.method} ${path} ${reply.statusCode} ${Math.round(reply.elapsedTime)}ms`
  );
}

export async function measureDependencyMetric<T>(
  input: DependencyMetricInput,
  operation: () => Promise<T>,
  getRowsCount?: (result: T) => number | undefined
): Promise<T> {
  const startedAt = performance.now();

  try {
    const result = await operation();
    logDependencyMetric(input, Math.round(performance.now() - startedAt), true, getRowsCount?.(result));
    return result;
  } catch (error) {
    logDependencyMetric(input, Math.round(performance.now() - startedAt), false);
    throw error;
  }
}

export function logCacheMetric(input: CacheMetricInput) {
  const context = requestMetricContext.getStore();

  if (!context) {
    return;
  }

  context.log.info(
    {
      eventType: LogEventType.Metric,
      event: LogEvent.CacheMetric,
      metricType: "cache",
      cacheName: input.cacheName,
      cacheStatus: input.cacheStatus,
      keyPrefix: input.keyPrefix,
      path: context.path,
      route: context.path,
      method: context.method,
      durationMs: input.durationMs,
      requestId: context.requestId
    },
    `METRIC cache ${input.cacheName} ${input.cacheStatus} ${input.durationMs}ms`
  );
}

function logDependencyMetric(
  input: DependencyMetricInput,
  durationMs: number,
  success: boolean,
  rowsCount?: number
) {
  const context = requestMetricContext.getStore();

  if (!context) {
    return;
  }

  context.log.info(
    {
      eventType: LogEventType.Metric,
      event: LogEvent.DependencyMetric,
      metricType: "dependency",
      dependency: input.dependency,
      operation: input.operation,
      name: input.name,
      path: context.path,
      route: context.path,
      method: context.method,
      durationMs,
      success,
      rowsCount: input.rowsCount ?? rowsCount,
      requestId: context.requestId
    },
    `METRIC dependency ${input.dependency}.${input.operation}.${input.name} ${durationMs}ms`
  );
}

function getRequestPath(url: string) {
  return url.split("?")[0] || url;
}

function getResponseBytes(reply: FastifyReply) {
  const contentLength = reply.getHeader("content-length");

  if (typeof contentLength === "number") {
    return contentLength;
  }

  if (typeof contentLength === "string") {
    const parsed = Number(contentLength);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}
