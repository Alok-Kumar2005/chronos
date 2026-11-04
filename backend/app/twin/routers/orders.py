"""PayPal Orders v2 API routes."""

from decimal import Decimal

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import Universe
from app.twin.clock import now
from app.twin.dependencies import get_universe_from_token
from app.twin.services.payments import (
    AuthorizationService,
    CaptureService,
    OrderService,
    PaymentError,
)

router = APIRouter(prefix="/v2/checkout", tags=["orders"])


# Pydantic models for request bodies


class Amount(BaseModel):
    """Amount with currency code."""

    currency_code: str
    value: str


class PurchaseUnit(BaseModel):
    """Purchase unit in an order."""

    amount: Amount


class CreateOrderRequest(BaseModel):
    """Create order request body."""

    intent: str = Field(..., description="CAPTURE or AUTHORIZE")
    purchase_units: list[PurchaseUnit]
    return_url: str | None = None
    cancel_url: str | None = None


class ApproveOrderRequest(BaseModel):
    """Approve order request body."""

    payer_id: str


# Routes


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
    body: CreateOrderRequest = Body(...),
) -> dict:
    """Create a new order.

    POST /v2/checkout/orders
    """
    try:
        intent = body.intent
        purchase_units = body.purchase_units

        if not intent or intent not in ("CAPTURE", "AUTHORIZE"):
            raise PaymentError(
                "INVALID_REQUEST",
                "intent must be CAPTURE or AUTHORIZE",
                status_code=400,
            )

        if not purchase_units:
            raise PaymentError(
                "INVALID_REQUEST",
                "purchase_units must contain at least one item",
                status_code=400,
            )

        pu = purchase_units[0]
        currency_code = pu.amount.currency_code
        value = pu.amount.value

        if not currency_code:
            raise PaymentError(
                "INVALID_REQUEST",
                "currency_code is required",
                status_code=400,
            )

        if not value:
            raise PaymentError(
                "INVALID_REQUEST",
                "value is required",
                status_code=400,
            )

        try:
            value = Decimal(str(value))
        except Exception:
            raise PaymentError(
                "INVALID_REQUEST",
                "value must be a valid decimal number",
                status_code=400,
            )

        virtual_now = await now(db, universe)

        order = await OrderService.create_order(
            db=db,
            universe_id=universe.id,
            intent=intent,
            currency_code=currency_code,
            value=value,
            return_url=body.return_url,
            cancel_url=body.cancel_url,
            virtual_now=virtual_now,
        )

        await db.commit()

        return OrderService._order_to_dict(order)

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )
    except Exception as e:
        raise PayPalError(
            name="INTERNAL_SERVER_ERROR",
            message=str(e),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Get an order by ID.

    GET /v2/checkout/orders/{order_id}
    """
    try:
        order = await OrderService.get_order(db, universe.id, order_id)
        return OrderService._order_to_dict(order)
    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/orders/{order_id}/approve")
async def approve_order(
    order_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
    body: ApproveOrderRequest = Body(...),
) -> dict:
    """Approve an order (internal endpoint for payer approval flow).

    POST /v2/checkout/orders/{order_id}/approve
    """
    try:
        payer_id = body.payer_id
        if not payer_id:
            raise PaymentError(
                "INVALID_REQUEST",
                "payer_id is required",
                status_code=400,
            )

        order = await OrderService.get_order(db, universe.id, order_id)

        virtual_now = await now(db, universe)

        await OrderService.approve_order(db, order, payer_id, virtual_now)

        if order.intent == "AUTHORIZE":
            await AuthorizationService.create_authorization(
                db, order, virtual_now
            )

        await db.commit()
        return OrderService._order_to_dict(order)

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/orders/{order_id}/capture")
async def capture_order(
    order_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Capture an order.

    POST /v2/checkout/orders/{order_id}/capture
    """
    try:
        order = await OrderService.get_order(db, universe.id, order_id)

        virtual_now = await now(db, universe)

        capture = await OrderService.capture_order(db, order, virtual_now)

        await db.commit()

        return {
            "id": order.id,
            "status": order.status,
            "purchase_units": [
                {
                    "payments": {
                        "captures": [CaptureService._capture_to_dict(capture)],
                    },
                }
            ],
        }

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/orders/{order_id}/authorize")
async def authorize_order(
    order_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Authorize an order.

    POST /v2/checkout/orders/{order_id}/authorize
    """
    try:
        order = await OrderService.get_order(db, universe.id, order_id)

        virtual_now = await now(db, universe)

        auth = await OrderService.authorize_order(db, order, virtual_now)

        await db.commit()

        return {
            "id": order.id,
            "status": order.status,
            "purchase_units": [
                {
                    "payments": {
                        "authorizations": [AuthorizationService._authorization_to_dict(auth)],
                    },
                }
            ],
        }

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.patch("/orders/{order_id}")
async def update_order(
    order_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Update an order.

    PATCH /v2/checkout/orders/{order_id}
    """
    try:
        order = await OrderService.get_order(db, universe.id, order_id)
        return OrderService._order_to_dict(order)
    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )
