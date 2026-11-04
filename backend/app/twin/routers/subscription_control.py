"""Subscription control API for testing and time-based operations.

Provides:
- POST /_chronos/api/universes/{universe_id}/subscriptions/{sub_id}/approve
- POST /_chronos/api/universes/{universe_id}/subscriptions/{sub_id}/suspend
- POST /_chronos/api/universes/{universe_id}/subscriptions/{sub_id}/activate
- POST /_chronos/api/universes/{universe_id}/billing/process
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Universe
from app.twin.dependencies import get_universe_from_control_key
from app.twin.services.subscriptions import (
    SubscriptionService,
    SubscriptionError,
)

router = APIRouter(prefix="/_chronos/api", tags=["subscription-control"])


@router.post(
    "/universes/{universe_id}/subscriptions/{subscription_id}/approve",
    status_code=200,
)
async def approve_subscription(
    universe_id: str,
    subscription_id: str,
    universe: Universe = Depends(get_universe_from_control_key),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Approve a pending subscription.

    Transitions from APPROVAL_PENDING to ACTIVE and schedules first charge.

    Response:
    {
      "id": "I-...",
      "status": "ACTIVE",
      "start_time": "2026-11-04T10:00:00Z",
      ...
    }
    """
    try:
        subscription = await SubscriptionService.approve_subscription(
            db_session,
            universe.id,
            subscription_id,
        )

        await db_session.commit()

        return {
            "id": subscription.id,
            "plan_id": subscription.plan_id,
            "status": subscription.status,
            "subscriber": {
                "name": subscription.subscriber_name,
                "email_address": subscription.subscriber_email,
            },
            "start_time": (
                subscription.start_time.isoformat()
                if subscription.start_time
                else None
            ),
            "next_billing_time": (
                subscription.next_billing_time.isoformat()
                if subscription.next_billing_time
                else None
            ),
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


@router.post(
    "/universes/{universe_id}/subscriptions/{subscription_id}/suspend",
    status_code=200,
)
async def suspend_subscription(
    universe_id: str,
    subscription_id: str,
    body: dict[str, Any] | None = None,
    universe: Universe = Depends(get_universe_from_control_key),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Suspend a subscription.

    Stops billing but allows reactivation.

    Request body (optional):
    {
      "reason": "string"
    }

    Response:
    {
      "id": "I-...",
      "status": "SUSPENDED",
      ...
    }
    """
    try:
        reason = body.get("reason") if body else None

        await SubscriptionService.suspend_subscription(
            db_session,
            universe.id,
            subscription_id,
            reason=reason,
        )

        subscription = await SubscriptionService.get_subscription(
            db_session,
            universe.id,
            subscription_id,
        )

        await db_session.commit()

        return {
            "id": subscription.id,
            "status": subscription.status,
            "status_change_reason": subscription.status_change_reason,
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


@router.post(
    "/universes/{universe_id}/subscriptions/{subscription_id}/activate",
    status_code=200,
)
async def activate_subscription(
    universe_id: str,
    subscription_id: str,
    universe: Universe = Depends(get_universe_from_control_key),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Reactivate a suspended subscription.

    Response:
    {
      "id": "I-...",
      "status": "ACTIVE",
      ...
    }
    """
    try:
        subscription = await SubscriptionService.activate_subscription(
            db_session,
            universe.id,
            subscription_id,
        )

        await db_session.commit()

        return {
            "id": subscription.id,
            "status": subscription.status,
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


@router.post("/universes/{universe_id}/billing/process", status_code=200)
async def process_billing_cycles(
    universe_id: str,
    universe: Universe = Depends(get_universe_from_control_key),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Process all due billing cycles for this universe.

    This is called automatically by the scheduler but can be
    triggered manually for testing.

    Response:
    {
      "processed": 0,
      "message": "..."
    }
    """
    try:
        await SubscriptionService.process_billing_cycles(
            db_session,
            universe.id,
        )

        await db_session.commit()

        return {
            "processed": 1,
            "message": "Billing cycles processed",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": str(e)},
        )
