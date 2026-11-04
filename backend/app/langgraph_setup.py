"""LangGraph checkpoint and store setup with Postgres connection pool."""

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)


class LangGraphPool:
    """Manages LangGraph's Postgres pool, checkpointer, and store."""

    def __init__(self):
        self.pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.store: AsyncPostgresStore | None = None

    async def initialize(self) -> None:
        """Initialize the connection pool, checkpointer, and store at startup."""
        if self.pool is not None:
            logger.warning("LangGraphPool already initialized, skipping re-init")
            return

        # Get the raw Postgres URL (postgresql://) for LangGraph
        db_url = settings.get_raw_database_url()

        # Create the connection pool with Neon-friendly settings
        self.pool = AsyncConnectionPool(
            db_url,
            # Connection kwargs for Neon compatibility
            autocommit=True,  # Let LangGraph handle transactions
            prepare_threshold=None,  # Disable prepared statements initially
            row_factory=dict_row,  # Return rows as dicts
            # Pool sizing: small since LangGraph operations are typically brief
            min_size=2,
            max_size=10,
            # Health check for Neon scale-to-zero recovery
            check=self._health_check,
            check_interval=30,  # Check every 30 seconds
        )

        logger.info(
            "Initializing LangGraph connection pool",
            extra={
                "min_size": 2,
                "max_size": 10,
                "url": db_url[:50] + "...",
            },
        )

        # Open the pool
        await self.pool.open()
        logger.info("LangGraph connection pool opened")

        # Create checkpointer and store
        self.checkpointer = AsyncPostgresSaver(self.pool)
        self.store = AsyncPostgresStore(self.pool)

        # Initialize tables (idempotent)
        logger.info("Setting up LangGraph checkpointer tables")
        await self.checkpointer.setup()
        logger.info("Checkpointer setup complete")

        logger.info("Setting up LangGraph store tables")
        await self.store.setup()
        logger.info("Store setup complete")

    async def close(self) -> None:
        """Close the connection pool at shutdown."""
        if self.pool is not None:
            logger.info("Closing LangGraph connection pool")
            await self.pool.close()
            self.pool = None
            self.checkpointer = None
            self.store = None
            logger.info("LangGraph connection pool closed")

    @staticmethod
    async def _health_check(conn: AsyncConnection) -> None:
        """Health check for connection pool recovery from Neon scale-to-zero."""
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        except Exception as e:
            logger.warning("Health check failed", exc_info=e)
            raise


# Global instance
langgraph_pool = LangGraphPool()
