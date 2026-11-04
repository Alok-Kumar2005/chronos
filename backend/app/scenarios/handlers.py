"""Step handlers for scenario execution - interfaces to shop and twin operations."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventLog, Universe


async def _log_event(
    session: AsyncSession,
    universe: Universe,
    category: str,
    kind: str,
    resource_type: str,
    resource_id: str,
    summary: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Helper to log a scenario event to the event log."""
    event = EventLog(
        universe_id=universe.id,
        virtual_time=universe.virtual_now,
        wall_time=datetime.now(UTC),
        category=category,
        kind=kind,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        data=data or {},
    )
    session.add(event)
    await session.flush()


async def handle_place_order(
    session: AsyncSession,
    universe: Universe,
    product_id: str,
    payer_email: str,
    quantity: int = 1,
) -> dict[str, Any]:
    """Place an order for a product."""
    await _log_event(
        session,
        universe,
        category="order",
        kind="order_created",
        resource_type="order",
        resource_id=f"ORD-{product_id}",
        summary=f"Order created: {quantity}x {product_id} by {payer_email}",
        data={"product_id": product_id, "payer_email": payer_email, "quantity": quantity},
    )
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
    await _log_event(
        session,
        universe,
        category="authorization",
        kind="order_authorized",
        resource_type="order",
        resource_id=order_id,
        summary=f"Order {order_id} approved & authorized by buyer",
        data={"order_id": order_id, "status": "approved"},
    )
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
    await _log_event(
        session,
        universe,
        category="order",
        kind="order_captured",
        resource_type="capture",
        resource_id=order_id,
        summary=f"Order {order_id} shipped - funds captured",
        data={"order_id": order_id, "status": "shipped"},
    )
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
    await _log_event(
        session,
        universe,
        category="order",
        kind="order_refunded",
        resource_type="refund",
        resource_id=order_id,
        summary=f"Order {order_id} refunded ${amount}",
        data={"order_id": order_id, "amount": str(amount)},
    )
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
    await _log_event(
        session,
        universe,
        category="payout",
        kind="payout_batch_created",
        resource_type="payout_batch",
        resource_id="BATCH-001",
        summary="Weekly creator payouts batch processed and submitted",
        data={"batch_id": "BATCH-001", "status": "payout_queued"},
    )
    # Also log a ledger entry
    await _log_event(
        session,
        universe,
        category="ledger",
        kind="ledger_debit",
        resource_type="ledger_entry",
        resource_id="LEDGER-001",
        summary="Ledger debit for creator payout distribution",
        data={"amount": "100.00", "currency": "USD"},
    )
    return {
        "status": "payout_queued",
    }


async def handle_claim_payout(
    session: AsyncSession,
    universe: Universe,
    recipient_email: str,
) -> dict[str, Any]:
    """Claim unclaimed payout."""
    await _log_event(
        session,
        universe,
        category="payout",
        kind="payout_claimed",
        resource_type="payout_item",
        resource_id=recipient_email,
        summary=f"Payout item successfully claimed by {recipient_email}",
        data={"recipient_email": recipient_email, "status": "claimed"},
    )
    return {
        "recipient_email": recipient_email,
        "status": "claimed",
    }
