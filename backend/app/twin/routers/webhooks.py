"""PayPal Webhooks Management API routes.

Per PayPal's Webhooks Management API v1:
https://developer.paypal.com/api/webhooks/v1/
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import Universe, Webhook
from app.twin.dependencies import get_universe_from_token
from app.twin.ids import generate_webhook_id

router = APIRouter(prefix="/v1/notifications", tags=["webhooks"])


@router.post("/webhooks", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: dict,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a webhook to subscribe to events.
    
    Request body:
    {
        "url": "https://example.com/webhooks",
        "event_types": [
            {"name": "PAYMENT.CAPTURE.COMPLETED"},
            {"name": "*"}  # Subscribe to all events
        ]
    }
    
    Response:
    {
        "id": "WH-...",
        "url": "https://example.com/webhooks",
        "event_types": [...],
        "created_at": "2026-11-04T10:00:00Z"
    }
    """
    universe: Universe = await get_universe_from_token(authorization, session)

    # Validate input
    if "url" not in body or not isinstance(body["url"], str):
        raise PayPalError(
            name="INVALID_REQUEST",
            message="'url' is required and must be a string",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if "event_types" not in body or not isinstance(body["event_types"], list):
        raise PayPalError(
            name="INVALID_REQUEST",
            message="'event_types' is required and must be an array",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    url = body["url"]

    # Extract event type names from the array of objects
    event_types = []
    for et in body["event_types"]:
        if isinstance(et, dict) and "name" in et:
            event_types.append(et["name"])
        elif isinstance(et, str):
            event_types.append(et)
        else:
            raise PayPalError(
                name="INVALID_REQUEST",
                message="Each event_type must be a string or have a 'name' field",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # Create webhook
    webhook_id = generate_webhook_id()
    webhook = Webhook(
        id=webhook_id,
        universe_id=universe.id,
        url=url,
        event_types=event_types,
    )
    session.add(webhook)
    await session.commit()

    return {
        "id": webhook.id,
        "url": webhook.url,
        "event_types": [{"name": et} for et in webhook.event_types],
        "created_at": webhook.created_at.isoformat() + "Z",
    }


@router.get("/webhooks")
async def list_webhooks(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict:
    """List all webhooks for this universe.
    
    Response:
    {
        "webhooks": [...],
        "total": 42,
        "page": 1,
        "page_size": 10
    }
    """
    universe: Universe = await get_universe_from_token(authorization, session)

    # Get total count
    count_result = await session.execute(
        select(Webhook).where(Webhook.universe_id == universe.id)
    )
    webhooks = count_result.scalars().all()
    total = len(webhooks)

    # Paginate
    offset = (page - 1) * page_size
    paginated = webhooks[offset : offset + page_size]

    return {
        "webhooks": [
            {
                "id": w.id,
                "url": w.url,
                "event_types": [{"name": et} for et in w.event_types],
                "created_at": w.created_at.isoformat() + "Z",
            }
            for w in paginated
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get a specific webhook by ID."""
    universe: Universe = await get_universe_from_token(authorization, session)

    result = await session.execute(
        select(Webhook).where(
            (Webhook.id == webhook_id) & (Webhook.universe_id == universe.id)
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message=f"Webhook '{webhook_id}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return {
        "id": webhook.id,
        "url": webhook.url,
        "event_types": [{"name": et} for et in webhook.event_types],
        "created_at": webhook.created_at.isoformat() + "Z",
    }


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: str,
    body: dict,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Update a webhook's URL and/or event_types."""
    universe: Universe = await get_universe_from_token(authorization, session)

    result = await session.execute(
        select(Webhook).where(
            (Webhook.id == webhook_id) & (Webhook.universe_id == universe.id)
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message=f"Webhook '{webhook_id}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Update URL if provided
    if "url" in body:
        webhook.url = body["url"]

    # Update event_types if provided
    if "event_types" in body:
        event_types = []
        for et in body["event_types"]:
            if isinstance(et, dict) and "name" in et:
                event_types.append(et["name"])
            elif isinstance(et, str):
                event_types.append(et)
        webhook.event_types = event_types

    await session.commit()

    return {
        "id": webhook.id,
        "url": webhook.url,
        "event_types": [{"name": et} for et in webhook.event_types],
        "created_at": webhook.created_at.isoformat() + "Z",
    }


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: str,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a webhook."""
    universe: Universe = await get_universe_from_token(authorization, session)

    result = await session.execute(
        select(Webhook).where(
            (Webhook.id == webhook_id) & (Webhook.universe_id == universe.id)
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message=f"Webhook '{webhook_id}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    await session.delete(webhook)
    await session.commit()
