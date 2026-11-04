.PHONY: help dev test lint migrate clean

help:
	@echo "Chronos development targets:"
	@echo "  make dev      - Start development server"
	@echo "  make test     - Run test suite"
	@echo "  make lint     - Lint and format check"
	@echo "  make migrate  - Run database migrations"
	@echo "  make clean    - Clean build artifacts"

dev:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

test:
	cd backend && uv run pytest -v

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

migrate:
	cd backend && uv run alembic upgrade head

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/dist backend/build
