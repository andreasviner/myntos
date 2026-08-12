# Convenience targets. On Windows without make, run the underlying commands
# directly — they are listed in README.md.

.PHONY: help db-up db-down backend frontend test lint format

help:
	@echo "Targets: db-up db-down backend frontend test lint format"

db-up:
	docker compose up -d db

db-down:
	docker compose down

backend:
	cd backend && uv run uvicorn myntos.main:app --reload --port 8000

frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests
	cd frontend && pnpm check

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .
