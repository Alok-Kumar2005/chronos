"""Database connectivity with async SQLAlchemy 2.0 and psycopg3."""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings


def create_engine(url: str | None = None) -> AsyncEngine:
    """
    Create an async SQLAlchemy engine with psycopg3.

    Args:
        url: Database URL (uses settings.get_sqlalchemy_url() if None)

    Returns:
        Configured async engine
    """
    db_url = url or settings.get_sqlalchemy_url()

    return create_async_engine(
        db_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,  # Recycle connections after 1 hour
    )


# Global engine instance
engine = create_engine()

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def check_database_connection() -> dict[str, Any]:
    """
    Check database connectivity with retry logic.

    Neon databases scale to zero, so the first connection may take longer.
    This function retries up to 3 times with exponential backoff.

    Returns:
        Dict with connection status

    Raises:
        Exception if connection fails after retries
    """
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT 1 as connected, version() as version"))
        row = result.first()
        return {
            "connected": bool(row[0]) if row else False,
            "version": row[1] if row else None,
        }


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI routes to get a database session.

    Usage:
        @app.get("/example")
        async def example(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
