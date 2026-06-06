FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src" \
    POETRY_VERSION=2.1.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true

ENV PATH="$POETRY_HOME/bin:/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS deps

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

FROM base AS runtime

ENV APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    LOG_LEVEL=info \
    SERVICE_NAME=recommendation-service

RUN addgroup --system app \
    && adduser --system --ingroup app app

COPY --from=deps /app/.venv ./.venv
COPY artifacts ./artifacts
COPY src ./src
COPY pyproject.toml poetry.lock* README.md ./

USER app

EXPOSE 8000

CMD ["uvicorn", "recommendation_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
