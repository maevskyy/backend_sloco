# TASKS 23: Backend Metrics and Latency Breakdown

## Summary

Add simple standardized backend metrics without adding OpenTelemetry,
Prometheus, Tempo, or new infrastructure.

Current infra already ships backend JSON logs to Grafana Loki through Alloy.
For this task, metrics are emitted as structured log events. Grafana can query
and chart them with LogQL.

Goal:

```text
answer basic production questions quickly
```

Examples:

```text
Is the backend slow?
Is Supabase slow?
Is the response payload huge?
Are errors increasing?
Which route is slow?
```

This is not full tracing. It is a pragmatic MVP metrics layer.

## Current Status

```text
Implemented
```

This task supersedes the broader backlog item:

```text
docs/tasks/TBD_TRACING_LATENCY_BREAKDOWN.md
```

The TBD remains the future OpenTelemetry/Tempo direction.

## Design Principle

Do not make custom metrics per endpoint unless there is a real reason.

Start with two standard metric event types:

```text
http_request_metric
dependency_metric
```

Then every important question is answered from the same shape.

## Metric Event 1: HTTP Request Metric

Emit once for every backend request.

Example:

```json
{
  "eventType": "metric",
  "metricType": "http_request",
  "route": "/v1/map/places",
  "method": "GET",
  "statusCode": 200,
  "durationMs": 2388,
  "responseBytes": 1795850,
  "requestId": "req-123"
}
```

Fields:

```text
eventType         "metric"
metricType        "http_request"
route             stable route/path without query params
method            HTTP method
statusCode        HTTP status
durationMs        total Fastify request time
responseBytes     content-length when available, approximate otherwise
requestId         Fastify request id
```

Answers:

```text
Is backend slow?
Which route is slow?
Are 5xx/4xx increasing?
Are payloads too large?
```

## Metric Event 2: Dependency Metric

Emit around external calls made by backend.

Initial dependency:

```text
Supabase
```

Example:

```json
{
  "eventType": "metric",
  "metricType": "dependency",
  "dependency": "supabase",
  "operation": "rpc",
  "name": "places_in_bbox",
  "route": "/v1/map/places",
  "durationMs": 2100,
  "success": true,
  "rowsCount": 400,
  "requestId": "req-123"
}
```

Fields:

```text
eventType         "metric"
metricType        "dependency"
dependency        "supabase" | future "ml-service" | future "storage"
operation         "rpc" | "select" | "insert" | "upsert" | "http"
name              stable operation name, e.g. "places_in_bbox"
route             current HTTP route/path when available
durationMs        dependency call time
success           boolean
rowsCount         optional, when easy and cheap
requestId         Fastify request id when available
```

Answers:

```text
Is Supabase slow?
Which Supabase operation is slow?
Is backend waiting on an external dependency?
```

## Optional Metric Event 3: Payload Metric

Only if `responseBytes` is not reliable enough from Fastify/Nginx.

Example:

```json
{
  "eventType": "metric",
  "metricType": "payload",
  "route": "/v1/map/places",
  "jsonBytes": 1795850,
  "itemsCount": 100,
  "requestId": "req-123"
}
```

Use this sparingly. Do not `JSON.stringify` huge responses only for metrics if
it adds meaningful latency.

## Implementation Shape

Add small shared helpers:

```text
src/observability/metrics.ts
```

Responsibilities:

- emit HTTP request metric logs;
- emit dependency metric logs;
- measure async functions;
- keep metric field names stable.

Do not put business-specific logic in the helper.

Possible API:

```ts
logHttpRequestMetric(request, reply)

measureDependencyMetric(request, {
  dependency: "supabase",
  operation: "rpc",
  name: "places_in_bbox"
}, async () => {
  return supabase.rpc(...)
})
```

If passing `request` into every store is too invasive, start with route/path as
optional and use `"unknown"` when unavailable. Do not over-abstract.

## Initial Instrumentation

### HTTP

Replace or extend current request completion logging:

```text
src/config/logger.ts
```

Current log:

```text
eventType=response/request
```

Add metric log:

```text
eventType=metric
metricType=http_request
```

This applies to all routes:

- `/v1/health`
- `/v1/health/supabase`
- `/v1/map/places`
- `/v1/me`
- `/v1/me/saved`
- Swagger routes if not hidden/filterable

### Supabase

Instrument the most important store calls first:

