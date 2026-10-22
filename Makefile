# Convenience targets. On Windows without make, run the underlying commands
# directly — they are listed in README.md.

.PHONY: help backend frontend test lint format deploy

help:
	@echo "Targets: backend frontend test lint format deploy"

backend:
	cd backend && uv run uvicorn myntos.main:app --reload --port 8000

frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run pytest
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests
	cd frontend && pnpm check && pnpm build && pnpm check:seo

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .

# Restarts the live backend and deploys the frontend. Asks before it does.
deploy:
	./update.sh
