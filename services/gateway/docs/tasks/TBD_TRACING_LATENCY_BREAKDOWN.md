# TBD: Tracing and Latency Breakdown

## Summary

Add distributed tracing so we can see where backend time is actually spent:

- client -> backend request handling;
- backend -> Supabase/Postgres query;
- Supabase/Postgres -> backend response;
- backend serialization + response -> client.

The goal is to stop guessing about bottlenecks. For every important endpoint we
should be able to answer:

```text
Was this request slow because of backend code, database latency, external IO, or response size?
```

## Status

```text
TBD / backlog
```

Do not implement this before the core MVP endpoints settle. Tracing becomes more
valuable once we have several real endpoints and real frontend traffic.

## Goals

- Add request traces for Fastify endpoints.
- Add child spans for database/RPC calls.
- Add child spans for expensive service operations.
- Correlate traces with logs through `traceId` and `requestId`.
- Export traces to Grafana Cloud Tempo or an equivalent tracing backend.
- Build basic latency dashboards:
  - total request duration;
  - database duration;
  - service/business-logic duration;
  - response serialization duration;
  - p95/p99 by endpoint.

## Non-Goals

- Do not build a custom tracing system.
- Do not replace logs.
- Do not replace metrics.
- Do not add frontend/mobile tracing in the first pass.
- Do not trace every tiny helper function.
- Do not self-host Tempo unless Grafana Cloud limits become a real problem.

## Recommended Stack

Use OpenTelemetry in the Node backend.

Recommended target for MVP:

```text
Fastify + OpenTelemetry SDK -> OTLP exporter -> Grafana Cloud Tempo
```

Why:

- OpenTelemetry is vendor-neutral;
- Grafana Cloud already fits our logs/metrics direction;
- traces can link to logs later through `traceId`;
- if we leave Grafana later, the instrumentation can mostly stay.

## Trace Shape

For a request like:

```text
GET /v1/map/places
```

The trace should look conceptually like:

```text
HTTP GET /v1/map/places
  map.controller.getPlaces
    map.service.getPlaces
      map.store.findPlacesInBbox
        supabase.rpc places_in_bbox
      map.ranking.scoreAndSort
      map.response.serialize
```

This gives us a waterfall:

```text
client -> backend        total HTTP request span
backend -> db            Supabase/Postgres child span
db -> backend            included in DB child span duration
backend -> client        response serialization + Fastify response timing
```

## Span Naming

Use stable, boring names:

```text
http.request GET /v1/map/places
map.controller.getPlaces
map.service.getPlaces
map.store.findPlacesInBbox
supabase.rpc places_in_bbox
map.ranking.scoreAndSort
map.response.serialize
```

Avoid names with raw IDs, coordinates, user input, or full URLs.

## Required Span Attributes

HTTP/request span:

```text
http.method
http.route
http.status_code
request.id
user.id             only when authenticated
```

Map endpoint attributes:

```text
map.zoom
map.limit
map.places_count
map.featured_count
map.dot_count
```

Database span:

```text
db.system=postgresql
db.operation=rpc
db.rpc.name=places_in_bbox
db.rows_returned
```

Error attributes:

```text
error=true
exception.type
exception.message
```

Do not put secrets, auth tokens, service-role keys, full request bodies, or raw
large responses into span attributes.

## Log Correlation

Once tracing exists, structured logs should include:

```json
{
  "requestId": "req-123",
  "traceId": "abc...",
  "spanId": "def...",
  "path": "/v1/map/places",
  "statusCode": 200,
  "responseTimeMs": 120
}
```

This lets us go:

```text
Grafana log line -> trace waterfall -> exact slow child span
```

## Metrics From Traces

Tracing should feed practical dashboards, not just pretty waterfalls.

Useful panels:

- p95 request duration by route;
- p99 request duration by route;
- p95 database duration by RPC/query;
- slowest traces in the last hour;
- error traces by route;
- database time as percentage of total request time.

Example question we should answer:

```text
/v1/map/places p95 is 900ms.
Is 800ms in Supabase RPC, or 800ms in backend ranking/serialization?
```

## Implementation Plan

### Step 1: Add OpenTelemetry Bootstrap

Add a backend tracing bootstrap file, for example:

```text
src/telemetry/tracing.ts
```

It should:

- initialize OpenTelemetry before Fastify starts;
- configure service name:

  ```text
  backend-sloco
  ```

- configure environment:

  ```text
  production | development
  ```

- export traces through OTLP.

### Step 2: Add Env Vars

Add:

```text
OTEL_ENABLED=false
OTEL_SERVICE_NAME=backend-sloco
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_EXPORTER_OTLP_HEADERS=
```

Keep tracing disabled locally by default unless explicitly enabled.

### Step 3: Instrument Fastify Requests

Use OpenTelemetry Fastify/HTTP instrumentation if it works cleanly with our
Fastify version.

If automatic instrumentation is too noisy, start with a small manual wrapper
around route handlers.

### Step 4: Instrument Stores

Add spans around backend calls that cross process/network boundaries:

- Supabase client calls;
- Supabase RPC calls;
- future ML service HTTP calls;
- future external place/photo provider calls.

Stores are the right layer for DB spans because controllers should not know
about DB details.

### Step 5: Add Service-Level Spans For Expensive Logic

Only add spans where the work can realistically become expensive:

- map ranking;
- personalization scoring;
- recommendation generation;
- large response shaping.

Do not span every mapper/helper.

### Step 6: Connect Grafana Cloud Tempo

Use Grafana Cloud tracing endpoint and token.

Store credentials only in:

```text
/opt/backend_sloco/.env
GitHub Actions secrets if needed
```

Do not commit tokens.

### Step 7: Dashboard

Create dashboard-as-code later, similar to logs dashboards:

```text
grafana/dashboards/backend-traces.json
```

Panels:

- slowest traces;
- p95 request duration;
- p95 DB/RPC duration;
- route latency table;
- DB percentage of request time;
- error traces.

## Important Design Rules

- Tracing belongs to infrastructure/telemetry, not business logic.
- Controllers can create high-level route spans if needed.
- Stores own DB/external-IO spans.
- Services own expensive business-operation spans.
- Never log or trace secrets.
- Keep span names low-cardinality.
- Do not put raw bbox coordinates into labels/attributes unless we intentionally
  bucket them. High-cardinality labels make observability expensive and noisy.

## Test Plan

Local:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual local smoke:

```bash
OTEL_ENABLED=false pnpm dev
curl http://127.0.0.1:3000/v1/health
```

Production smoke:

```bash
curl https://sloco.pp.ua/v1/health
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

Grafana checks:

- traces arrive for `/v1/health`;
- traces arrive for `/v1/map/places`;
- `/v1/map/places` trace contains a DB/RPC child span;
- slow request logs include `traceId`;
- trace can be opened from logs.

## Acceptance Criteria

- Production backend exports traces when `OTEL_ENABLED=true`.
- Logs include `traceId` for traced requests.
- `/v1/map/places` has separate spans for:
  - total request;
  - Supabase/RPC call;
  - ranking/scoring;
  - response shaping when meaningful.
- Grafana can show p95/p99 request latency by route.
- Grafana can show database latency separately from total request latency.
- No secrets or large response bodies are present in spans.

