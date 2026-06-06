# TASKS 15: Minimal Hetzner Deploy Migration

## Goal

Move production deploy from the current Lightsail host to a new Hetzner Ubuntu
host with the smallest reliable path: Docker Compose + Nginx + GitHub Actions
CD. The server stays stateless; Supabase remains the managed database.

No source code clone is required on the server. GitHub Actions builds the Docker
image, pushes it to GHCR, SSHes into the server, pulls the image, and restarts
the container.

## Out Of Scope

- Self-hosted Grafana, Loki, Prometheus.
- Grafana Alloy migration.
- Terraform, Ansible, Kubernetes, k3s, or a provisioning framework.
- Domain/HTTPS. This was handled later in `TASKS_20_DOMAIN_HTTPS_NGINX_HARDENING.md`.
- Multiple servers or load balancing.

## Current CD Shape

The deploy workflow stays manual:

```text
.github/workflows/deploy-production.yml
```

It uses provider-neutral GitHub secrets:

```text
DEPLOY_HOST
DEPLOY_USER
DEPLOY_SSH_KEY
PRODUCTION_API_URL
GHCR_USERNAME
GHCR_READ_TOKEN
```

Cutover is done by changing those secrets from the Lightsail values to the
Hetzner values. Until the secrets point to Hetzner, deploy still targets the old
server.

## Hetzner Server Setup

Connect to the new server:

```bash
ssh <ssh-user>@<hetzner-ip>
```

Install runtime packages:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg nginx
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker <ssh-user>
```

Reconnect SSH after adding the user to the `docker` group, then verify:

```bash
docker --version
docker compose version
docker ps
```

Create the app directory:

```bash
sudo mkdir -p /opt/backend_sloco
sudo chown -R <ssh-user>:<ssh-user> /opt/backend_sloco
```

From local machine, copy the compose template:

```bash
scp deploy/docker-compose.production.yml \
  <ssh-user>@<hetzner-ip>:/opt/backend_sloco/docker-compose.yml
```

Create `/opt/backend_sloco/.env` on the server:

```bash
nano /opt/backend_sloco/.env
```

Minimum values:

```bash
NODE_ENV=production
HOST=0.0.0.0
PORT=3000
BACKEND_IMAGE=ghcr.io/maevskyy/backend_sloco:prod-latest
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Do not commit real secrets.

## Nginx Setup

Create the site config on the server:

```bash
sudo nano /etc/nginx/sites-available/backend_sloco
```

Paste the content of:

```text
deploy/nginx/backend_sloco.conf
```

Enable it:

```bash
sudo ln -sf /etc/nginx/sites-available/backend_sloco /etc/nginx/sites-enabled/backend_sloco
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Before deploy, public health may return `502` because the backend container is
not running yet. That is expected.

## Deploy SSH Key

Create a dedicated key for GitHub Actions:

```bash
ssh-keygen -t ed25519 -C "github-actions-backend-sloco-hetzner" \
  -f ~/.ssh/backend_sloco_hetzner_ci
```

Add the public key to the server:

```bash
cat ~/.ssh/backend_sloco_hetzner_ci.pub
nano ~/.ssh/authorized_keys
```

The public key must be a new line in `authorized_keys`, not a replacement for
your personal SSH key.

Test from local machine:

```bash
ssh -i ~/.ssh/backend_sloco_hetzner_ci <ssh-user>@<hetzner-ip>
```

Put the private key content into GitHub secret `DEPLOY_SSH_KEY`.

## GitHub Secrets Cutover

Set repository secrets:

```text
DEPLOY_HOST=<hetzner-ip>
DEPLOY_USER=<ssh-user>
DEPLOY_SSH_KEY=<private deploy key>
PRODUCTION_API_URL=http://<hetzner-ip>
GHCR_USERNAME=<github username>
GHCR_READ_TOKEN=<token with read:packages>
```

After these values are changed, the next manual `Deploy Production` run targets
Hetzner.

Do not delete the old Lightsail values from your password manager until Hetzner
is verified. Rollback is just setting the secrets back to the old host and
rerunning the workflow.

## First Deploy

Run GitHub Actions:

```text
Actions -> Deploy Production -> Run workflow -> ref: main
```

The workflow should:

- build and test the backend;
- push image to GHCR;
- SSH into Hetzner;
- update `BACKEND_IMAGE` in `/opt/backend_sloco/.env`;
- run `docker compose pull backend`;
- run `docker compose up -d backend`;
- pass local and public `/v1/health` checks.

## Verification

On the server:

```bash
cd /opt/backend_sloco
docker compose ps
docker compose logs --tail=100 backend
curl http://127.0.0.1:3000/v1/health
curl http://127.0.0.1:3000/v1/health/supabase
```

From local machine:

```bash
curl http://<hetzner-ip>/v1/health
curl http://<hetzner-ip>/v1/health/supabase
curl "http://<hetzner-ip>/v1/map/places?swLat=44.40&swLng=26.05&neLat=44.48&neLng=26.13&zoom=13"
```

GitHub Actions run must be green end-to-end.

## After Successful Cutover

- Update hardcoded backend IP references in active docs/configs:
  - `src/config/swagger.ts`;
  - `README.md`;
  - `AGENTS.md`;
  - `docs/README.md`;
  - `docs/FRONTEND_MAP_API.md`;
  - `grafana/README.md`.
- Update the iOS app base URL.
- Keep Lightsail alive until the frontend and backend are verified on Hetzner.
- Then stop old Lightsail and rotate secrets that lived on it.

## Assumptions

- Hetzner server runs Ubuntu.
- We keep one production workflow, not a separate Hetzner workflow.
- The backend remains stateless.
- Supabase stays managed and has no IP allowlist blocking the new server.
- Domain/HTTPS and observability migration are separate tasks.
