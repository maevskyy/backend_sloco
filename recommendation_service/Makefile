.PHONY: install dev lint format typecheck test check docker-build

install:
	poetry install

dev:
	poetry run uvicorn recommendation_service.main:app --reload --host 0.0.0.0 --port 8000

lint:
	poetry run ruff check .

format:
	poetry run ruff format .

typecheck:
	poetry run mypy src

test:
	poetry run pytest

check:
	poetry check --lock
	poetry run ruff check .
	poetry run mypy src
	poetry run pytest

docker-build:
	docker build -t recommendation-service:local .

