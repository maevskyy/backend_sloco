# TASKS 2: Manual Production CD

## Goal

Add a manual production deployment pipeline for the backend.

The deploy should be fully controlled by us:

- GitHub Actions shows a manual "Deploy to production" button.
- GitHub Actions builds a Docker image.
- The image is pushed to GitHub Container Registry.
- GitHub Actions connects to AWS Lightsail over SSH.
- The Lightsail server pulls the new image and restarts the backend with Docker Compose.
- Nginx proxies public HTTP/HTTPS traffic to the backend container.

## Deployment Decision

Use AWS Lightsail instead of Render/Railway/Fly.io.

Reasoning:

- We want infra behavior to be explicit and under our control.
- A Node/Fastify backend is simple enough to run on a VPS.
- Moving from MVP to a more production-ready setup later will be easier if the deployment model is already Docker-based.
- The server should run the app, not build it.

Selected setup:

```text
GitHub Actions
  -> build Docker image
  -> push image to GHCR
  -> SSH into AWS Lightsail
  -> docker compose pull
  -> docker compose up -d
  -> healthcheck /health

AWS Lightsail
  -> Nginx
  -> Docker Compose
  -> backend container
```

## Infrastructure Assumptions

Production server:

- AWS Lightsail Linux instance.
- 2 GB RAM / 2 vCPU / 60 GB SSD / 3 TB transfer is enough for MVP.
- Server runs only the backend and basic infra tools.
- Supabase remains external and is not hosted on this instance.
- Python analytics service is not hosted on this instance for CD v1.

Runtime:

- Docker and Docker Compose are installed on the server.
- Nginx is installed on the server.
- Backend container listens on internal port `3000`.
- Nginx forwards public traffic to `127.0.0.1:3000`.
- The app uses the platform-provided `PORT` env var where needed, defaulting to `3000`.

## Container Registry

Use GitHub Container Registry.

Image name:

```text
ghcr.io/maevskyy/backend_sloco
```

Tags:

```text
ghcr.io/maevskyy/backend_sloco:<git-sha>
ghcr.io/maevskyy/backend_sloco:prod-latest
```

Rules:

- GitHub Actions builds the image.
- GitHub Actions pushes the image to GHCR.
- Lightsail pulls the image from GHCR.
- If the package is private, the server needs a GHCR read token.
- If the package is public, server pull can be simpler, but plan should still support auth.

## CD Trigger

Use a separate GitHub Actions workflow:

```text
.github/workflows/deploy-production.yml
```

Trigger:

```yaml
on:
  workflow_dispatch:
    inputs:
      ref:
        description: "Branch, tag, or commit SHA to deploy"
        required: true
        default: "main"
```

This gives us a manual "Run workflow" button in GitHub Actions.

No automatic production deploy on push.

## Required GitHub Secrets

Add these repository secrets in GitHub:

```text
LIGHTSAIL_HOST
LIGHTSAIL_USER
LIGHTSAIL_SSH_KEY
PRODUCTION_API_URL
```

If GHCR package pull requires auth on the server:

```text
GHCR_USERNAME
GHCR_READ_TOKEN
```

Optional later:

```text
PRODUCTION_ENV_FILE
```

For CD v1, app env vars can be managed directly on the server in a `.env` file used by Docker Compose.

## Server Layout

Use this production directory:

```text
/opt/backend_sloco
```

Expected files on the server:

```text
/opt/backend_sloco/docker-compose.yml
/opt/backend_sloco/.env
```

The `.env` file is not committed.

Example server `.env`:

```bash
NODE_ENV=production
HOST=0.0.0.0
PORT=3000
BACKEND_IMAGE=ghcr.io/maevskyy/backend_sloco:prod-latest
```

Future Supabase env vars will also live here:

