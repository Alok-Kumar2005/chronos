"""FastAPI application factory and main entry point."""

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import check_database_connection
from app.errors import PayPalError, generic_error_handler, paypal_error_handler
from app.logging_setup import setup_logging

# Import handlers to register them
from app.twin import handlers  # noqa: F401

# Set up logging first
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup/shutdown."""
    # Startup
    logger.info("Starting Chronos application", extra={"env": settings.ENV})

    # Check database connectivity with retry logic (Neon cold start)
    try:
        db_info = await check_database_connection()
        logger.info(
            "Database connection established",
            extra={
                "connected": db_info["connected"],
                "version": db_info.get("version", "unknown"),
            },
        )
    except Exception as e:
        logger.error("Failed to connect to database", exc_info=e)
        raise

    # Initialize LangGraph pool, checkpointer, and store
    from app.langgraph_setup import langgraph_pool

    try:
        await langgraph_pool.initialize()
        app.state.langgraph_pool = langgraph_pool
    except Exception as e:
        logger.error("Failed to initialize LangGraph pool", exc_info=e)
        raise

    logger.info("Application startup complete")

    yield

    # Shutdown
    logger.info("Shutting down Chronos application")

    # Close LangGraph pool
    try:
        await langgraph_pool.close()
    except Exception as e:
        logger.error("Failed to close LangGraph pool", exc_info=e)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Chronos",
        description="PayPal-compatible sandbox twin with virtual time travel",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register routers
    from app.twin.routers.chat import router as chat_router
    from app.twin.routers.control import router as control_router
    from app.twin.routers.oauth import router as oauth_router
    from app.twin.routers.orders import router as orders_router
    from app.twin.routers.payments import router as payments_router
    from app.twin.routers.payout_control import router as payout_control_router
    from app.twin.routers.payouts import router as payouts_router
    from app.twin.routers.subscription_control import router as subscription_control_router
    from app.twin.routers.subscriptions import router as subscriptions_router
    from app.twin.routers.webhook_verify import router as webhook_verify_router
    from app.twin.routers.webhooks import router as webhooks_router

    app.include_router(oauth_router)
    app.include_router(control_router)
    app.include_router(chat_router)
    app.include_router(webhooks_router)
    app.include_router(webhook_verify_router)
    app.include_router(orders_router)
    app.include_router(payments_router)
    app.include_router(payouts_router)
    app.include_router(payout_control_router)
    app.include_router(subscriptions_router)
    app.include_router(subscription_control_router)

    # Request ID middleware
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        """Add a unique request ID to each request for tracing."""
        request_id = request.headers.get("X-Request-ID", secrets.token_hex(8))
        request.state.request_id = request_id

        # Add to logging context
        logger_adapter = logging.LoggerAdapter(
            logger,
            {"request_id": request_id},
        )
        request.state.logger = logger_adapter

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Error handlers
    app.add_exception_handler(PayPalError, paypal_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)

    # Health endpoints
    @app.get(
        "/healthz",
        status_code=status.HTTP_200_OK,
        tags=["Health"],
        summary="Process health check",
    )
    async def healthz() -> dict[str, str]:
        """
        Basic health check - returns 200 if the process is running.

        This endpoint does not check database connectivity.
        """
        return {"status": "ok"}

    @app.get(
        "/readyz",
        status_code=status.HTTP_200_OK,
        tags=["Health"],
        summary="Readiness check with database connectivity",
    )
    async def readyz() -> dict[str, Any]:
        """
        Readiness check - returns 200 if the database is reachable.

        This endpoint verifies that the application can serve traffic.
        """
        try:
            db_info = await check_database_connection()
            return {
                "status": "ready",
                "database": "connected",
                "version": db_info.get("version"),
            }
        except Exception as e:
            logger.error("Readiness check failed", exc_info=e)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "unavailable",
                    "database": "disconnected",
                    "error": str(e),
                },
            )

    return app


# Application instance
app = create_app()
