import {
  Counter,
  Gauge,
  Histogram,
  Registry,
  collectDefaultMetrics
} from "prom-client";
import { getDbPoolStats } from "../lib/db.js";

// Real Prometheus metrics for load testing. Lives alongside the Loki log metrics
// (metrics.ts): logs stay for detailed traces, Prometheus gives accurate
// realtime percentiles + Node runtime signals for the load-test dashboard.

export const metricsRegistry = new Registry();

// Node runtime: event-loop lag (the silent killer under load), heap/RSS, GC, handles.
collectDefaultMetrics({ register: metricsRegistry });

const LATENCY_BUCKETS = [
  0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10
];

const httpRequestDuration = new Histogram({
  name: "http_request_duration_seconds",
  help: "HTTP request duration in seconds.",
  labelNames: ["method", "route", "status_code"],
  buckets: LATENCY_BUCKETS,
  registers: [metricsRegistry]
});

const httpRequestsTotal = new Counter({
  name: "http_requests_total",
  help: "Total HTTP requests.",
  labelNames: ["method", "route", "status_code"],
  registers: [metricsRegistry]
});

const dependencyDuration = new Histogram({
  name: "dependency_duration_seconds",
  help: "External dependency call duration in seconds.",
  labelNames: ["dependency", "operation", "success"],
  buckets: LATENCY_BUCKETS,
  registers: [metricsRegistry]
});

const cacheEventsTotal = new Counter({
  name: "cache_events_total",
  help: "Cache events by status (hit/miss/set/error/bypass).",
  labelNames: ["cache", "status"],
  registers: [metricsRegistry]
});

// Direct Postgres pool (src/lib/db.ts), read on every scrape. waiting > 0 for
// long means PG_POOL_MAX is the bottleneck; requests start getting 503 once a
// wait exceeds PG_POOL_CONNECTION_TIMEOUT_MS.
new Gauge({
  name: "pg_pool_clients",
  help: "Postgres pool clients, idle and checked out.",
  registers: [metricsRegistry],
  collect() {
    this.set(getDbPoolStats().total);
  }
});

new Gauge({
  name: "pg_pool_idle_clients",
  help: "Postgres pool clients idle and ready to hand out.",
  registers: [metricsRegistry],
  collect() {
    this.set(getDbPoolStats().idle);
  }
});

new Gauge({
  name: "pg_pool_waiting_requests",
  help: "Queries queued waiting for a Postgres pool client.",
  registers: [metricsRegistry],
  collect() {
    this.set(getDbPoolStats().waiting);
  }
});

export function observeHttpRequest(
  method: string,
  route: string,
  statusCode: number,
  durationMs: number
) {
  const labels = { method, route, status_code: String(statusCode) };
  httpRequestDuration.observe(labels, durationMs / 1000);
  httpRequestsTotal.inc(labels);
}

export function observeDependency(
  dependency: string,
  operation: string,
  success: boolean,
  durationMs: number
) {
  dependencyDuration.observe(
    { dependency, operation, success: String(success) },
    durationMs / 1000
  );
}

export function observeCacheEvent(cache: string, status: string) {
  cacheEventsTotal.inc({ cache, status });
}

export function renderMetrics() {
  return metricsRegistry.metrics();
}

export const metricsContentType = metricsRegistry.contentType;
