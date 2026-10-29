# Common operations. Run `make help` for the list.
SHELL := /bin/bash
COMPOSE := docker compose
API := cd api && .venv/bin

.PHONY: help up down logs ps migrate seed owner demo demo-derby license-keys backup restore test test-api test-web e2e lint build

help: ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

up: ## Build and start the full stack (Postgres, Redis, API, worker, beat, Caddy)
	$(COMPOSE) up -d --build

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

seed: ## Create default plans and feature flags (idempotent)
	$(COMPOSE) run --rm api python -m app.cli seed

owner: ## Create a platform super admin: make owner EMAIL=ops@example.com NAME="Ops"
	$(COMPOSE) run --rm api python -m app.cli create-platform-admin --email "$(EMAIL)" --name "$(NAME)"

demo: ## Create the development demo tenant (refused in production)
	$(COMPOSE) run --rm api python -m app.cli demo-tenant

demo-derby: ## Add a busy derby card to the demo tenant: make demo-derby [FIGHTS=80]
	$(COMPOSE) run --rm api python -m app.cli demo-derby --fights $(or $(FIGHTS),80)

license-keys: ## Print a new Ed25519 key pair for license signing
	$(COMPOSE) run --rm --no-deps api python -m app.cli generate-license-keys

backup: ## Back up the database and media to db/backups/
	db/scripts/backup.sh

restore: ## Restore: make restore DUMP=db/backups/<file>.dump [MEDIA=db/backups/<file>.tar.gz]
	db/scripts/restore.sh "$(DUMP)" $(MEDIA)

test: test-api test-web ## Run backend and frontend unit/integration tests

test-api: ## Backend tests (needs `docker compose up -d postgres redis`)
	$(API)/pytest -q

test-web: ## Angular unit tests (Vitest)
	cd web && npm run test:unit

e2e: ## Playwright end-to-end tests (API on :8000 and dev servers)
	cd web && npm run e2e

lint: ## Static checks
	$(API)/ruff check app tests
	cd web && npx ng build owner --configuration development >/dev/null && npx ng build admin --configuration development >/dev/null && npx ng build player --configuration development >/dev/null

build: ## Production builds of the three portals
	cd web && npm run build:all
