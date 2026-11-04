"""Control API for payout recipient management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import Universe
from app.twin.clock import now
from app.twin.services.payouts import PayoutService

router = APIRouter(prefix="/_chronos/api", tags=["payout_control"])


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
    from sqlalchemy import select

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


@router.post("/universes/{universe_id}/recipients/{email}/register", status_code=status.HTTP_200_OK)
async def register_recipient(
    universe_id: str,
    email: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_control_key),
) -> dict:
    """Register a payout recipient.

    Args:
        universe_id: Universe ID
        email: Recipient email
        session: Database session
        universe: Universe (from control key validation)

    Returns:
        Recipient dict
    """
    virtual_now = await now(session, universe)
    result = await PayoutService.register_recipient(session, universe.id, email, virtual_now)
    await session.commit()
    return result


@router.post("/universes/{universe_id}/recipients/{email}/claim", status_code=status.HTTP_200_OK)
async def claim_payout_items(
    universe_id: str,
    email: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_control_key),
) -> dict:
    """Claim UNCLAIMED payout items for a recipient.

    Args:
        universe_id: Universe ID
        email: Recipient email
        session: Database session
        universe: Universe (from control key validation)

    Returns:
        Dict with count of claimed items
    """
    virtual_now = await now(session, universe)
    claimed_count = await PayoutService.claim_payout_item(session, universe.id, email, virtual_now)
    await session.commit()
    return {"claimed_items": claimed_count, "email": email}
