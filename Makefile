.PHONY: up up-all down logs ps build pull config load load-tiles load-tiles-record

BASE_URL ?= http://127.0.0.1:3000

up:
	docker compose up -d

up-all:
	docker compose --profile observability up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

build:
	docker compose build

pull:
	docker compose pull

config:
	docker compose config

load:
	cd load && npx artillery@^2 run -t $(BASE_URL) map-places.yml

load-tiles:
	cd load && node gen-tiles.mjs > tiles.csv && npx artillery@^2 run -t $(BASE_URL) tiles.yml

load-tiles-record:
	cd load && node gen-tiles.mjs > tiles.csv && npx artillery@^2 run --record --key $$ARTILLERY_CLOUD_API_KEY -t $(BASE_URL) tiles.yml
