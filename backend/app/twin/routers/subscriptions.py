"""PayPal Subscriptions v1 API routes.

Endpoints for catalog products, billing plans, and subscriptions.
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Universe
from app.twin.dependencies import get_universe_from_token
from app.twin.services.subscriptions import (
    ProductService,
    BillingPlanService,
    SubscriptionService,
    SubscriptionError,
)

router = APIRouter(prefix="", tags=["subscriptions"])


# Catalog Products


@router.post("/v1/catalogs/products", status_code=201)
async def create_product(
    body: dict[str, Any],
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a new catalog product.

    Request body:
    {
      "name": "string (required)",
      "description": "string",
      "type": "SERVICE | PHYSICAL_GOODS | DIGITAL_GOODS (required)",
      "category": "string",
      "image_url": "string",
      "home_url": "string"
    }

    Response:
    {
      "id": "PROD-...",
      "name": "string",
      "type": "string",
      "status": "ACTIVE",
      "links": [...]
    }
    """
    try:
        # Validate required fields
        if "name" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "name is required",
                [{"issue": "name", "description": "Missing required field"}],
            )

        if "type" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "type is required",
                [{"issue": "type", "description": "Missing required field"}],
            )

        product = await ProductService.create_product(
            db_session=db_session,
            universe_id=universe.id,
            name=body["name"],
            product_type=body["type"],
            description=body.get("description"),
            category=body.get("category"),
            image_url=body.get("image_url"),
            home_url=body.get("home_url"),
        )

        await db_session.commit()

        return {
            "id": product.id,
            "name": product.name,
            "type": product.type,
            "description": product.description,
            "category": product.category,
            "status": product.status,
            "links": [
                {
                    "rel": "self",
                    "href": f"/v1/catalogs/products/{product.id}",
                }
            ],
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "name": e.name,
                "message": e.message,
                "details": e.details,
            },
        )


@router.get("/v1/catalogs/products/{product_id}")
async def get_product(
    product_id: str,
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a product by ID."""
    try:
        product = await ProductService.get_product(
            db_session, universe.id, product_id
        )

        return {
            "id": product.id,
            "name": product.name,
            "type": product.type,
            "description": product.description,
            "status": product.status,
            "links": [
                {
                    "rel": "self",
                    "href": f"/v1/catalogs/products/{product.id}",
                }
            ],
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


# Billing Plans


@router.post("/v1/billing/plans", status_code=201)
async def create_plan(
    body: dict[str, Any],
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a new billing plan.

    Request body:
    {
      "product_id": "string (required)",
      "name": "string (required)",
      "description": "string",
      "billing_cycles": [
        {
          "frequency": {
            "interval_unit": "MONTH | YEAR | WEEK | DAY",
            "interval_count": 1
          },
          "pricing_scheme": {
            "fixed_price": {
              "value": "10.00",
              "currency_code": "USD"
            }
          },
          "tenure_type": "REGULAR",
          "sequence": 1,
          "total_cycles": 0
        }
      ],
      "payment_preferences": {
        "service_type": "RECURRING",
        "auto_bill_amount": "MAX_AMOUNT",
        "setup_fee": {"value": "0", "currency_code": "USD"},
        "setup_fee_failure_action": "CONTINUE",
        "payment_failure_threshold": 2
      }
    }

    Response:
    {
      "id": "P-...",
      "name": "string",
      "status": "CREATED",
      "links": [...]
    }
    """
    try:
        # Validate required fields
        if "product_id" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "product_id is required",
            )
        if "name" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "name is required",
            )
        if "billing_cycles" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "billing_cycles is required",
            )
        if "payment_preferences" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "payment_preferences is required",
            )

        plan = await BillingPlanService.create_plan(
            db_session=db_session,
            universe_id=universe.id,
            product_id=body["product_id"],
            name=body["name"],
            description=body.get("description"),
            billing_cycles=body["billing_cycles"],
            payment_preferences=body["payment_preferences"],
        )

        await db_session.commit()

        return {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "status": plan.status,
            "product_id": plan.product_id,
            "billing_cycles": plan.billing_cycles,
            "payment_preferences": plan.payment_preferences,
            "links": [
                {
                    "rel": "self",
                    "href": f"/v1/billing/plans/{plan.id}",
                }
            ],
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "name": e.name,
                "message": e.message,
                "details": e.details,
            },
        )


