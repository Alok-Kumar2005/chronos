"""Reference-fix shop handlers for Lumen Store - the correct implementation.

This module is identical to handlers_buggy.py but with all known bugs fixed.
It serves as the ground truth for testing.
"""

import logging
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ShopAppliedWebhookEvent,
    ShopCreator,
    ShopErrorLog,
    ShopLedgerEntry,
    ShopOrder,
    ShopSettings,
)
from app.shop.paypal_client import PayPalClient, PayPalClientError

logger = logging.getLogger(__name__)


class ReferenceFixShopHandlers:
    """Shop handlers with all bugs fixed (reference implementation)."""

    def __init__(self, paypal_client: PayPalClient, db_session: AsyncSession):
        self.paypal_client = paypal_client
        self.db = db_session

    async def place_order(
        self,
        product_id: str,
        customer_name: str,
        payer_email: str,
        amount: Decimal,
        universe_id: str,
    ) -> tuple[str, str]:
        """Place an order and create PayPal authorization."""
        try:
            # Create PayPal order with AUTHORIZE intent
            order_data = await self.paypal_client.create_order(
                intent="AUTHORIZE",
                amount=amount,
                currency_code="USD",
                return_url="http://localhost:3000/shop/return",
                cancel_url="http://localhost:3000/shop/cancel",
            )

            paypal_order_id = order_data["id"]

            # Create shop order in database
            from app.twin.clock import get_now
            now = await get_now(self.db, universe_id)

            shop_order = ShopOrder(
                id=f"order_{paypal_order_id}",
                universe_id=universe_id,
                product_id=product_id,
                paypal_order_id=paypal_order_id,
                status="created",
                amount=amount,
                payer_email=payer_email,
                customer_name=customer_name,
                quantity=1,
                created_virtual_at=now,
            )
            self.db.add(shop_order)
            await self.db.flush()

            # Extract approval link
            approval_link = next(
                (link["href"] for link in order_data.get("links", []) if link["rel"] == "approve"),
                None,
            )

            return shop_order.id, approval_link

        except PayPalClientError as e:
            logger.error(f"Failed to place order: {e}")
            raise

    async def authorize_order_after_approval(
        self,
        shop_order_id: str,
        payer_id: str,
        universe_id: str,
    ) -> None:
        """Called when customer returns from PayPal approval."""
        # Fetch shop order
        result = await self.db.execute(
            select(ShopOrder).where(
                and_(ShopOrder.id == shop_order_id, ShopOrder.universe_id == universe_id)
            )
        )
        shop_order = result.scalar_one_or_none()
        if not shop_order:
            raise ValueError(f"Shop order not found: {shop_order_id}")

        # Approve PayPal order
        await self.paypal_client.approve_order(shop_order.paypal_order_id, payer_id)

        # Authorize (creates authorization for later capture on ship)
        auth_data = await self.paypal_client.authorize_order(shop_order.paypal_order_id)
        paypal_auth_id = auth_data["id"]

        # Update shop order
        from app.twin.clock import get_now
        now = await get_now(self.db, universe_id)

        shop_order.paypal_authorization_id = paypal_auth_id
        shop_order.status = "authorized"
        shop_order.authorized_virtual_at = now
        await self.db.flush()

    async def ship_order(
        self,
        shop_order_id: str,
        universe_id: str,
    ) -> None:
        """Ship an order and capture the authorization.

        FIX for B1: Only mark shipped/paid if capture succeeds.
        """
        # Fetch shop order
        result = await self.db.execute(
            select(ShopOrder).where(
                and_(ShopOrder.id == shop_order_id, ShopOrder.universe_id == universe_id)
            )
        )
        shop_order = result.scalar_one_or_none()
        if not shop_order:
            raise ValueError(f"Shop order not found: {shop_order_id}")

        from app.twin.clock import get_now
        now = await get_now(self.db, universe_id)

        try:
            # Attempt to capture the authorization
            capture_data = await self.paypal_client.capture_authorization(
                shop_order.paypal_authorization_id,
                final_capture=True,
            )
            paypal_capture_id = capture_data["id"]
        except PayPalClientError as e:
            # FIX B1: Log the error but DON'T mark shipped/paid. Propagate or retry.
            logger.error(
                f"Capture failed for order {shop_order_id}: {e.error_name}",
                exc_info=e,
            )
            # Record the error (not swallowed)
            error_log = ShopErrorLog(
                universe_id=universe_id,
                operation="capture_on_ship",
                error_code=e.error_name,
                error_message=str(e),
                swallowed=False,
                created_virtual_at=now,
            )
            self.db.add(error_log)
            await self.db.flush()
            # Re-raise so the caller knows
            raise

        # Capture succeeded
        shop_order.paypal_capture_id = paypal_capture_id
        shop_order.status = "shipped"
        shop_order.shipped_virtual_at = now
        shop_order.status = "paid"
        shop_order.paid_virtual_at = now
        await self.db.flush()

        # Record the capture in the ledger
        result_settings = await self.db.execute(
            select(ShopSettings).where(ShopSettings.universe_id == universe_id)
        )
        settings = result_settings.scalar_one()

        creator_email = shop_order.product.creator_email
        creator_pct = settings.creator_payout_percentage
        creator_amount = shop_order.amount * creator_pct

        # Ledger: shop_revenue gets the full amount
        revenue_entry = ShopLedgerEntry(
            universe_id=universe_id,
            account="shop_revenue",
            amount=shop_order.amount,
            order_id=shop_order.id,
            source_event_id=paypal_capture_id,
            kind="capture",
            created_virtual_at=now,
        )
        self.db.add(revenue_entry)

        # Ledger: creator balance increases
        result = await self.db.execute(
            select(ShopCreator).where(
                and_(ShopCreator.universe_id == universe_id, ShopCreator.email == creator_email)
            )
        )
        creator = result.scalar_one_or_none()
        if not creator:
            creator = ShopCreator(
                universe_id=universe_id,
                email=creator_email,
                balance=Decimal("0.00"),
            )
            self.db.add(creator)
            await self.db.flush()

        creator.balance += creator_amount

        # Ledger: creator payable
        creator_entry = ShopLedgerEntry(
            universe_id=universe_id,
            account=f"creator_{creator_email}",
            amount=creator_amount,
            order_id=shop_order.id,
            source_event_id=paypal_capture_id,
            kind="capture",
            created_virtual_at=now,
        )
        self.db.add(creator_entry)
        await self.db.flush()

    async def handle_capture_completed_webhook(
        self,
        webhook_event_id: str,
        capture_id: str,
        amount: str,
        universe_id: str,
    ) -> None:
        """Handle PAYMENT.CAPTURE.COMPLETED webhook.

        FIX for B4: Check idempotency before applying.
        """
        # FIX B4: Check if already applied
        result = await self.db.execute(
            select(ShopAppliedWebhookEvent).where(
                and_(
                    ShopAppliedWebhookEvent.universe_id == universe_id,
                    ShopAppliedWebhookEvent.source_event_id == webhook_event_id,
                )
            )
        )
        if result.scalar_one_or_none():
            logger.info(f"Webhook {webhook_event_id} already applied")
            return

        from app.twin.clock import get_now
        now = await get_now(self.db, universe_id)

        # Find the shop order by paypal_capture_id
        result = await self.db.execute(
            select(ShopOrder).where(
                and_(ShopOrder.universe_id == universe_id, ShopOrder.paypal_capture_id == capture_id)
            )
        )
        shop_order = result.scalar_one_or_none()
        if not shop_order:
            logger.warning(f"No shop order found for capture {capture_id}")
            return

        if shop_order.status == "paid":
            logger.info(f"Order {shop_order.id} already paid")
            return

        result_settings = await self.db.execute(
            select(ShopSettings).where(ShopSettings.universe_id == universe_id)
        )
        settings = result_settings.scalar_one()

        creator_email = shop_order.product.creator_email
        creator_pct = settings.creator_payout_percentage
        amount_decimal = Decimal(amount)
        creator_amount = amount_decimal * creator_pct

        # Ledger entry
        ledger_entry = ShopLedgerEntry(
            universe_id=universe_id,
            account=f"creator_{creator_email}",
            amount=creator_amount,
            order_id=shop_order.id,
            source_event_id=webhook_event_id,
            kind="capture",
            created_virtual_at=now,
        )
        self.db.add(ledger_entry)

        result = await self.db.execute(
            select(ShopCreator).where(
                and_(ShopCreator.universe_id == universe_id, ShopCreator.email == creator_email)
            )
        )
        creator = result.scalar_one_or_none()
        if creator:
            creator.balance += creator_amount
        else:
            creator = ShopCreator(
                universe_id=universe_id,
                email=creator_email,
                balance=creator_amount,
            )
            self.db.add(creator)

        shop_order.status = "paid"
        shop_order.paid_virtual_at = now

        # FIX B4: Mark as applied to prevent re-processing
        applied_event = ShopAppliedWebhookEvent(
            universe_id=universe_id,
            source_event_id=webhook_event_id,
            applied_virtual_at=now,
        )
        self.db.add(applied_event)

        await self.db.flush()

    async def run_weekly_payout(
        self,
        universe_id: str,
    ) -> str | None:
        """Run weekly payout: collect creator balances and submit a payout batch.

        FIX for B3: Don't mark paid until items are SUCCESS.
        """
        from app.twin.clock import get_now

        now = await get_now(self.db, universe_id)

        # Collect all creators with balance > 0
        result = await self.db.execute(
            select(ShopCreator).where(
                and_(ShopCreator.universe_id == universe_id, ShopCreator.balance > Decimal("0.00"))
            )
        )
        creators = result.scalars().all()

        if not creators:
            logger.info(f"No creators to pay in universe {universe_id}")
            return None

        result_settings = await self.db.execute(
            select(ShopSettings).where(ShopSettings.universe_id == universe_id)
        )
        result_settings.scalar_one()

        # Build payout items
        payout_items = []
        for creator in creators:
            payout_items.append(
                {
                    "recipient_type": "EMAIL",
                    "receiver": creator.email,
                    "amount": str(creator.balance),
                    "sender_item_id": f"creator_{creator.email}_{now.isoformat()}",
                    "note": f"Weekly payout for {creator.email}",
                }
            )

        # Submit payout batch
        sender_batch_id = f"payout_{universe_id}_{now.timestamp()}"
        payout_data = await self.paypal_client.create_payout_batch(
            sender_batch_id=sender_batch_id,
            email_subject="Weekly Payout",
            email_message="Your weekly payout from Lumen Store",
            currency_code="USD",
            items=payout_items,
        )

        paypal_batch_id = payout_data["id"]

        # FIX B3: DON'T mark as paid yet. Wait for SUCCESS webhook.
        # Only mark the batch ID for tracking.
        for creator in creators:
            creator.payout_status = "submitted"
            creator.last_payout_virtual_at = now
            creator.last_payout_batch_id = paypal_batch_id
            # NOTE: Don't clear balance until SUCCESS webhook

        await self.db.flush()

        return paypal_batch_id

    async def handle_payout_item_returned_webhook(
        self,
        webhook_event_id: str,
        item_id: str,
        amount: str,
        receiver_email: str,
        universe_id: str,
    ) -> None:
        """Handle PAYMENT.PAYOUTS-ITEM.RETURNED webhook.

        FIX for B3: Credit the platform/sender, not the creator.
        """
        from app.twin.clock import get_now

        now = await get_now(self.db, universe_id)
        amount_decimal = Decimal(amount)

        # FIX B3: Credit the platform account (shop_revenue), not the creator
        platform_ledger = ShopLedgerEntry(
            universe_id=universe_id,
            account="shop_revenue",
            amount=amount_decimal,  # Positive: money returns to shop
            source_event_id=webhook_event_id,
            kind="payout_returned",
            created_virtual_at=now,
        )
        self.db.add(platform_ledger)

        await self.db.flush()
