# Small, stable interface for humans and CI. `make help` lists targets.
.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON        ?= python3
VENV          ?= .venv
BIN           := $(VENV)/bin
PORT          ?= 8080
DATABASE_URL  ?= postgresql+psycopg://postgres:postgres@localhost:5432/files
TEST_DATABASE_URL ?= postgresql+psycopg://postgres:postgres@localhost:5432/files_test
IMAGE         ?= secure-file-service:local
SMOKE_URL     ?= http://localhost:$(PORT)

export DATABASE_URL

.PHONY: help install dev local run test test-unit test-api test-integration test-contract test-e2e test-performance \
        lint format format-check typecheck migrate migration migration-check openapi openapi-check build \
        smoke seed secret verify clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

install: $(BIN)/python ## Create the virtualenv and install the project with dev extras
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

dev: ## [needs Docker] Start API + PostgreSQL with Docker Compose (migrations run first)
	docker compose up --build

local: ## Docker-free dev loop: wait for local PostgreSQL, migrate, run with reload on PORT
	$(BIN)/python scripts/wait_for_db.py --timeout 30
	$(BIN)/alembic upgrade head
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

run: ## Run the API locally with auto-reload against DATABASE_URL (development only)
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

test: ## Unit, API and contract tests (no external services)
	$(BIN)/pytest tests/unit tests/api tests/contract --cov --cov-report=term-missing

test-unit: ## Unit tests only
	$(BIN)/pytest tests/unit

test-api: ## HTTP-level tests against in-memory SQLite
	$(BIN)/pytest tests/api

test-integration: ## PostgreSQL-backed tests (needs TEST_DATABASE_URL)
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) $(BIN)/pytest tests/integration -m integration

test-contract: ## OpenAPI contract tests
	$(BIN)/pytest tests/contract

test-e2e: ## End-to-end tests against E2E_BASE_URL (e.g. make test-e2e E2E_BASE_URL=https://...)
	$(BIN)/pytest tests/e2e -m e2e

test-performance: ## Opt-in latency budget tests
	RUN_PERFORMANCE=1 $(BIN)/pytest tests/performance -m performance -s

lint: ## Ruff lint
	$(BIN)/ruff check app tests scripts migrations

format: ## Ruff format (writes files)
	$(BIN)/ruff format app tests scripts migrations

format-check: ## Ruff format check (no writes)
	$(BIN)/ruff format --check app tests scripts migrations

typecheck: ## mypy strict
	$(BIN)/mypy app scripts

migrate: ## Apply migrations to DATABASE_URL
	$(BIN)/alembic upgrade head

migration: ## Autogenerate a migration: make migration MSG="add column"
	$(BIN)/alembic revision --autogenerate -m "$(MSG)"

migration-check: ## Fail if models and migrations have drifted (needs DATABASE_URL at head)
	$(BIN)/alembic check

openapi: ## Regenerate openapi/openapi.yaml from the code
	$(BIN)/python scripts/export_openapi.py

openapi-check: ## Fail if openapi/openapi.yaml is stale
	$(BIN)/python scripts/export_openapi.py --check

build: ## [needs Docker] Build the Ubuntu 24.04 based production image
	docker build -t $(IMAGE) .

smoke: ## Run the smoke test: make smoke SMOKE_URL=https://your-app
	$(BIN)/python scripts/smoke_test.py $(SMOKE_URL) --wait 30

seed: ## Upload sample files to SMOKE_URL for demos
	$(BIN)/python scripts/seed.py $(SMOKE_URL)

secret: ## Print a fresh signing secret for SIGNING_KEYS
	@$(BIN)/python -c "from app.core.security import new_secret; print(new_secret())"

verify: format-check lint typecheck openapi-check test test-integration ## Everything CI runs, locally
	@echo "verify: all checks passed"

clean: ## Remove caches, coverage and local storage
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov var
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
