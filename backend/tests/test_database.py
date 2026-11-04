"""Tests for database connectivity."""

import pytest

from app.db import AsyncSessionLocal


@pytest.mark.asyncio
async def test_database_session_factory() -> None:
    """Test that database session factory is configured."""
    # Verify the session factory exists and is configured
    assert AsyncSessionLocal is not None
    assert callable(AsyncSessionLocal)
