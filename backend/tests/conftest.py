"""Pytest configuration and fixtures."""

import asyncio
import logging
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import create_app
from app.models import Universe

logger = logging.getLogger(__name__)

# Set Windows event loop policy before any test runs
if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Create event loop for async tests.

    On Windows, use SelectorEventLoop for psycopg3 compatibility.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_db_engine():
    """Create test database engine."""
    # Verify test database is different from production
    if settings.TEST_DATABASE_URL == settings.DATABASE_URL:
        raise RuntimeError(
            "TEST_DATABASE_URL must be different from DATABASE_URL! "
            "Refusing to run tests against production database."
        )

    # Convert to SQLAlchemy URL with psycopg driver
    test_url = settings.TEST_DATABASE_URL
    if test_url.startswith("postgres://"):
        test_url = "postgresql://" + test_url[len("postgres://"):]
    if test_url.startswith("postgresql://"):
        test_url = "postgresql+psycopg://" + test_url[len("postgresql://"):]

    engine = create_async_engine(
        test_url,
        echo=False,
        pool_size=5,
        max_overflow=0,
    )

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session and create all tables."""
    # Create all tables using Alembic migrations
    from alembic import command
    from alembic.config import Config
    import os

    # Get the path to alembic config
    alembic_cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic.ini"
    )

    # Update the alembic config to use test database
    alembic_cfg = Config(alembic_cfg_path)
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        settings.TEST_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://"),
    )

    # Run migrations in a thread pool to avoid blocking the event loop
    import concurrent.futures
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            concurrent.futures.ThreadPoolExecutor(),
            lambda: command.upgrade(alembic_cfg, "head")
        )
        logger.info("Alembic migrations completed")
    except Exception as e:
        logger.warning(f"Alembic migrations failed: {e}, attempting schema creation from models")
        # If migrations fail, create tables from models
        from app.models import Base

        async with test_db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    async_session_local = async_sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    # Truncate all tables before test
    async with test_db_engine.begin() as conn:
        # Get list of all tables
        result = await conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                AND tablename NOT LIKE 'pg_%'
                AND tablename NOT LIKE 'information_%'
                AND tablename NOT LIKE 'alembic_%'
            """
            )
        )
        tables = [row[0] for row in result.fetchall()]

        for table in tables:
            await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

    async with async_session_local() as session:
        yield session
        await session.close()


@pytest_asyncio.fixture
async def async_client(test_db_engine) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing, using the test database."""

    # Override the app's engine to use test database

    app = create_app()

    # Patch the get_session dependency to use test database
    from app.db import get_session

    async def get_test_session():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        session_factory = async_sessionmaker(
            test_db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session

    async with AsyncClient(
        base_url="http://test",
        transport=ASGITransport(app=app),
    ) as client:
        yield client


@pytest_asyncio.fixture
async def db_session(test_db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_local = async_sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    # Truncate all tables before test
    async with test_db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                AND tablename NOT LIKE 'pg_%'
                AND tablename NOT LIKE 'information_%'
                AND tablename NOT LIKE 'alembic_%'
            """
            )
        )
        tables = [row[0] for row in result.fetchall()]

        for table in tables:
            await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

    async with async_session_local() as session:
        yield session
        await session.close()


@pytest_asyncio.fixture
async def universe(db_session: AsyncSession) -> Universe:
    """Create a test universe."""
    from datetime import UTC, datetime, timedelta

    universe, client_secret, control_key, base_url = Universe.create(
        name="test-universe",
        universe_ttl_days=30,
    )
    db_session.add(universe)
    await db_session.commit()

    # Update the universe object to have the secret for testing
    universe.test_secret = client_secret
    universe.test_control_key = control_key

    return universe


@pytest_asyncio.fixture
async def client(async_client: AsyncClient, universe: Universe) -> AsyncClient:
    """Create a test HTTP client with a universe pre-created."""
    # Store universe on client for access in tests
    async_client.universe = universe
    return async_client
