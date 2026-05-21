.PHONY: install dev demo test lint typecheck web-install

install:
	uv sync

web-install:
	cd apps/web && (pnpm install || npm install)

test:
	uv run pytest

lint:
	uv run ruff check src tests

typecheck:
	uv run mypy src

dev:
	@echo "Starting backend (8000) and frontend (3000)..."
	@echo "Hit Ctrl-C to stop both."
	( uv run research serve & echo $$! > /tmp/auto-research-backend.pid ) ; \
	( cd apps/web && (pnpm dev || npm run dev) & echo $$! > /tmp/auto-research-frontend.pid ) ; \
	wait

demo:
	@echo "Starting backend in DEMO MODE (no API keys needed)..."
	@echo "Then in another terminal: cd apps/web && pnpm dev"
	uv run research demo