```bash
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

## Docker Compose Shape

Production compose should run one backend service:

```yaml
services:
  backend:
    image: ${BACKEND_IMAGE}
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "127.0.0.1:3000:3000"
```

Notes:

- Bind to `127.0.0.1` so the container is not directly public.
- Nginx is the public entrypoint.
- `restart: unless-stopped` keeps the backend running after process crash or reboot.

## Dockerfile Requirements

Add a production Dockerfile.

Expected behavior:

- Use a Node 24 base image.
- Install pnpm.
- Install dependencies with lockfile.
- Build TypeScript.
- Run production code with `pnpm start`.
- Do not include local `.env`.
- Do not rely on `tsx` in production.

Expected high-level stages:

```text
deps
build
runtime
```

Runtime image should contain:

- `dist`
- `package.json`
- `pnpm-lock.yaml`
- production `node_modules`

## Nginx Shape

Nginx should proxy the public API to the local backend container.

Initial HTTP-only config is acceptable if no domain is ready yet.

Expected proxy target:

```text
http://127.0.0.1:3000
```

Required paths:

```text
GET /health
```

When domain is ready, add HTTPS with Certbot.

Do not block CD v1 on HTTPS if we only have the Lightsail public IP.

## GitHub Actions CD Workflow

Workflow file:

```text
.github/workflows/deploy-production.yml
```

Job:

```text
deploy-production
```

Steps:

1. Checkout selected ref.
2. Setup pnpm.
3. Setup Node.js 24.
4. Install dependencies with `pnpm install --frozen-lockfile`.
5. Run validation before deploy:

   ```bash
   pnpm typecheck
   pnpm build
   pnpm test
   pnpm lint
   ```

6. Login to GHCR.
7. Build Docker image.
8. Tag image with:

   ```text
   <git-sha>
   prod-latest
   ```

9. Push both tags to GHCR.
10. SSH into Lightsail.
11. Optionally login to GHCR from the server.
12. Update `/opt/backend_sloco/.env` so `BACKEND_IMAGE` points to the deployed SHA tag.
13. Run:

    ```bash
    cd /opt/backend_sloco
    docker compose pull backend
    docker compose up -d backend
    docker image prune -f
    ```

14. Run production healthcheck:

    ```bash
    curl --fail "$PRODUCTION_API_URL/health"
    ```

## Deployment Policy

Production deploys are manual only.

Rules:

- CI continues to run on every push and pull request.
- CD does not run on push.
- CD can deploy `main`, a branch, a tag, or a commit SHA via `workflow_dispatch` input.
- The workflow must fail if build, test, or lint fails.
- The workflow must fail if `/health` does not return success after deploy.

## Server Provisioning Steps

This task should document the server setup, but not fully automate provisioning yet.

Manual setup on Lightsail:

1. Create Lightsail Linux instance.
2. Add SSH key access.
3. Install Docker.
4. Install Docker Compose plugin.
5. Install Nginx.
6. Create app directory:

   ```bash
   sudo mkdir -p /opt/backend_sloco
   sudo chown -R $USER:$USER /opt/backend_sloco
   ```

7. Create `/opt/backend_sloco/.env`.
8. Create `/opt/backend_sloco/docker-compose.yml`.
9. Configure Nginx site.
10. Restart Nginx.
11. Confirm server can pull from GHCR.

Future task can automate provisioning with Ansible or Terraform.

## Files To Add Or Change

Expected repository changes:

```text
Dockerfile
.dockerignore
.github/workflows/deploy-production.yml
docs/TASKS_2_CD.md
docs/DEPLOYMENT.md
```

Optional if we want server templates in repo:

```text
deploy/docker-compose.production.yml
deploy/nginx/backend_sloco.conf
```

Do not commit production secrets.

## Acceptance Criteria

- `TASKS_2_CD.md` exists and documents the CD plan.
- Docker image can be built locally.
- Docker container starts and serves `GET /health`.
- GHCR image is built and pushed by GitHub Actions.
- GitHub Actions has a manual production deployment workflow.
- Deployment workflow accepts a branch/tag/SHA input.
- Deployment workflow validates build, tests, and lint before deploy.
- Deployment workflow SSHes into Lightsail and restarts the backend container.
- Nginx proxies traffic to the backend container.
- Production `/health` check passes after deploy.
- Commit and push are handled manually by the user.

## Future Follow-Ups

- Add HTTPS with Certbot after domain is ready.
- Add zero-downtime deploy strategy.
- Add rollback workflow by image tag.
- Add staging environment.
- Add deployment notifications.
- Add basic server monitoring.
- Add log shipping.
- Add automated provisioning with Ansible or Terraform.

