# Deployment

Production runs on AWS Lightsail with Docker Compose and Nginx.

## Server Directory

```text
/opt/backend_sloco
```

Expected files:

```text
/opt/backend_sloco/docker-compose.yml
/opt/backend_sloco/.env
```

Use `deploy/docker-compose.production.yml` as the production compose template.

## Environment

Example `/opt/backend_sloco/.env`:

```bash
NODE_ENV=production
HOST=0.0.0.0
PORT=3000
BACKEND_IMAGE=ghcr.io/maevskyy/backend_sloco:prod-latest
```

Do not commit production secrets.

## Nginx

Use `deploy/nginx/backend_sloco.conf` as the initial HTTP-only Nginx template.

It proxies public traffic to:

```text
http://127.0.0.1:3000
```

Add HTTPS with Certbot after a domain is ready.

## Manual Deploy Workflow

GitHub Actions workflow:

```text
.github/workflows/deploy-production.yml
```

It is manual only and accepts a branch, tag, or commit SHA.

Required GitHub repository secrets:

```text
LIGHTSAIL_HOST
LIGHTSAIL_USER
LIGHTSAIL_SSH_KEY
PRODUCTION_API_URL
```

Optional if the server must authenticate to pull from GHCR:

```text
GHCR_USERNAME
GHCR_READ_TOKEN
```

