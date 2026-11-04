"""FastAPI dependencies for universe and token resolution."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import AccessToken, Universe


async def get_universe_from_id(
    universe_id: Annotated[str, Path()],
    session: AsyncSession = Depends(get_session),
) -> Universe:
    """
    Resolve universe_id path parameter to universe using control key.

    Raises 404 if universe not found.

    Usage:
        @app.get("/api/universes/{universe_id}/...")
        async def example(universe: Universe = Depends(get_universe_from_id)):
            ...
    """
    result = await session.execute(
        select(Universe).where(Universe.id == universe_id)
    )
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return universe


async def get_universe_from_token(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> Universe:
    """
    Resolve a Bearer token to its universe.

    Raises 401 AUTHENTICATION_FAILURE if token is invalid or expired.

    Usage:
        @app.get("/example")
        async def example(universe: Universe = Depends(get_universe_from_token)):
            ...
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Missing or invalid Authorization header",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    plaintext_token = authorization[7:]  # Remove "Bearer "

    # Hash and look up token
    token_hash = AccessToken.hash_token(plaintext_token)
    result = await session.execute(
        select(AccessToken).where(AccessToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()

    if not token:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid access token",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Get the universe
    result = await session.execute(
        select(Universe).where(Universe.id == token.universe_id)
    )
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Universe not found",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Check token expiry against VIRTUAL time
    if universe.virtual_now >= token.expires_virtual_at:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Access token has expired",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Check universe expiry
    if datetime.now(UTC) > universe.expires_at:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Universe has expired",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return universe


async def get_control_key(
    x_chronos_key: Annotated[str | None, Header()] = None,
) -> str:
    """Extract and validate control key header."""
    if not x_chronos_key:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Missing X-Chronos-Key header",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return x_chronos_key


async def get_universe_from_control_key(
    universe_id: str,
    control_key: str = Depends(get_control_key),
    session: AsyncSession = Depends(get_session),
) -> Universe:
    """Resolve control key to universe."""
    result = await session.execute(
        select(Universe).where(Universe.id == universe_id)
    )
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not Universe.verify_secret(control_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return universe
