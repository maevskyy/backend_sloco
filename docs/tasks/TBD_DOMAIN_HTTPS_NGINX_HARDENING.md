# TBD: Domain, HTTPS, and Nginx Hardening

## Summary

Move production API from raw IP + HTTP to a real domain + HTTPS.

Also harden Nginx so random internet scans like `/.env`, `/login`, `/wp-admin`,
and other garbage paths do not reach Fastify and do not pollute backend Grafana
logs.

Status:

```text
Ready / DNS verified for sloco.pp.ua
```

The domain is already selected:

```text
sloco.pp.ua
```

Before enabling HTTPS, verify that the domain reaches the Hetzner backend:

```bash
curl -I http://sloco.pp.ua/v1/health
```

Expected:

```text
HTTP/1.1 200 OK
Server: nginx/1.24.0 (Ubuntu)
```

Target production shape:

```text
https://sloco.pp.ua
```

Public backend routes should stay under:

```text
/v1/*
```

Everything else should be handled by Nginx before it reaches the app.

## Goals

- Buy or connect a domain.
- Point `sloco.pp.ua` to the current Hetzner server.
- Enable HTTPS with Certbot.
- Update production API URL in GitHub Actions.
- Update OpenAPI production server URL.
- Update frontend docs to use HTTPS.
- Keep Swagger working over HTTPS.
- Stop bot noise from reaching Fastify for non-API paths.

## Non-Goals

- Do not migrate hosting away from the current Hetzner server.
- Do not add Cloudflare Tunnel yet.
- Do not add auth yet.
- Do not add WAF/rate limiting yet.
- Do not make the frontend app production-ready in this task.

## Domain Setup

Production domain:

```text
sloco.pp.ua
```

## DNS Records

Create:

```text
Type: A
Name: @ / root / sloco.pp.ua
Value: 65.108.142.55
TTL: automatic or 300 seconds
```

Optional later:

```text
Type: CAA
Name: @ / root / sloco.pp.ua
Value: letsencrypt.org
```

Do not enable Cloudflare proxy until HTTPS is working directly on the server.
If using Cloudflare DNS, keep the record as DNS-only first.

## Server Preparation

SSH into the Hetzner server:

```bash
ssh <ssh-user>@65.108.142.55
```

Check current Nginx:

```bash
sudo nginx -t
sudo systemctl status nginx
```

Check app:

```bash
curl http://127.0.0.1:3000/v1/health
curl http://sloco.pp.ua/v1/health
```

## Install Certbot

