"""PayPal Orders v2 and Payments v2 business logic service."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Order,
    Authorization,
    Capture,
    Refund,
    AccountBalance,
)
from app.twin.ids import (
    generate_order_id,
    generate_authorization_id,
    generate_capture_id,
    generate_refund_id,
)
from app.twin.webhooks import emit_event
import uuid


class PaymentError(Exception):
    """Base class for payment-related errors."""

    def __init__(self, name: str, message: str, status_code: int = 422, details: list[dict] | None = None):
        self.name = name
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)


class OrderService:
    """Service for managing Orders."""

    @staticmethod
    async def create_order(
        db: AsyncSession,
        universe_id: uuid.UUID,
        intent: str,
        currency_code: str,
        value: Decimal,
        return_url: str | None = None,
        cancel_url: str | None = None,
        virtual_now: datetime = None,
    ) -> Order:
        """Create a new order.

        Args:
            db: Database session
            universe_id: Universe ID
            intent: "CAPTURE" or "AUTHORIZE"
            currency_code: ISO 4217 currency code
            value: Order amount
            return_url: Payer approval redirect URL
            cancel_url: Payer cancellation redirect URL
            virtual_now: Current virtual time

        Returns:
            Created Order object

        Raises:
            PaymentError: On validation failure
        """
        # Validate intent
        if intent not in ("CAPTURE", "AUTHORIZE"):
            raise PaymentError(
                "INVALID_REQUEST",
                f"intent must be CAPTURE or AUTHORIZE, got {intent}",
                status_code=400,
            )

        # Validate currency code
        if not currency_code or len(currency_code) != 3:
            raise PaymentError(
                "INVALID_REQUEST",
                "currency_code must be a 3-character ISO 4217 code",
                status_code=400,
            )

        # Validate value (must be positive and have valid decimal places)
        if value <= 0:
            raise PaymentError(
                "INVALID_REQUEST",
                "value must be greater than 0",
                status_code=400,
            )

        # JPY and other 0-decimal currencies: value must be integer
        # For now accept any Decimal format, validation at response time
        if virtual_now is None:
            virtual_now = datetime.now(UTC)

        order = Order(
            id=generate_order_id(),
            universe_id=universe_id,
            intent=intent,
            status="CREATED",
            currency_code=currency_code,
            value=value,
            return_url=return_url,
            cancel_url=cancel_url,
            created_at=datetime.now(UTC),
            created_virtual_at=virtual_now,
        )

        db.add(order)
        await db.flush()
        return order

    @staticmethod
    async def get_order(
        db: AsyncSession,
        universe_id: uuid.UUID,
        order_id: str,
    ) -> Order:
        """Get an order by ID.

        Args:
            db: Database session
            universe_id: Universe ID (for cross-universe protection)
            order_id: Order ID

        Returns:
            Order object

        Raises:
            PaymentError: If order not found
        """
        result = await db.execute(
            select(Order).where(
                Order.id == order_id,
                Order.universe_id == universe_id,
            )
        )
        order = result.scalars().first()
        if not order:
            raise PaymentError(
                "RESOURCE_NOT_FOUND",
                f"Order {order_id} not found",
                status_code=404,
            )
        return order

    @staticmethod
    async def approve_order(
        db: AsyncSession,
        order: Order,
        payer_id: str,
        virtual_now: datetime,
    ) -> None:
        """Move an order to APPROVED status.

        Args:
            db: Database session
            order: Order to approve
            payer_id: Payer ID from approval flow
            virtual_now: Current virtual time
        """
        order.status = "APPROVED"
        order.payer_id = payer_id
        order.approved_virtual_at = virtual_now

        # Emit webhook event
        await emit_event(
            db=db,
            universe_id=order.universe_id,
            event_type="CHECKOUT.ORDER.APPROVED",
            resource_type="order",
            summary=f"Order {order.id} approved by payer",
            resource=OrderService._order_to_dict(order),
            virtual_now=virtual_now,
        )

    @staticmethod
    async def capture_order(
        db: AsyncSession,
        order: Order,
        virtual_now: datetime,
    ) -> Capture:
        """Capture (charge) an order.

        When intent is CAPTURE, capture directly. When intent is AUTHORIZE, an authorization
        must exist and not be expired.

        Args:
            db: Database session
            order: Order to capture
            virtual_now: Current virtual time

        Returns:
            Created Capture object

        Raises:
            PaymentError: On validation failure
        """
        # Check order is APPROVED
        if order.status != "APPROVED":
            raise PaymentError(
                "ORDER_NOT_APPROVED",
                f"Order {order.id} must be in APPROVED status, current status: {order.status}",
                status_code=422,
                details=[{"issue": "order_status", "description": f"Order status is {order.status}"}],
            )

        # If intent is AUTHORIZE, check authorization
        if order.intent == "AUTHORIZE":
            # Get the authorization for this order
            result = await db.execute(
                select(Authorization).where(
                    Authorization.order_id == order.id,
                    Authorization.status == "APPROVED",
                )
            )
            auth = result.scalars().first()
            if not auth:
                raise PaymentError(
                    "AUTHORIZATION_NOT_FOUND",
                    f"No approved authorization for order {order.id}",
                    status_code=422,
                )

            # Check authorization is not expired
            if auth.expires_virtual_at and virtual_now > auth.expires_virtual_at:
                raise PaymentError(
                    "AUTHORIZATION_EXPIRED",
                    f"Authorization {auth.id} has expired",
                    status_code=422,
                    details=[{"issue": "authorization_expired", "description": "The authorization has expired"}],
                )

            # Calculate total already captured
            captures_result = await db.execute(
                select(Capture).where(Capture.authorization_id == auth.id)
            )
            existing_captures = captures_result.scalars().all()
            total_captured = sum(c.amount for c in existing_captures)

            # Check we don't exceed authorized amount
            if total_captured + order.value > auth.amount:
                raise PaymentError(
                    "CAPTURE_AMOUNT_INVALID",
                    f"Capture amount {order.value} exceeds remaining authorized amount {auth.amount - total_captured}",
                    status_code=422,
                )

        capture = Capture(
            id=generate_capture_id(),
            universe_id=order.universe_id,
            order_id=order.id,
            authorization_id=None,  # Will be set if AUTHORIZE intent
            status="COMPLETED",
            currency_code=order.currency_code,
            amount=order.value,
            final_capture=True,
            created_virtual_at=virtual_now,
        )

        db.add(capture)
        order.status = "COMPLETED"
        order.completed_virtual_at = virtual_now

        # Add to account balance
        await _update_account_balance(
            db,
            order.universe_id,
            order.currency_code,
            order.value,
        )

        # Emit webhook event
        await emit_event(
            db=db,
            universe_id=order.universe_id,
            event_type="PAYMENT.CAPTURE.COMPLETED",
            resource_type="capture",
            summary=f"Capture {capture.id} completed for order {order.id}",
            resource=CaptureService._capture_to_dict(capture),
            virtual_now=virtual_now,
        )

        return capture

    @staticmethod
    async def authorize_order(
        db: AsyncSession,
        order: Order,
        virtual_now: datetime,
    ) -> Authorization:
        """Create an authorization for an order (intent=AUTHORIZE).

        Args:
            db: Database session
            order: Order to authorize
            virtual_now: Current virtual time

        Returns:
            Created Authorization object

        Raises:
            PaymentError: On validation failure
        """
        if order.intent != "AUTHORIZE":
            raise PaymentError(
                "INVALID_REQUEST",
                f"Order {order.id} has intent {order.intent}, not AUTHORIZE",
                status_code=400,
            )

        if order.status != "APPROVED":
            raise PaymentError(
                "ORDER_NOT_APPROVED",
                f"Order {order.id} must be in APPROVED status",
                status_code=422,
            )

        return await AuthorizationService.create_authorization(
            db=db,
            order=order,
            virtual_now=virtual_now,
        )

    @staticmethod
    def _order_to_dict(order: Order) -> dict[str, Any]:
        """Convert Order to dict for API response."""
        links = [
            {
                "rel": "self",
                "href": f"/v2/checkout/orders/{order.id}",
                "method": "GET",
            },
            {
                "rel": "approve",
                "href": f"/checkoutnow?token={order.id}",
                "method": "GET",
            },
        ]

        return {
            "id": order.id,
            "status": order.status,
            "intent": order.intent,
            "payer": {"email_address": f"payer+{order.id}@example.com"} if order.payer_id else {},
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": order.currency_code,
                        "value": str(order.value),
                    },
                }
            ],
            "create_time": order.created_at.isoformat(),
            "links": links,
        }


class AuthorizationService:
    """Service for managing Authorizations."""

    @staticmethod
    async def create_authorization(
        db: AsyncSession,
        order: Order,
        virtual_now: datetime,
    ) -> Authorization:
        """Create an authorization for an order.

        Args:
            db: Database session
            order: Order to authorize
            virtual_now: Current virtual time

        Returns:
            Created Authorization object
        """
        # Create authorization with honor period and expiry
        # AUTH-HONOR: 3 days
        # AUTH-VALID: 29 days
        honor_period_days = 3
        validity_days = 29

        honor_period_ends_at = virtual_now + timedelta(days=honor_period_days)
        expires_at = virtual_now + timedelta(days=validity_days)

        auth = Authorization(
            id=generate_authorization_id(),
            universe_id=order.universe_id,
            order_id=order.id,
            status="APPROVED",
            currency_code=order.currency_code,
            amount=order.value,
            created_virtual_at=virtual_now,
            honor_period_ends_virtual_at=honor_period_ends_at,
            expires_virtual_at=expires_at,
        )

        db.add(auth)

        # Schedule events:
        # - auth.honor_end at +3 days: just logs the event
        # - auth.expire at +29 days: moves auth to EXPIRED state
        from app.models import ScheduledEvent

        honor_end_event = ScheduledEvent(
            universe_id=order.universe_id,
            due_at=honor_period_ends_at,
            kind="auth.honor_end",
            payload={"authorization_id": auth.id},
            status="pending",
        )
        db.add(honor_end_event)

        expire_event = ScheduledEvent(
            universe_id=order.universe_id,
            due_at=expires_at,
            kind="auth.expire",
            payload={"authorization_id": auth.id},
            status="pending",
        )
        db.add(expire_event)

        # Emit webhook event
        await emit_event(
            db=db,
            universe_id=order.universe_id,
            event_type="PAYMENT.AUTHORIZATION.CREATED",
            resource_type="authorization",
            summary=f"Authorization {auth.id} created for order {order.id}",
            resource=AuthorizationService._authorization_to_dict(auth),
            virtual_now=virtual_now,
        )

        return auth

    @staticmethod
    async def get_authorization(
        db: AsyncSession,
        universe_id: uuid.UUID,
        auth_id: str,
    ) -> Authorization:
        """Get an authorization by ID.

        Args:
            db: Database session
            universe_id: Universe ID (for cross-universe protection)
            auth_id: Authorization ID

        Returns:
            Authorization object

        Raises:
            PaymentError: If authorization not found
        """
        result = await db.execute(
            select(Authorization).where(
                Authorization.id == auth_id,
                Authorization.universe_id == universe_id,
            )
        )
        auth = result.scalars().first()
        if not auth:
            raise PaymentError(
                "RESOURCE_NOT_FOUND",
                f"Authorization {auth_id} not found",
                status_code=404,
            )
        return auth

    @staticmethod
    async def capture_authorization(
        db: AsyncSession,
        auth: Authorization,
        amount: Decimal | None = None,
        final_capture: bool = False,
        invoice_id: str | None = None,
        note_to_payer: str | None = None,
        virtual_now: datetime = None,
    ) -> Capture:
        """Capture (charge) an authorization, possibly partially.

        Args:
            db: Database session
            auth: Authorization to capture
            amount: Amount to capture (defaults to full auth amount)
            final_capture: Whether this is the final capture
            invoice_id: Optional invoice ID
            note_to_payer: Optional note to payer
            virtual_now: Current virtual time

        Returns:
            Created Capture object

        Raises:
            PaymentError: On validation failure
        """
        if virtual_now is None:
            virtual_now = datetime.now(UTC)

        # Check authorization is not expired
        if auth.expires_virtual_at and virtual_now > auth.expires_virtual_at:
            raise PaymentError(
                "AUTHORIZATION_EXPIRED",
                f"Authorization {auth.id} has expired",
                status_code=422,
                details=[{"issue": "authorization_expired", "description": "The authorization has expired"}],
            )

        # Use full amount if not specified
        if amount is None:
            amount = auth.amount

        # Check amount
        if amount <= 0:
            raise PaymentError(
                "INVALID_REQUEST",
                "Capture amount must be greater than 0",
                status_code=400,
            )

        # Calculate total already captured
        captures_result = await db.execute(
            select(Capture).where(Capture.authorization_id == auth.id)
        )
        existing_captures = captures_result.scalars().all()
        total_captured = sum(c.amount for c in existing_captures)

        # Check we don't exceed authorized amount
        if total_captured + amount > auth.amount:
            raise PaymentError(
                "CAPTURE_AMOUNT_INVALID",
                f"Capture amount {amount} exceeds remaining authorized amount {auth.amount - total_captured}",
                status_code=422,
            )

        capture = Capture(
            id=generate_capture_id(),
            universe_id=auth.universe_id,
            order_id=auth.order_id,
            authorization_id=auth.id,
            status="COMPLETED",
            currency_code=auth.currency_code,
            amount=amount,
            final_capture=final_capture,
            invoice_id=invoice_id,
            note_to_payer=note_to_payer,
            created_virtual_at=virtual_now,
        )

        db.add(capture)

        # Add to account balance
        await _update_account_balance(
            db,
            auth.universe_id,
            auth.currency_code,
            amount,
        )

        # Emit webhook event
        await emit_event(
            db=db,
            universe_id=auth.universe_id,
            event_type="PAYMENT.CAPTURE.COMPLETED",
            resource_type="capture",
            summary=f"Capture {capture.id} completed for authorization {auth.id}",
            resource=CaptureService._capture_to_dict(capture),
            virtual_now=virtual_now,
        )

        return capture

    @staticmethod
    async def void_authorization(
        db: AsyncSession,
        auth: Authorization,
        virtual_now: datetime = None,
    ) -> None:
        """Void (cancel) an authorization.

        Args:
            db: Database session
            auth: Authorization to void
            virtual_now: Current virtual time
        """
        if virtual_now is None:
            virtual_now = datetime.now(UTC)

        auth.status = "VOIDED"
        auth.voided_virtual_at = virtual_now

        # Emit webhook event
        await emit_event(
            db=db,
            universe_id=auth.universe_id,
            event_type="PAYMENT.AUTHORIZATION.VOIDED",
            resource_type="authorization",
            summary=f"Authorization {auth.id} voided",
            resource=AuthorizationService._authorization_to_dict(auth),
            virtual_now=virtual_now,
        )

    @staticmethod
    def _authorization_to_dict(auth: Authorization) -> dict[str, Any]:
        """Convert Authorization to dict for API response."""
        links = [
            {
                "rel": "self",
                "href": f"/v2/payments/authorizations/{auth.id}",
                "method": "GET",
            },
            {
                "rel": "capture",
                "href": f"/v2/payments/authorizations/{auth.id}/capture",
                "method": "POST",
            },
            {
                "rel": "void",
                "href": f"/v2/payments/authorizations/{auth.id}/void",
                "method": "POST",
            },
        ]

        result = {
            "id": auth.id,
            "status": auth.status,
            "amount": {
                "currency_code": auth.currency_code,
                "value": str(auth.amount),
            },
            "create_time": auth.created_virtual_at.isoformat(),
            "links": links,
        }

        if auth.honor_period_ends_virtual_at:
            result["honor_period_ends_at"] = auth.honor_period_ends_virtual_at.isoformat()

        if auth.expires_virtual_at:
            result["expiration_time"] = auth.expires_virtual_at.isoformat()

        return result


class CaptureService:
    """Service for managing Captures."""

    @staticmethod
    async def get_capture(
        db: AsyncSession,
        universe_id: uuid.UUID,
        capture_id: str,
    ) -> Capture:
        """Get a capture by ID.

        Args:
            db: Database session
            universe_id: Universe ID (for cross-universe protection)
            capture_id: Capture ID

        Returns:
            Capture object

        Raises:
            PaymentError: If capture not found
        """
        result = await db.execute(
            select(Capture).where(
                Capture.id == capture_id,
                Capture.universe_id == universe_id,
            )
        )
        capture = result.scalars().first()
        if not capture:
            raise PaymentError(
                "RESOURCE_NOT_FOUND",
                f"Capture {capture_id} not found",
                status_code=404,
            )
        return capture

    @staticmethod
    async def refund_capture(
        db: AsyncSession,
        capture: Capture,
        amount: Decimal | None = None,
        note_to_payer: str | None = None,
        virtual_now: datetime = None,
    ) -> Refund:
        """Refund a capture, possibly partially.

        Args:
            db: Database session
            capture: Capture to refund
            amount: Amount to refund (defaults to full capture amount)
            note_to_payer: Optional note to payer
            virtual_now: Current virtual time

        Returns:
            Created Refund object

        Raises:
            PaymentError: On validation failure
        """
        if virtual_now is None:
            virtual_now = datetime.now(UTC)

        # Use full amount if not specified
        if amount is None:
            amount = capture.amount

        # Check amount
        if amount <= 0:
            raise PaymentError(
                "INVALID_REQUEST",
                "Refund amount must be greater than 0",
                status_code=400,
            )

        # Calculate total already refunded
        refunds_result = await db.execute(
            select(Refund).where(Refund.capture_id == capture.id)
        )
        existing_refunds = refunds_result.scalars().all()
        total_refunded = sum(r.amount for r in existing_refunds)

        # Check we don't exceed capture amount
        if total_refunded + amount > capture.amount:
            raise PaymentError(
                "REFUND_AMOUNT_INVALID",
                f"Refund amount {amount} exceeds remaining capture amount {capture.amount - total_refunded}",
                status_code=422,
            )

        refund = Refund(
            id=generate_refund_id(),
            universe_id=capture.universe_id,
            capture_id=capture.id,
            status="COMPLETED",
            currency_code=capture.currency_code,
            amount=amount,
            note_to_payer=note_to_payer,
            created_virtual_at=virtual_now,
        )

        db.add(refund)

        # Subtract from account balance
        await _update_account_balance(
            db,
            capture.universe_id,
            capture.currency_code,
            -amount,
        )

        # Emit webhook event
        await emit_event(
            db=db,
            universe_id=capture.universe_id,
            event_type="PAYMENT.CAPTURE.REFUNDED",
            resource_type="refund",
            summary=f"Refund {refund.id} completed for capture {capture.id}",
            resource=RefundService._refund_to_dict(refund),
            virtual_now=virtual_now,
        )

        return refund

    @staticmethod
    def _capture_to_dict(capture: Capture) -> dict[str, Any]:
        """Convert Capture to dict for API response."""
        links = [
            {
                "rel": "self",
                "href": f"/v2/payments/captures/{capture.id}",
                "method": "GET",
            },
            {
                "rel": "refund",
                "href": f"/v2/payments/captures/{capture.id}/refund",
                "method": "POST",
            },
        ]

        return {
            "id": capture.id,
            "status": capture.status,
            "amount": {
                "currency_code": capture.currency_code,
                "value": str(capture.amount),
            },
            "final_capture": capture.final_capture,
            "create_time": capture.created_virtual_at.isoformat(),
            "links": links,
        }


class RefundService:
    """Service for managing Refunds."""

    @staticmethod
    async def get_refund(
        db: AsyncSession,
        universe_id: uuid.UUID,
        refund_id: str,
    ) -> Refund:
        """Get a refund by ID.

        Args:
            db: Database session
            universe_id: Universe ID (for cross-universe protection)
            refund_id: Refund ID

        Returns:
            Refund object

        Raises:
            PaymentError: If refund not found
        """
        result = await db.execute(
            select(Refund).where(
                Refund.id == refund_id,
                Refund.universe_id == universe_id,
            )
        )
        refund = result.scalars().first()
        if not refund:
            raise PaymentError(
                "RESOURCE_NOT_FOUND",
                f"Refund {refund_id} not found",
                status_code=404,
            )
        return refund

    @staticmethod
    def _refund_to_dict(refund: Refund) -> dict[str, Any]:
        """Convert Refund to dict for API response."""
        links = [
            {
                "rel": "self",
                "href": f"/v2/payments/refunds/{refund.id}",
                "method": "GET",
            },
        ]

        return {
            "id": refund.id,
            "status": refund.status,
            "amount": {
                "currency_code": refund.currency_code,
                "value": str(refund.amount),
            },
            "create_time": refund.created_virtual_at.isoformat(),
            "links": links,
        }


async def _update_account_balance(
    db: AsyncSession,
    universe_id: uuid.UUID,
    currency_code: str,
    delta: Decimal,
) -> None:
    """Update account balance for a universe/currency.

    Args:
        db: Database session
        universe_id: Universe ID
        currency_code: Currency code
        delta: Amount to add (can be negative for refunds)
    """
    result = await db.execute(
        select(AccountBalance).where(
            AccountBalance.universe_id == universe_id,
            AccountBalance.currency_code == currency_code,
        )
    )
    balance = result.scalars().first()

    if balance:
        balance.available += delta
    else:
        balance = AccountBalance(
            universe_id=universe_id,
            currency_code=currency_code,
            available=delta if delta > 0 else Decimal("0.00"),
        )
        db.add(balance)
