# TBD: CI/CD, Secrets, Private Repos, And Self-Hosted Runners

## Why This Exists

Current CI/CD is good enough for the first MVP services, but it is already
starting to feel fragile as the backend moves toward multiple repositories and
multiple services.

Current pain:

- secrets live across several GitHub repositories;
- it is easy to forget which repo owns which deploy key/token/env var;
- repositories are currently public partly to keep GitHub Actions usage simple;
- closing repos may require a paid GitHub plan for private Actions minutes;
- GitHub Actions minutes may become expensive or annoying as services grow;
- Python pipelines are already much slower than Node pipelines;
- future architecture may have 5+ repositories/services;
- one powerful Hetzner server already exists and could run CI/CD workloads.

This is not an implementation task yet. It is a thinking document for a future
CI/CD and secret-management redesign.

## Current Situation

Current shape:

- GitHub Actions build service Docker images;
- images are pushed to GHCR;
- production deploy SSHes into Hetzner;
- server pulls images and restarts Docker Compose;
- secrets are stored in GitHub repository secrets.

Expected near future:

- Node gateway service;
- Python recommendation service;
- iOS frontend repository;
- maybe admin/import tooling;
- maybe observability/deployment repository;
- more deploy keys, tokens, env vars, and service-specific secrets.

## Main Questions

### 1. Where Should Secrets Live?

Options to evaluate:

- GitHub repository secrets per repo;
- GitHub organization-level secrets;
- 1Password / Doppler / Infisical / Vaultwarden;
- HashiCorp Vault;
- TeamCity secret parameters;
- secrets stored only on the server and referenced by deploy jobs.

Things we care about:

- one clear place to rotate secrets;
- per-service access control;
- no real secrets committed to repos;
- easy local development workflow;
- easy deploy workflow;
- not overengineering MVP into enterprise theater.

### 2. Should Repositories Stay Public?

Current public repos are convenient, but this will get weird as product and
infra become more real.

Questions:

- When do we make repos private?
- What GitHub plan would we need?
- How many Actions minutes do we get?
- How much do private repo Actions cost for our expected pipelines?
- Can we avoid GitHub-hosted minutes through self-hosted runners?

### 3. Should CI/CD Move To Hetzner?

We already have a powerful Hetzner server. Possible future path:

- run GitHub self-hosted runners on Hetzner;
- or run TeamCity on Hetzner;
- or run another CI/CD tool on Hetzner;
- keep GitHub only as source control and PR UI.

Potential benefits:

- use hardware we already pay for;
- avoid GitHub-hosted private minute usage;
- centralize secrets;
- keep CI/CD behavior visible and under our control;
- easier to run heavier Python jobs.

Potential risks:

- runner isolation becomes our responsibility;
- secrets can leak if runner/workspace cleanup is bad;
- build jobs can affect production workload if not isolated;
- TeamCity/GitLab/Jenkins/Gitea Actions adds maintenance;
- self-hosted CI/CD becomes another production system to monitor.

## TeamCity Option

TeamCity is worth evaluating because it gives:

- a real CI/CD UI;
- project-level and build-level secrets;
- build agents;
- clear pipeline history;
- easier mental model for multiple services;
- mature Docker build/deploy workflows.

Questions:

- Is the free tier enough for our team/services?
- How painful is setup/backup/upgrade?
- Can it run cleanly on the Hetzner server in Docker?
- How do we isolate agents from production containers?
- How do we store deploy keys and GHCR tokens?
- How do we handle PR checks from GitHub?

## GitHub Self-Hosted Runner Option

Alternative: keep GitHub Actions YAML, but execute jobs on our Hetzner runner.

Pros:

- minimal workflow rewrite;
- keeps GitHub PR/check UI;
- avoids GitHub-hosted minute usage;
- simpler than adopting TeamCity immediately.

Cons:

- secrets still mostly live in GitHub unless changed separately;
- self-hosted runners are dangerous for public repos;
- runner hardening and cleanup are our responsibility;
- multiple repos need runner group/access strategy.

Important: if repos stay public, self-hosted runners must be treated carefully.
Public repository pull requests can be risky if untrusted code can reach a
self-hosted runner.

## Possible Target Architecture

Future idea:

```text
GitHub private repos
  -> CI/CD system on Hetzner
  -> centralized secret storage
  -> Docker builds
  -> GHCR or local registry
  -> deploy to Docker Compose / later Kubernetes
```

Possible variants:

```text
GitHub Actions YAML + Hetzner self-hosted runners
```

or:

```text
TeamCity server + TeamCity agents on Hetzner
```

or:

```text
GitHub Actions for lightweight checks
TeamCity/self-hosted runner for heavy builds/deploys
```

## Things To Decide Later

- Keep GitHub Actions or move to TeamCity?
- Use GitHub-hosted or self-hosted runners?
- Make repos private now or later?
- Centralize secrets now or after 3+ repos?
- Use GHCR or move to a private registry on Hetzner?
- Separate CI machine from production machine, or run isolated containers on the
  same Hetzner server?
- How to back up CI/CD config and secrets?
- How to rotate all deploy keys cleanly?
- How to document ownership of each secret?

## Non-Goals For Now

Do not implement yet:

- TeamCity;
- self-hosted GitHub runners;
- HashiCorp Vault;
- private registry;
- Kubernetes CI/CD;
- full secret rotation;
- repo privacy migration.

This document only captures the concern and future evaluation space.

## Future Evaluation Checklist

Before changing CI/CD, measure:

- average Node CI time;
- average Python CI time;
- expected monthly runs per repo;
- expected GitHub private Actions minutes cost;
- Hetzner CPU/RAM headroom during builds;
- risk of running CI on the same host as production;
- setup time for TeamCity vs GitHub self-hosted runners;
- how many secrets already exist and where they live.

Then decide whether the first real move should be:

1. GitHub organization secrets and cleaner naming;
2. GitHub self-hosted runners on Hetzner;
3. TeamCity on Hetzner;
4. full private repos + centralized secret manager.
