"""Tests for health check endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_healthz(async_client: AsyncClient) -> None:
    """Test /healthz endpoint returns 200."""
    response = await async_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz(async_client: AsyncClient) -> None:
    """Test /readyz endpoint returns 200 or 503 depending on DB connectivity."""
    response = await async_client.get("/readyz")
    # Response should be either 200 (connected) or 503 (unavailable)
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "database" in data
