# walk-planner microservice image (FastAPI + uvicorn). Code only.
#
# Build context = services/walk_planner (the package root; filtered by its .dockerignore):
#   cd services/walk_planner
#   docker build -f deploy/Dockerfile -t sloco-walk-planner:1.0.0 --build-arg GIT_SHA="$(git rev-parse --short=12 walk-planner-v1.0.0^{commit})" .   # the release tag's commit
#   (or: docker compose --env-file <walk.env> -f deploy/docker-compose.yml build walk-planner)
# Tag = ALGORITHM_VERSION (walk_planner/version.py), optionally also sha-<git>. Never deploy "latest".
#
# Contents: the hash-pinned runtime closure (deploy/requirements.lock) + the walk_planner wheel + uvicorn's JSON
# log config. No tests, golden data, docs or build tools. Data bundles are never baked in: they are mounted
# read-only and selected with WALK_BUNDLE_DIR. Secrets (ORS_API_KEY) arrive as env vars at run time
# (deploy/env.example). Runs as uid/gid 10001; serves :8000; healthy = GET /v1/health/ready returns 200.
# Uses only classic Dockerfile syntax (no BuildKit-only features), so the legacy builder works too.
#
# Base image: pinned to an exact Python patch release on Debian trixie (the lock files are resolved for
# CPython 3.12 manylinux wheels). When pinned (2026-10-03) the tag resolved to the multi-arch index
#   python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256
#   (linux/amd64 sha256:6b1f85a08c199d29d5b6d71ab9c27bd5b3b393492e01216a15758ff69c4be8b8,
#    linux/arm64 sha256:18d23907c5d7ef2ca1b0ea8b4a73fe4a6ae47114c257a1afed8bc66c3886f8cb).
# Debian security fixes are re-published under the same tag: rebuild regularly; for a bit-identical rebuild
# pass --build-arg PYTHON_IMAGE=<the tag@sha256 index digest above>. Bump the patch release deliberately (and
# run the CI image job) -- never a floating tag such as 3.12-slim.

ARG PYTHON_IMAGE=python:3.12.15-slim-trixie

# ---- stage 1: build the walk_planner wheel (setuptools lives only in this stage) ---------------------
FROM ${PYTHON_IMAGE} AS wheel
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1
COPY deploy/requirements-build.lock /tmp/requirements-build.lock
RUN pip install --require-hashes --only-binary=:all: -r /tmp/requirements-build.lock
WORKDIR /src
COPY . /src
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /src

# ---- stage 2: runtime ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

# Threads: one BLAS/OpenMP thread per process. Parallelism comes from uvicorn workers and request threads,
#   and a multi-threaded BLAS in every worker would oversubscribe the container's CPU limit.
# MALLOC_ARENA_MAX=2: bounds glibc malloc arenas (RSS creep) in multi-threaded workers.
# WEB_CONCURRENCY: uvicorn worker processes (uvicorn reads it itself). Memory per worker: see deploy/env.example
#   (measured ~0.6 GB steady, peaks ~1.3 GB under bursts of 24-hour plans; WALK_MAX_CONCURRENT_PLANS bounds it).
# UVICORN_<OPTION> env vars are read by uvicorn for any CLI option not given on the command line, so these
#   defaults stay overridable at run time:
#   - ACCESS_LOG=false: the service logs one JSON line per request itself;
#   - TIMEOUT_GRACEFUL_SHUTDOWN: let in-flight requests finish on `docker stop` (compose stop_grace_period 30s);
#   - TIMEOUT_KEEP_ALIVE=65: longer than a gateway's idle keep-alive, so the server never closes a pooled
#     socket the client is about to reuse (sporadic ECONNRESET / 502 otherwise).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    WEB_CONCURRENCY=2 \
    UVICORN_ACCESS_LOG=false \
    UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=20 \
    UVICORN_TIMEOUT_KEEP_ALIVE=65

# Dependencies first (cached until the lock changes); hash-checked, wheels only (no compiler in -slim).
COPY deploy/requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

# The package itself (its deps are already satisfied by the lock), then a fail-fast import check.
COPY --from=wheel /wheels/ /tmp/wheels/
RUN pip install --no-deps /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels \
 && python -c "import numpy, pandas, pyarrow, scipy, sklearn, requests, redis, fastapi, uvicorn, pydantic_settings, walk_planner; print('walk_planner', walk_planner.__version__, '| numpy', numpy.__version__, '| pandas', pandas.__version__)"

COPY deploy/docker-entrypoint.sh /usr/local/bin/walk-planner-entrypoint
# uvicorn's own log lines (its supervisor: "Started parent process", worker restarts ...) as JSON, like the
# service's: the formatter is walk_planner.service.logging.JsonFormatter.
COPY deploy/uvicorn-log-config.json /etc/walk-planner/uvicorn-log-config.json
RUN groupadd --system --gid 10001 walk \
 && useradd --system --uid 10001 --gid walk --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin walk

WORKDIR /app
USER 10001:10001
EXPOSE 8000

# 503 until the bundle is loaded and validated (start period covers the load, ~5-10 s per worker).
# The proxy handler is disabled so an inherited HTTP(S)_PROXY can never intercept the loopback probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request as u; r = u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8000/v1/health/ready', timeout=4); sys.exit(0 if r.status == 200 else 1)"]

# The entrypoint validates every bundle of WALK_BUNDLE_DIR first (exit 78 = EX_CONFIG when one is missing or
# corrupt), then execs the command.
ENTRYPOINT ["/bin/sh", "/usr/local/bin/walk-planner-entrypoint"]
CMD ["uvicorn", "walk_planner.service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--log-config", "/etc/walk-planner/uvicorn-log-config.json"]

# Last on purpose: changes on every commit, so everything above stays cached.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="sloco-walk-planner" \
      org.opencontainers.image.description="SLOCO Walk Planner microservice (FastAPI): walking-route plans over a mounted city data bundle" \
      org.opencontainers.image.revision="${GIT_SHA}"
ENV WALK_GIT_SHA=${GIT_SHA}
