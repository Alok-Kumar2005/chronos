# Chronos Backend

FastAPI application implementing a PayPal-compatible sandbox with virtual time travel.

## Development

```bash
# Install dependencies
uv sync

# Run development server
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .

# Database migrations
uv run alembic upgrade head
```

## Project Structure

- `app/` - Main application code
  - `main.py` - FastAPI app factory
  - `config.py` - Configuration management
  - `db.py` - Database connectivity
  - `errors.py` - PayPal-shaped error handling
  - `logging_setup.py` - Structured logging

- `app/twin/` - PayPal sandbox implementation
- `app/control/` - Control API (universe management, scenarios)
- `app/shop/` - Demo shop with deliberate bugs
- `app/agent/` - AI agent for bug detection
- `app/scenarios/` - Scenario DSL and library
- `app/reality/` - Reality checks and probes

- `migrations/` - Alembic database migrations
- `tests/` - Test suite
