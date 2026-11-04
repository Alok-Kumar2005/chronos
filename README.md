# Chronos

A PayPal-compatible sandbox twin with virtual time travel and AI-powered bug detection.

## What is Chronos?

Chronos is a PayPal sandbox alternative that adds virtual clocks (like Stripe Test Clocks). Point your PayPal integration at Chronos, then fast-forward time (+3 days, +30 days) to see how time-based behaviors affect your app: authorization expiry, unclaimed payouts being returned, expired OAuth tokens, webhook retries, duplicate and out-of-order webhooks.

## Features

- **Virtual Time Travel**: Per-tenant virtual clocks for testing time-dependent PayPal behaviors
- **PayPal-Compatible API**: Drop-in replacement for PayPal sandbox with identical endpoints and response formats
- **Webhook Chaos Engineering**: Test duplicate webhooks, out-of-order delivery, and retry behavior
- **Demo Shop**: Example e-commerce integration with deliberate time-based bugs
- **AI Agent**: LangGraph-powered agent that finds bugs, explains root causes, and proposes fixes
- **Deterministic Rules**: All PayPal behavior driven by a documented rules catalogue, not AI guesses

## Quick Start

```bash
# Install dependencies
cd backend
uv sync

# Set up database
uv run alembic upgrade head

# Run development server
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest
```

## Architecture

- **Backend**: FastAPI + SQLAlchemy 2.0 (async) + Neon Postgres
- **AI Layer**: LangGraph + Groq for scenario generation and bug detection
- **Frontend**: React SPA (coming in Step 11)

## Documentation

- [AGENTS.md](AGENTS.md) - Development conventions and protocols
- [docs/SPEC.md](docs/SPEC.md) - Complete product specification
- [docs/PROGRESS.md](docs/PROGRESS.md) - Development progress tracker

## License

MIT
