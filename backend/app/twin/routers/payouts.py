"""PayPal Payouts v1 API routes."""

from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import ScheduledEvent, Universe
from app.twin.clock import now
from app.twin.dependencies import get_universe_from_token
from app.twin.services.payouts import PayoutError, PayoutService

router = APIRouter(prefix="/v1/payments", tags=["payouts"])


@router.post("/payouts")
async def create_payout(
    request_body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
    idempotency_key: str | None = Header(None, alias="PayPal-Request-Id"),
    paypal_auth_algo: str | None = Header(None, alias="PayPal-Auth-Algo"),
) -> dict[str, Any]:
    """Create a payout batch.

    Args:
        request_body: Request with sender_batch_header (email_subject, email_message) and items list
        session: Database session
        universe: Current universe
        idempotency_key: PayPal-Request-Id for idempotency
        paypal_auth_algo: Auth algorithm (ignored, for PayPal compatibility)

    Returns:
        Payout batch response

    Raises:
        HTTPException: On validation or business logic errors
    """
    try:
        # Extract sender_batch_header and items
        sender_batch_header = request_body.get("sender_batch_header", {})
        sender_batch_id = sender_batch_header.get("sender_batch_id", "")
        items = request_body.get("items", [])

        if not sender_batch_id:
            raise PayoutError(
                name="INVALID_REQUEST",
                message="sender_batch_id is required in sender_batch_header",
                http_status=400,
            )

        virtual_now = await now(session, universe)
        result = await PayoutService.create_payout_batch(
            session,
            universe.id,
            sender_batch_id,
            sender_batch_header,
            items,
            virtual_now,
        )

        await session.commit()

        # Schedule the batch to process in a few virtual seconds
        scheduled_event = ScheduledEvent(
            universe_id=universe.id,
            due_at=virtual_now,  # Process immediately
            kind="payout.process",
            payload={"batch_id": result["batch_header"]["payout_batch_id"]},
        )
        session.add(scheduled_event)
        await session.commit()

        return result
    except PayoutError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.http_status,
            details=e.details,
        )


@router.get("/payouts/{batch_id}")
async def get_payout(
    batch_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict[str, Any]:
    """Get a payout batch with pagination.

    Args:
        batch_id: Batch ID
        page: Page number (1-indexed)
        page_size: Items per page
        session: Database session
        universe: Current universe

    Returns:
        Payout batch dict with paginated items

    Raises:
        HTTPException: If batch not found
    """
    try:
        result = await PayoutService.get_payout_batch(
            session, universe.id, batch_id, page, page_size
        )
        return result
    except PayoutError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.http_status,
            details=e.details,
        )


@router.get("/payouts-item/{item_id}")
async def get_payout_item(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict[str, Any]:
    """Get a payout item.

    Args:
        item_id: Item ID
        session: Database session
        universe: Current universe

    Returns:
        Payout item dict

    Raises:
        HTTPException: If item not found
    """
    try:
        result = await PayoutService.get_payout_item(session, universe.id, item_id)
        return result
    except PayoutError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.http_status,
            details=e.details,
        )


@router.post("/payouts-item/{item_id}/cancel")
async def cancel_payout_item(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict[str, Any]:
    """Cancel a payout item (only UNCLAIMED items).

    Args:
        item_id: Item ID
        session: Database session
        universe: Current universe

    Returns:
        Updated payout item dict

    Raises:
        HTTPException: If item not found or not UNCLAIMED
    """
    try:
        virtual_now = await now(session, universe)
        result = await PayoutService.cancel_payout_item(
            session, universe.id, item_id, virtual_now
        )
        await session.commit()
        return result
    except PayoutError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.http_status,
            details=e.details,
        )
