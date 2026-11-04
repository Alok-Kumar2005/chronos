"""Step handlers for scenario execution - interfaces to shop and twin operations."""

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Universe


async def handle_place_order(
    session: AsyncSession,
    universe: Universe,
    product_id: str,
    payer_email: str,
    quantity: int = 1,
) -> dict[str, Any]:
    """Place an order for a product."""
    # TODO: Implement shop integration
    return {
        "product_id": product_id,
        "payer_email": payer_email,
        "quantity": quantity,
        "status": "created",
    }


async def handle_approve_order(
    session: AsyncSession,
    universe: Universe,
    order_id: str,
) -> dict[str, Any]:
    """Approve a pending order."""
    # TODO: Implement shop integration
    return {
        "order_id": order_id,
        "status": "approved",
    }


async def handle_ship_order(
    session: AsyncSession,
    universe: Universe,
    order_id: str,
) -> dict[str, Any]:
    """Ship an order (capture funds)."""
    # TODO: Implement shop integration
    return {
        "order_id": order_id,
        "status": "shipped",
    }


async def handle_refund_order(
    session: AsyncSession,
    universe: Universe,
    order_id: str,
    amount: Decimal,
) -> dict[str, Any]:
    """Refund an order."""
    # TODO: Implement shop integration
    return {
        "order_id": order_id,
        "amount": str(amount),
        "status": "refunded",
    }


async def handle_run_payout(
    session: AsyncSession,
    universe: Universe,
) -> dict[str, Any]:
    """Run the weekly payout process."""
    # TODO: Implement payout integration
    return {
        "status": "payout_queued",
    }


async def handle_claim_payout(
    session: AsyncSession,
    universe: Universe,
    recipient_email: str,
) -> dict[str, Any]:
    """Claim unclaimed payout."""
    # TODO: Implement payout integration
    return {
        "recipient_email": recipient_email,
        "status": "claimed",
    }