On the server:

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx
```

Check:

```bash
certbot --version
```

## Nginx Config Before Certbot

Update:

```text
/etc/nginx/sites-available/backend_sloco
```

The source template lives in the repo:

```text
deploy/nginx/backend_sloco.conf
```

The production server does not automatically sync this file from the repo.
Apply it manually through SSH before running Certbot:

```bash
sudo nano /etc/nginx/sites-available/backend_sloco
```

Initial domain config:

```nginx
server {
    listen 80;
    server_name sloco.pp.ua;

    location ^~ /v1/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = / {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    location / {
        access_log off;
        return 444;
    }
}
```

Why:

- `/v1/*` goes to Fastify;
- `/` stays useful for a simple browser/smoke check;
- `/.env`, `/login`, `/wp-admin`, and random bot paths get closed by Nginx;
- backend logs become cleaner because non-API noise never reaches Docker logs.

Validate:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Check:

```bash
curl -I http://sloco.pp.ua/
curl -I http://sloco.pp.ua/v1/health
curl -I http://sloco.pp.ua/.env
```

Expected:

- `/` returns `200`;
- `/v1/health` returns `200`;
- `/.env` returns empty/closed connection because of `444`.

## Enable HTTPS

Run:

```bash
sudo certbot --nginx -d sloco.pp.ua
```

Choose redirect HTTP to HTTPS when Certbot asks.

After Certbot:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Check:

```bash
curl -I https://sloco.pp.ua/v1/health
curl -I https://sloco.pp.ua/v1/swagger/docs
curl -I https://sloco.pp.ua/v1/swagger/docs/static/index.css
curl -I https://sloco.pp.ua/v1/swagger/openapi.json
```

Expected:

- all return `200`;
- Swagger UI static assets return CSS/JS content types;
- no browser mixed-content errors.

## Cert Renewal

Certbot should create a systemd timer automatically.

Check:

```bash
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

## Update GitHub Actions

Repository secret:

```text
PRODUCTION_API_URL
```

Set to:

```text
https://sloco.pp.ua
```

The deploy workflow already checks:

```text
/v1/health
```

so only the secret should need to change.

## Update Backend Code

Update OpenAPI server URL in:

```text
src/config/swagger.ts
```

Production server should be:

```text
https://sloco.pp.ua
```

Keep local server:

```text
http://127.0.0.1:3000
```

## Update Docs

Update:

```text
README.md
AGENTS.md
docs/README.md
docs/DEPLOYMENT.md
docs/FRONTEND_MAP_API.md
grafana/README.md
docs/tasks/TASKS_7_GRAFANA_DASHBOARD_LOGS.md
```

Use this public base URL:

```text
https://sloco.pp.ua
```

Do not update old historical task docs unless they are actively used as
runbooks.

## Update Nginx Template In Repo

Update:

```text
deploy/nginx/backend_sloco.conf
```

Make it match the hardened production intent:

- real domain `sloco.pp.ua`;
- `/v1/` proxy;
- `/` ok response;
- everything else `444`;
- comment that Certbot will manage HTTPS blocks on the server.

## Grafana Impact

Alloy does not need changes.

Reason:

- Alloy collects Docker container stdout/stderr;
- Nginx route filtering happens before Fastify;
- valid `/v1/*` backend logs still flow to Loki.

Expected improvement:

- less Fastify noise from bot paths;
- `/login`, `/.env`, `/wp-admin` should disappear from backend logs;
- Nginx can still have its own access/error logs if needed.

Grafana dashboard queries do not need domain changes because they filter by:

```logql
{service="backend"}
```

and fields like:

```text
path="/v1/map/places"
```

Those paths stay the same.

## Security Notes

This task improves transport and noise, but it is not full security.

Still needed later:

- auth for user-owned data;
- rate limiting;
- request size limits;
- CORS policy tightened from `origin: true`;
- basic WAF rules if needed;
- hiding Swagger in production if API becomes sensitive.

For current MVP, public Swagger is acceptable because current endpoints are
public and contain no user-owned private data.

## Test Plan

Local code checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Server checks before HTTPS:

```bash
curl -I http://sloco.pp.ua/
curl -I http://sloco.pp.ua/v1/health
curl -I http://sloco.pp.ua/.env
```

Server checks after HTTPS:

```bash
curl -I https://sloco.pp.ua/
curl -I https://sloco.pp.ua/v1/health
curl -I https://sloco.pp.ua/v1/swagger/docs
curl -I https://sloco.pp.ua/v1/swagger/docs/static/index.css
curl -I https://sloco.pp.ua/v1/swagger/openapi.json
```

Deploy check:

```bash
Run GitHub Actions Deploy Production
```

Browser check:

```text
https://sloco.pp.ua/v1/swagger/docs
```

Grafana check:

Generate traffic:

```bash
curl https://sloco.pp.ua/v1/health
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
curl -I https://sloco.pp.ua/.env
```

Expected:

- `/v1/health` and `/v1/map/places` appear in backend Grafana logs;
- `/.env` does not appear in backend Grafana logs;
- Swagger UI opens and loads CSS/JS correctly.

## Rollback Plan

If HTTPS breaks:

1. Keep DNS pointing to the server.
2. Temporarily serve HTTP by restoring the previous Nginx config.
3. Run:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

4. Change `PRODUCTION_API_URL` back temporarily to:

   ```text
   http://65.108.142.55
   ```

5. Redeploy if needed.

Do not delete certificates unless they are actively causing Nginx config issues.

## Acceptance Criteria

- Domain exists.
- `sloco.pp.ua` resolves to `65.108.142.55`.
- `https://sloco.pp.ua/v1/health` returns `200`.
- `https://sloco.pp.ua/v1/swagger/docs` loads in browser.
- `https://sloco.pp.ua/v1/swagger/openapi.json` returns valid OpenAPI JSON.
- GitHub deploy healthcheck uses HTTPS domain through `PRODUCTION_API_URL`.
- Nginx blocks random non-`/v1` paths before Fastify.
- Grafana backend logs are cleaner.
- CI stays green.