```text
src/modules/map/stores/map.store.ts
src/modules/health/stores or health check path
src/modules/saved-places/stores/saved-places.store.ts
src/modules/auth/*
```

Start with map if we need speed, but design the helper so other stores use the
same metric shape.

## Grafana Dashboard

Create dashboard-as-code:

```text
grafana/dashboards/backend-metrics.json
```

Panels:

1. HTTP Request Metrics

```logql
{service="backend"} | json | eventType = "metric" | metricType = "http_request"
```

2. Slow HTTP Requests

```logql
{service="backend"} | json | eventType = "metric" | metricType = "http_request" | durationMs > 1000
```

3. Supabase Dependency Metrics

```logql
{service="backend"} | json | eventType = "metric" | metricType = "dependency" | dependency = "supabase"
```

4. Slow Supabase Calls

```logql
{service="backend"} | json | eventType = "metric" | metricType = "dependency" | dependency = "supabase" | durationMs > 1000
```

5. Large Responses

```logql
{service="backend"} | json | eventType = "metric" | metricType = "http_request" | responseBytes > 1000000
```

If Loki unwrap works well, add time-series panels:

```logql
avg_over_time({service="backend"} | json | eventType = "metric" | metricType = "http_request" | unwrap durationMs [5m])
```

```logql
avg_over_time({service="backend"} | json | eventType = "metric" | metricType = "dependency" | dependency = "supabase" | unwrap durationMs [5m])
```

## Debug Runbook

After deploy:

```bash
curl -s -o /tmp/map-1.json -w 'limit=1 total=%{time_total}s size=%{size_download}B code=%{http_code}\n' \
'http://127.0.0.1:3000/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=1'
```

```bash
curl -s -o /tmp/map-100.json -w 'limit=100 total=%{time_total}s size=%{size_download}B code=%{http_code}\n' \
'http://127.0.0.1:3000/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=100'
```

Then in Grafana:

```logql
{service="backend"} | json | eventType = "metric"
```

Look at:

```text
http_request.durationMs
http_request.responseBytes
dependency.supabase.durationMs
```

Interpretation:

```text
http duration high + supabase duration high
  => Supabase/PostgREST/network to backend is the bottleneck.

http duration high + supabase duration low + responseBytes high
  => backend serialization / Fastify / transfer is the bottleneck.

http duration high + supabase duration low + responseBytes low
  => backend code path is suspicious.

http duration low locally + public curl high
  => Nginx/client/network is suspicious.
```

## Expected Finding For Current Map Problem

Likely:

```text
Supabase dependency duration is high
responseBytes is high
```

Meaning:

```text
Postgres execution is fast, but moving/serializing huge wide rows through
Supabase API and backend is slow.
```

Product fix:

```text
/v1/map/places returns preview objects
/v1/places/:id returns full place details
```

Map preview should not ship giant blobs:

```text
google_details
apify_details
ai_details
photo_details
full opening_hours
```

unless explicitly requested.

## Future Phase: OpenTelemetry

OpenTelemetry remains the proper future solution once:

- frontend traffic grows;
- ML service is connected;
- place details and photo flows exist;
- we need cross-service traces.

Future stack:

```text
OpenTelemetry SDK -> OTLP -> Grafana Cloud Tempo
```

For now:

```text
structured metric logs -> Loki -> Grafana dashboard
```

## Test Plan

Code checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual checks:

```bash
curl https://sloco.pp.ua/v1/health
curl "https://sloco.pp.ua/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=1"
curl "https://sloco.pp.ua/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=100"
```

Grafana checks:

- HTTP request metric logs appear.
- Supabase dependency metric logs appear.
- Slow requests are filterable.
- Large responses are filterable.
- No secrets or full response bodies are logged.

Current LogQL for raw inspection:

```logql
{service="backend"} | json | eventType = "metric"
```

HTTP only:

```logql
{service="backend"} | json | eventType = "metric" | metricType = "http_request"
```

Supabase only:

```logql
{service="backend"} | json | eventType = "metric" | metricType = "dependency" | dependency = "supabase"
```

## Acceptance Criteria

- All routes emit standardized HTTP request metric logs.
- Supabase calls in important stores emit standardized dependency metric logs.
- `/v1/map/places` can be diagnosed with:
  - total request duration;
  - Supabase duration;
  - response size.
- Grafana dashboard or documented LogQL can answer:
  - is backend slow?
  - is Supabase slow?
  - is payload huge?
  - are errors increasing?
- No new observability infrastructure is required.
