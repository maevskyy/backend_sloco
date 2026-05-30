# TASKS 6: Polished Backend Logs

## Goal

Make backend logs beautiful, compact, and useful for debugging.

Logs should be pleasant enough for local development and structured enough for
Grafana Cloud. The goal is not a full observability platform yet. The goal is a
clear debugging instrument for the MVP backend.

## Current Problem

Fastify currently uses default Pino request logging.

That produces two separate logs per request:

- `incoming request`
- `request completed`

This is technically correct, but annoying in Grafana:

- method/url live in one log line;
- status/response time live in another log line;
- JSON is too verbose;
- Explore feels noisy;
- non-engineers will not enjoy reading it.

We want one useful request completion log that includes the important request
context in a compact shape.

## Desired Log Philosophy

Use logs for:

- request debugging;
- error investigation;
- backend behavior visibility;
- quick production checks.

Do not use logs for:

- analytics events;
- long-term product metrics;
- user behavior dashboards;
- full tracing.

Keep logs structured JSON in production. Make them pretty only in development.

## Log Levels

Use these levels:

```text
debug -> local details, disabled in production by default
info  -> successful application/request events
warn  -> recoverable problems or suspicious inputs
error -> failed requests, thrown errors, external service failures
fatal -> startup crash / unrecoverable process-level failure
```

Default levels:

```text
development -> debug
test        -> silent
production  -> info
```

## Request Logging Shape

For every completed request, produce one compact structured log:

```json
{
  "level": "info",
  "time": "2026-05-30T09:25:52.556Z",
  "reqId": "req-br",
  "method": "GET",
  "url": "/map/places",
  "path": "/map/places",
  "statusCode": 200,
  "responseTimeMs": 115,
  "msg": "GET /map/places 200 115ms"
}
```

For failed requests:

```json
{
  "level": "error",
  "time": "2026-05-30T09:25:52.556Z",
  "reqId": "req-br",
  "method": "GET",
  "url": "/map/places",
  "path": "/map/places",
  "statusCode": 500,
  "responseTimeMs": 115,
  "err": {
    "type": "Error",
    "message": "Supabase query failed",
    "stack": "..."
  },
  "msg": "GET /map/places 500 115ms"
}
```

Rules:

- Log only request completion by default.
- Do not log `incoming request` in production.
- Keep query string in `url` for now because bbox/debugging is useful.
- Also include `path` without query string for easier Grafana grouping.
- Round `responseTimeMs` to a whole number.
- Use `warn` for 4xx.
- Use `error` for 5xx.
- Use `info` for 2xx/3xx.

## Development Output

Add `pino-pretty` for development only.

Local logs should be readable and colored:

```text
INFO  GET /health 200 3ms
INFO  GET /map/places 200 115ms
WARN  GET /map/places 400 2ms
ERROR GET /map/places 500 21ms Supabase query failed
```

Implementation:

- Use `pino-pretty` transport only when `NODE_ENV=development`.
- Do not use pretty logs in production.
- Do not write app logs to files.

## Test Output

Tests should not print noisy expected errors.

Implementation:

- Disable logger in `NODE_ENV=test`.
- Keep route behavior tests unchanged.
- If a test specifically needs logs later, inject logger config explicitly.

## Production Output

Production logs remain JSON to stdout/stderr.

Docker collects stdout/stderr.

Alloy ships Docker logs to Grafana Cloud Loki.

Docker log rotation must remain enabled:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

## Grafana Usage

Primary dashboard should not use raw Explore as the main UX.

Useful LogQL queries after polished logs:

All request logs:

```logql
{service="backend"} | json | path != ""
```

Map endpoint:

```logql
{service="backend"} | json | path = "/map/places"
```

Errors:

```logql
{service="backend"} | json | statusCode >= 500
```

Bad requests:

```logql
{service="backend"} | json | statusCode >= 400 and statusCode < 500
```

Slow requests:

```logql
{service="backend"} | json | responseTimeMs > 500
```

## Implementation Plan

1. Add `pino-pretty` as a dev dependency.

2. Create logger config:

   ```text
   src/config/logger.ts
   ```

3. Replace `Fastify({ logger: true })` with:

   ```ts
   Fastify({
     logger: createLoggerOptions(env.NODE_ENV)
   })
   ```

4. Configure Fastify/Pino:

   - dev: pretty transport, level `debug`;
   - test: logger disabled;
   - prod: JSON logs, level `info`;
   - request completion log includes method/url/path/status/response time.

5. Avoid duplicate request logs:

   - suppress default incoming request log if possible;
   - keep one completion log as the primary request event.

6. Update Docker compose template if log rotation is not already present.

7. Update logging docs:

   - how to read local logs;
   - how to query Grafana;
   - which logs to expect.

## Testing

Local checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual local check:

```bash
pnpm dev
curl http://127.0.0.1:3000/health
curl "http://127.0.0.1:3000/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100"
```

Expected local logs:

```text
GET /health 200
GET /map/places 200
```

Manual production check after deploy:

```bash
curl http://52.18.13.69/health
curl "http://52.18.13.69/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100"
```

Grafana check:

```logql
{service="backend"} | json | path != ""
```

## Acceptance Criteria

- Development logs are colored and readable.
- Test logs are quiet.
- Production logs are structured JSON.
- Each request produces one primary completion log.
- Request logs include method, url, path, statusCode, responseTimeMs, reqId.
- 2xx/3xx request logs are info.
- 4xx request logs are warn.
- 5xx request logs are error.
- Grafana can query by `path`, `statusCode`, and `responseTimeMs`.
- Docker log rotation remains configured.

## Future Follow-Ups

- Add request ID propagation from incoming headers.
- Add user id when auth exists.
- Add structured app events for recommendation scoring.
- Build a Grafana dashboard for backend logs.
- Add alerts for 5xx spikes and high latency.

