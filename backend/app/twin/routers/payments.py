"""PayPal Payments v2 API routes."""

from decimal import Decimal

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import Universe
from app.twin.clock import now
from app.twin.dependencies import get_universe_from_token
from app.twin.services.payments import (
    AuthorizationService,
    CaptureService,
    PaymentError,
    RefundService,
)

router = APIRouter(prefix="/v2/payments", tags=["payments"])


# Pydantic models for request bodies


class Amount(BaseModel):
    """Amount with currency code."""

    currency_code: str | None = None
    value: str | None = None


class CaptureAuthorizationRequest(BaseModel):
    """Capture authorization request body."""

    amount: Amount | None = None
    final_capture: bool = False
    invoice_id: str | None = None
    note_to_payer: str | None = None


class RefundCaptureRequest(BaseModel):
    """Refund capture request body."""

    amount: Amount | None = None
    note_to_payer: str | None = None


# Authorization endpoints


@router.get("/authorizations/{auth_id}")
async def get_authorization(
    auth_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Get an authorization by ID.

    GET /v2/payments/authorizations/{auth_id}
    """
    try:
        auth = await AuthorizationService.get_authorization(db, universe.id, auth_id)
        return AuthorizationService._authorization_to_dict(auth)
    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/authorizations/{auth_id}/capture")
async def capture_authorization(
    auth_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
    body: CaptureAuthorizationRequest = Body(default=CaptureAuthorizationRequest()),
) -> dict:
    """Capture an authorization (full or partial).

    POST /v2/payments/authorizations/{auth_id}/capture
    """
    try:
        auth = await AuthorizationService.get_authorization(db, universe.id, auth_id)

        amount = None
        if body.amount and body.amount.value:
            try:
                amount = Decimal(str(body.amount.value))
            except Exception:
                raise PaymentError(
                    "INVALID_REQUEST",
                    "amount.value must be a valid decimal number",
                    status_code=400,
                )

        virtual_now = await now(db, universe.id)

        capture = await AuthorizationService.capture_authorization(
            db=db,
            auth=auth,
            amount=amount,
            final_capture=body.final_capture,
            invoice_id=body.invoice_id,
            note_to_payer=body.note_to_payer,
            virtual_now=virtual_now,
        )

        await db.commit()

        return CaptureService._capture_to_dict(capture)

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/authorizations/{auth_id}/reauthorize")
async def reauthorize_authorization(
    auth_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Reauthorize an authorization (extend its expiry).

    POST /v2/payments/authorizations/{auth_id}/reauthorize
    """
    try:
        from datetime import timedelta

        from sqlalchemy import select

        from app.models import ScheduledEvent

        auth = await AuthorizationService.get_authorization(db, universe.id, auth_id)

        virtual_now = await now(db, universe.id)

        if auth.honor_period_ends_virtual_at and virtual_now > auth.honor_period_ends_virtual_at:
            raise PaymentError(
                "REAUTHORIZATION_NOT_ALLOWED",
                "Reauthorization is only allowed during the honor period (3 days after authorization)",
                status_code=422,
            )

        new_expires_at = virtual_now + timedelta(days=29)
        auth.expires_virtual_at = new_expires_at

        # Cancel old expiry event
        old_event = await db.execute(
            select(ScheduledEvent).where(
                ScheduledEvent.kind == "auth.expire",
                ScheduledEvent.payload["authorization_id"].astext == auth.id,
            )
        )
        old_event_obj = old_event.scalars().first()
        if old_event_obj:
            old_event_obj.status = "cancelled"

        # Create new expiry event
        expire_event = ScheduledEvent(
            universe_id=auth.universe_id,
            due_at=new_expires_at,
            kind="auth.expire",
            payload={"authorization_id": auth.id},
            status="pending",
        )
        db.add(expire_event)

        await db.commit()

        return AuthorizationService._authorization_to_dict(auth)

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/authorizations/{auth_id}/void")
async def void_authorization(
    auth_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Void (cancel) an authorization.

    POST /v2/payments/authorizations/{auth_id}/void
    """
    try:
        auth = await AuthorizationService.get_authorization(db, universe.id, auth_id)

        virtual_now = await now(db, universe.id)

        await AuthorizationService.void_authorization(db, auth, virtual_now)

        await db.commit()

        return AuthorizationService._authorization_to_dict(auth)

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


# Capture endpoints


@router.get("/captures/{capture_id}")
async def get_capture(
    capture_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Get a capture by ID.

    GET /v2/payments/captures/{capture_id}
    """
    try:
        capture = await CaptureService.get_capture(db, universe.id, capture_id)
        return CaptureService._capture_to_dict(capture)
    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


@router.post("/captures/{capture_id}/refund")
async def refund_capture(
    capture_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
    body: RefundCaptureRequest = Body(default=RefundCaptureRequest()),
) -> dict:
    """Refund a capture (full or partial).

    POST /v2/payments/captures/{capture_id}/refund
    """
    try:
        capture = await CaptureService.get_capture(db, universe.id, capture_id)

        amount = None
        if body.amount and body.amount.value:
            try:
                amount = Decimal(str(body.amount.value))
            except Exception:
                raise PaymentError(
                    "INVALID_REQUEST",
                    "amount.value must be a valid decimal number",
                    status_code=400,
                )

        virtual_now = await now(db, universe.id)

        refund = await CaptureService.refund_capture(
            db=db,
            capture=capture,
            amount=amount,
            note_to_payer=body.note_to_payer,
            virtual_now=virtual_now,
        )

        await db.commit()

        return RefundService._refund_to_dict(refund)

    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )


# Refund endpoints


@router.get("/refunds/{refund_id}")
async def get_refund(
    refund_id: str,
    db: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_token),
) -> dict:
    """Get a refund by ID.

    GET /v2/payments/refunds/{refund_id}
    """
    try:
        refund = await RefundService.get_refund(db, universe.id, refund_id)
        return RefundService._refund_to_dict(refund)
    except PaymentError as e:
        raise PayPalError(
            name=e.name,
            message=e.message,
            status_code=e.status_code,
            details=e.details,
        )