@router.get("/v1/billing/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a billing plan by ID."""
    try:
        plan = await BillingPlanService.get_plan(
            db_session, universe.id, plan_id
        )

        return {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "status": plan.status,
            "product_id": plan.product_id,
            "billing_cycles": plan.billing_cycles,
            "payment_preferences": plan.payment_preferences,
            "links": [
                {
                    "rel": "self",
                    "href": f"/v1/billing/plans/{plan.id}",
                }
            ],
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


# Subscriptions


@router.post("/v1/billing/subscriptions", status_code=201)
async def create_subscription(
    body: dict[str, Any],
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a new subscription.

    Returns APPROVAL_PENDING status with approval link.

    Request body:
    {
      "plan_id": "string (required)",
      "subscriber": {
        "name": {
          "given_name": "string",
          "surname": "string"
        },
        "email_address": "string"
      },
      "application_context": {
        "return_url": "string",
        "cancel_url": "string"
      }
    }

    Response:
    {
      "id": "I-...",
      "plan_id": "P-...",
      "status": "APPROVAL_PENDING",
      "subscriber": {...},
      "links": [
        {"rel": "approve", "href": "..."},
        ...
      ]
    }
    """
    try:
        # Validate required fields
        if "plan_id" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "plan_id is required",
            )
        if "subscriber" not in body:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "subscriber is required",
            )

        subscriber = body["subscriber"]
        app_context = body.get("application_context", {})

        subscription = await SubscriptionService.create_subscription(
            db_session=db_session,
            universe_id=universe.id,
            plan_id=body["plan_id"],
            subscriber_name=subscriber.get("name", {}),
            subscriber_email=subscriber.get("email_address", ""),
            quantity=body.get("quantity", "1"),
            shipping_amount=(
                Decimal(body["shipping_amount"]["value"])
                if "shipping_amount" in body
                else None
            ),
            return_url=app_context.get("return_url"),
            cancel_url=app_context.get("cancel_url"),
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
            "quantity": subscription.quantity,
            "start_time": subscription.start_time.isoformat() if subscription.start_time else None,
            "links": [
                {
                    "rel": "self",
                    "href": f"/v1/billing/subscriptions/{subscription.id}",
                },
                {
                    "rel": "approve",
                    "href": f"/v1/billing/subscriptions/{subscription.id}/approve",
                    "method": "POST",
                },
            ],
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "name": e.name,
                "message": e.message,
                "details": e.details,
            },
        )


@router.get("/v1/billing/subscriptions/{subscription_id}")
async def get_subscription(
    subscription_id: str,
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a subscription by ID."""
    try:
        subscription = await SubscriptionService.get_subscription(
            db_session, universe.id, subscription_id
        )

        return {
            "id": subscription.id,
            "plan_id": subscription.plan_id,
            "status": subscription.status,
            "subscriber": {
                "name": subscription.subscriber_name,
                "email_address": subscription.subscriber_email,
            },
            "quantity": subscription.quantity,
            "start_time": subscription.start_time.isoformat() if subscription.start_time else None,
            "next_billing_time": (
                subscription.next_billing_time.isoformat()
                if subscription.next_billing_time
                else None
            ),
            "status_update_time": (
                subscription.status_update_virtual_at.isoformat()
                if subscription.status_update_virtual_at
                else None
            ),
            "links": [
                {
                    "rel": "self",
                    "href": f"/v1/billing/subscriptions/{subscription.id}",
                },
            ],
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


@router.post("/v1/billing/subscriptions/{subscription_id}/cancel", status_code=204)
async def cancel_subscription(
    subscription_id: str,
    body: dict[str, Any] | None = None,
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> None:
    """Cancel a subscription."""
    try:
        reason = body.get("reason") if body else None

        await SubscriptionService.cancel_subscription(
            db_session,
            universe.id,
            subscription_id,
            reason=reason,
        )

        await db_session.commit()

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )


@router.get("/v1/billing/subscriptions/{subscription_id}/transactions")
async def get_subscription_transactions(
    subscription_id: str,
    start_index: int = 0,
    page_size: int = 20,
    universe: Universe = Depends(get_universe_from_token),
    db_session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get paginated transactions for a subscription."""
    try:
        transactions, total = await SubscriptionService.get_subscription_transactions(
            db_session,
            universe.id,
            subscription_id,
            start_index=start_index,
            page_size=page_size,
        )

        return {
            "transactions": [
                {
                    "id": t.id,
                    "status": t.status,
                    "amount_value": str(t.amount),
                    "amount_currency_code": t.currency_code,
                    "time": t.billing_virtual_at.isoformat(),
                    "type": t.type,
                }
                for t in transactions
            ],
            "total_items": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    except SubscriptionError as e:
        raise HTTPException(
            status_code=404 if e.name == "RESOURCE_NOT_FOUND" else 400,
            detail={"name": e.name, "message": e.message},
        )
