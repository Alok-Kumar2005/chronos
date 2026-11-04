"""PayPal Payouts v1 API business logic."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountBalance, PayoutBatch, PayoutItem, PayoutRecipient, ScheduledEvent
from app.twin.ids import generate_batch_id, generate_payout_item_id
from app.twin.webhooks import emit_event

if TYPE_CHECKING:
    from app.twin.scheduler import SchedulerContext


class PayoutError(Exception):
    """Custom payout error."""

    def __init__(self, name: str, message: str, http_status: int = 400, details: list[dict[str, str]] | None = None):
        """Initialize error."""
        self.name = name
        self.message = message
        self.http_status = http_status
        self.details = details or []
        super().__init__(self.message)


class PayoutService:
    """Business logic for payout operations."""

    # Per-item fee (rule: PAYOUT-FEE, confidence: unverified unless set in rules)
    PAYOUT_FEE_PER_ITEM = Decimal("0.20")

    # Unclaimed item return period (rule: PAYOUT-UNCLAIMED, 30 days)
    UNCLAIMED_RETURN_DAYS = 30

    @staticmethod
    async def create_payout_batch(
        session: AsyncSession,
        universe_id: uuid.UUID,
        sender_batch_id: str,
        sender_batch_header: dict[str, Any],
        items: list[dict[str, Any]],
        virtual_now: datetime,
    ) -> dict[str, Any]:
        """Create a payout batch.

        Args:
            session: Database session
            universe_id: Universe ID
            sender_batch_id: User-supplied batch ID
            sender_batch_header: Batch header with email_subject, email_message
            items: List of payout items with recipient_type, receiver, amount, note, sender_item_id, recipient_wallet
            virtual_now: Current virtual time

        Returns:
            Payout batch dict

        Raises:
            PayoutError: If batch with sender_batch_id already exists or validation fails
        """
        # Check for duplicate sender_batch_id
        existing = await session.execute(
            select(PayoutBatch).where(
                PayoutBatch.universe_id == universe_id,
                PayoutBatch.sender_batch_id == sender_batch_id,
            )
        )
        if existing.scalars().first():
            raise PayoutError(
                name="USER_BUSINESS_ERROR",
                message="Batch with given sender_batch_id already exists",
                http_status=400,
                details=[{"issue": "sender_batch_id", "description": "Batch with this ID already exists"}],
            )

        # Validate items
        if not items:
            raise PayoutError(
                name="INVALID_REQUEST",
                message="At least one payout item is required",
                http_status=400,
            )

        # Calculate totals and validate amounts
        total_amount = Decimal("0")
        total_fee = Decimal("0")
        for item in items:
            amount = Decimal(str(item.get("amount", 0)))
            if amount <= 0:
                raise PayoutError(
                    name="INVALID_REQUEST",
                    message="Payout item amount must be greater than 0",
                    http_status=400,
                )
            total_amount += amount
            total_fee += PayoutService.PAYOUT_FEE_PER_ITEM

        # Determine currency from first item (or from header if present)
        currency_code = sender_batch_header.get("currency_code", "USD")

        # Debit sender's account (amount + fee)
        await PayoutService._update_account_balance(
            session, universe_id, currency_code, -(total_amount + total_fee)
        )

        # Create batch
        batch_id = generate_batch_id()
        batch = PayoutBatch(
            id=batch_id,
            universe_id=universe_id,
            sender_batch_id=sender_batch_id,
            sender_batch_header=sender_batch_header,
            currency_code=currency_code,
            total_amount=total_amount,
            total_fee=total_fee,
            item_count=len(items),
            created_virtual_at=virtual_now,
        )
        session.add(batch)

        # Create items
        for item_data in items:
            item_id = generate_payout_item_id()
            item = PayoutItem(
                id=item_id,
                universe_id=universe_id,
                batch_id=batch_id,
                sender_item_id=item_data.get("sender_item_id", ""),
                recipient_type=item_data.get("recipient_type", "EMAIL"),
                receiver=item_data.get("receiver", ""),
                amount=Decimal(str(item_data.get("amount", 0))),
                fee=PayoutService.PAYOUT_FEE_PER_ITEM,
                note=item_data.get("note"),
                recipient_wallet=item_data.get("recipient_wallet"),
                created_virtual_at=virtual_now,
            )
            session.add(item)

        await session.flush()

        # Return batch representation
        return {
            "batch_header": {
                "payout_batch_id": batch_id,
                "batch_status": "PENDING",
                "sender_batch_id": sender_batch_id,
                "sender_batch_header": sender_batch_header,
                "time_created": virtual_now.isoformat(),
                "time_completed": None,
            },
            "links": [
                {"rel": "self", "href": f"/v1/payments/payouts/{batch_id}"},
                {"rel": "up", "href": "/v1/payments/payouts"},
            ],
        }

    @staticmethod
    async def get_payout_batch(
        session: AsyncSession,
        universe_id: uuid.UUID,
        batch_id: str,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Get a payout batch with pagination.

        Args:
            session: Database session
            universe_id: Universe ID
            batch_id: Batch ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Payout batch dict with paginated items

        Raises:
            PayoutError: If batch not found
        """
        batch = await session.execute(
            select(PayoutBatch).where(
                PayoutBatch.universe_id == universe_id,
                PayoutBatch.id == batch_id,
            )
        )
        batch_obj = batch.scalars().first()
        if not batch_obj:
            raise PayoutError(
                name="RESOURCE_NOT_FOUND",
                message="Payout batch not found",
                http_status=404,
            )

        # Get paginated items
        offset = (page - 1) * page_size
        items_result = await session.execute(
            select(PayoutItem)
            .where(PayoutItem.batch_id == batch_id)
            .offset(offset)
            .limit(page_size)
        )
        items = items_result.scalars().all()

        # Get total count
        count_result = await session.execute(
            select(PayoutItem).where(PayoutItem.batch_id == batch_id)
        )
        total_items = len(count_result.scalars().all())

        return {
            "batch_header": {
                "payout_batch_id": batch_obj.id,
                "batch_status": batch_obj.status,
                "sender_batch_id": batch_obj.sender_batch_id,
                "sender_batch_header": batch_obj.sender_batch_header,
                "time_created": batch_obj.created_virtual_at.isoformat(),
                "time_completed": batch_obj.processed_virtual_at.isoformat() if batch_obj.processed_virtual_at else None,
            },
            "payout_items": [PayoutService._payout_item_to_dict(item) for item in items],
            "pagination": {
                "total_items": total_items,
                "total_pages": (total_items + page_size - 1) // page_size,
                "current_page": page,
                "page_size": page_size,
            },
            "links": [
                {"rel": "self", "href": f"/v1/payments/payouts/{batch_id}?page={page}&page_size={page_size}"},
                {"rel": "up", "href": "/v1/payments/payouts"},
            ],
        }

    @staticmethod
    async def get_payout_item(
        session: AsyncSession,
        universe_id: uuid.UUID,
        item_id: str,
    ) -> dict[str, Any]:
        """Get a payout item.

        Args:
            session: Database session
            universe_id: Universe ID
            item_id: Item ID

        Returns:
            Payout item dict

        Raises:
            PayoutError: If item not found
        """
        item = await session.execute(
            select(PayoutItem).where(
                PayoutItem.universe_id == universe_id,
                PayoutItem.id == item_id,
            )
        )
        item_obj = item.scalars().first()
        if not item_obj:
            raise PayoutError(
                name="RESOURCE_NOT_FOUND",
                message="Payout item not found",
                http_status=404,
            )

        return PayoutService._payout_item_to_dict(item_obj)

    @staticmethod
    async def cancel_payout_item(
        session: AsyncSession,
        universe_id: uuid.UUID,
        item_id: str,
        virtual_now: datetime,
    ) -> dict[str, Any]:
        """Cancel a payout item (only UNCLAIMED items).

        Args:
            session: Database session
            universe_id: Universe ID
            item_id: Item ID
            virtual_now: Current virtual time

        Returns:
            Updated payout item dict

        Raises:
            PayoutError: If item not found or not UNCLAIMED
        """
        item = await session.execute(
            select(PayoutItem).where(
                PayoutItem.universe_id == universe_id,
                PayoutItem.id == item_id,
            )
        )
        item_obj = item.scalars().first()
        if not item_obj:
            raise PayoutError(
                name="RESOURCE_NOT_FOUND",
                message="Payout item not found",
                http_status=404,
            )

        if item_obj.status != "UNCLAIMED":
            raise PayoutError(
                name="ITEM_INCORRECT_STATUS",
                message=f"Item status {item_obj.status} cannot be cancelled. Only UNCLAIMED items can be cancelled.",
                http_status=400,
                details=[
                    {"issue": "status", "description": f"Current status is {item_obj.status}, expected UNCLAIMED"}
                ],
            )

        # Get batch for currency
        batch = await session.execute(
            select(PayoutBatch).where(PayoutBatch.id == item_obj.batch_id)
        )
        batch_obj = batch.scalars().first()

        # Update item status to RETURNED
        item_obj.status = "RETURNED"
        item_obj.returned_virtual_at = virtual_now

        # Return funds to sender
        await PayoutService._update_account_balance(
            session, universe_id, batch_obj.currency_code, item_obj.amount + item_obj.fee
        )

        # Emit webhook
        await emit_event(
            session,
            "PAYMENT.PAYOUTS-ITEM.CANCELED",
            "payout_item",
            PayoutService._payout_item_to_dict(item_obj),
            f"Payout item {item_obj.id} cancelled",
            universe_id,
            virtual_now,
        )

        await session.flush()
        return PayoutService._payout_item_to_dict(item_obj)

    @staticmethod
    async def claim_payout_item(
        session: AsyncSession,
        universe_id: uuid.UUID,
        receiver_email: str,
        virtual_now: datetime,
    ) -> int:
        """Claim an UNCLAIMED payout item (register recipient).

        This marks the recipient as registered and converts matching UNCLAIMED items to SUCCESS.

        Args:
            session: Database session
            universe_id: Universe ID
            receiver_email: Recipient email
            virtual_now: Current virtual time

        Returns:
            Count of items claimed

        Raises:
            PayoutError: If item is already returned
        """
        # Get or create recipient
        recipient = await session.execute(
            select(PayoutRecipient).where(
                PayoutRecipient.universe_id == universe_id,
                PayoutRecipient.email == receiver_email,
            )
        )
        recipient_obj = recipient.scalars().first()
        if not recipient_obj:
            recipient_obj = PayoutRecipient(
                universe_id=universe_id,
                email=receiver_email,
                registered=True,
                registered_virtual_at=virtual_now,
            )
            session.add(recipient_obj)
        else:
            recipient_obj.registered = True
            recipient_obj.registered_virtual_at = virtual_now

        # Find all UNCLAIMED items for this recipient
        items = await session.execute(
            select(PayoutItem).where(
                PayoutItem.universe_id == universe_id,
                PayoutItem.receiver == receiver_email,
                PayoutItem.status == "UNCLAIMED",
            )
        )
        items_to_claim = items.scalars().all()

        claimed_count = 0
        for item in items_to_claim:
            # Check if still within 30 days (not scheduled for return yet or past return)
            if item.return_scheduled_virtual_at and item.return_scheduled_virtual_at <= virtual_now:
                # Already returned or scheduled for return
                continue

            # Claim the item
            item.status = "SUCCESS"
            item.processed_virtual_at = virtual_now
            item.return_scheduled_virtual_at = None  # Cancel the scheduled return
            claimed_count += 1

            # Emit webhook
            await emit_event(
                session,
                "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
                "payout_item",
                PayoutService._payout_item_to_dict(item),
                f"Payout item {item.id} claimed and succeeded",
                universe_id,
                virtual_now,
            )

        await session.flush()
        return claimed_count

    @staticmethod
    async def register_recipient(
        session: AsyncSession,
        universe_id: uuid.UUID,
        email: str,
        virtual_now: datetime,
    ) -> dict[str, Any]:
        """Register a payout recipient (control API).

        Args:
            session: Database session
            universe_id: Universe ID
            email: Recipient email
            virtual_now: Current virtual time

        Returns:
            Recipient dict
        """
        recipient = await session.execute(
            select(PayoutRecipient).where(
                PayoutRecipient.universe_id == universe_id,
                PayoutRecipient.email == email,
            )
        )
        recipient_obj = recipient.scalars().first()
        if not recipient_obj:
            recipient_obj = PayoutRecipient(
                universe_id=universe_id,
                email=email,
                registered=True,
                registered_virtual_at=virtual_now,
            )
            session.add(recipient_obj)
        else:
            recipient_obj.registered = True
            recipient_obj.registered_virtual_at = virtual_now

        await session.flush()
        return {
            "email": recipient_obj.email,
            "registered": recipient_obj.registered,
            "registered_at": recipient_obj.registered_virtual_at.isoformat() if recipient_obj.registered_virtual_at else None,
        }

    @staticmethod
    async def process_payout_batch(
        session: AsyncSession,
        universe_id: uuid.UUID,
        batch_id: str,
        scheduler_context: "SchedulerContext",
        virtual_now: datetime,
    ) -> None:
        """Process a payout batch (run via scheduled event).

        Transitions batch from PENDING to PROCESSING, processes items, then to SUCCESS/DENIED.

        Args:
            session: Database session
            universe_id: Universe ID
            batch_id: Batch ID
            scheduler_context: Scheduler context for scheduling return events
            virtual_now: Current virtual time
        """
        batch = await session.execute(
            select(PayoutBatch).where(
                PayoutBatch.id == batch_id,
                PayoutBatch.universe_id == universe_id,
            )
        )
        batch_obj = batch.scalars().first()
        if not batch_obj or batch_obj.status != "PENDING":
            return

        # Move to PROCESSING
        batch_obj.status = "PROCESSING"
        batch_obj.processed_virtual_at = virtual_now

        # Process each item
        items = await session.execute(
            select(PayoutItem).where(PayoutItem.batch_id == batch_id)
        )
        items_list = items.scalars().all()

        for item in items_list:
            await PayoutService._process_payout_item(
                session, universe_id, item, batch_obj, scheduler_context, virtual_now
            )

        # Update batch status based on item outcomes
        success_count = sum(1 for item in items_list if item.status == "SUCCESS")
        failed_count = sum(1 for item in items_list if item.status in ["FAILED", "BLOCKED", "DENIED"])
        unclaimed_count = sum(1 for item in items_list if item.status == "UNCLAIMED")

        batch_obj.successful_item_count = success_count
        batch_obj.failed_item_count = failed_count
        batch_obj.unclaimed_item_count = unclaimed_count

        # If all items succeeded, batch is SUCCESS; otherwise check if any unclaimed
        if failed_count > 0 or unclaimed_count > 0:
            batch_obj.status = "PROCESSING"  # Keep as PROCESSING until all items resolved
        else:
            batch_obj.status = "SUCCESS"

        # Emit batch event
        await emit_event(
            session,
            "PAYMENT.PAYOUTSBATCH.PROCESSING",
            "payout_batch",
            PayoutService._payout_batch_to_dict(batch_obj),
            f"Payout batch {batch_obj.id} processing",
            universe_id,
            virtual_now,
        )

        await session.flush()

    @staticmethod
    async def _process_payout_item(
        session: AsyncSession,
        universe_id: uuid.UUID,
        item: PayoutItem,
        batch: PayoutBatch,
        scheduler_context: "SchedulerContext",
        virtual_now: datetime,
    ) -> None:
        """Process an individual payout item based on recipient registration status.

        Args:
            session: Database session
            universe_id: Universe ID
            item: Payout item to process
            batch: Parent batch
            scheduler_context: Scheduler context
            virtual_now: Current virtual time
        """
        # Check for magic test domains
        receiver = item.receiver.lower()
        if receiver.endswith("@unregistered.test"):
            item.status = "UNCLAIMED"
        elif receiver.endswith("@blocked.test"):
            item.status = "BLOCKED"
        elif receiver.endswith("@failed.test"):
            item.status = "FAILED"
        elif receiver.endswith("@onhold.test"):
            item.status = "ONHOLD"
        else:
            # Check if recipient is registered
            recipient = await session.execute(
                select(PayoutRecipient).where(
                    PayoutRecipient.universe_id == universe_id,
                    PayoutRecipient.email == receiver,
                )
            )
            recipient_obj = recipient.scalars().first()

            if recipient_obj and recipient_obj.registered:
                item.status = "SUCCESS"
            else:
                item.status = "UNCLAIMED"

        item.processed_virtual_at = virtual_now

        # If UNCLAIMED, schedule auto-return after 30 days
        if item.status == "UNCLAIMED":
            return_at = virtual_now + timedelta(days=PayoutService.UNCLAIMED_RETURN_DAYS)
            item.return_scheduled_virtual_at = return_at

            # Schedule a return event
            scheduled_event = ScheduledEvent(
                universe_id=universe_id,
                due_at=return_at,
                kind="payout.return_unclaimed",
                payload={"item_id": item.id, "batch_id": batch.id},
            )
            session.add(scheduled_event)

        # Emit item event
        event_type_map = {
            "SUCCESS": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
            "UNCLAIMED": "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
            "BLOCKED": "PAYMENT.PAYOUTS-ITEM.BLOCKED",
            "FAILED": "PAYMENT.PAYOUTS-ITEM.FAILED",
            "ONHOLD": "PAYMENT.PAYOUTS-ITEM.HELD",
            "RETURNED": "PAYMENT.PAYOUTS-ITEM.RETURNED",
        }
        event_type = event_type_map.get(item.status, "PAYMENT.PAYOUTS-ITEM.PROCESSING")

        await emit_event(
            session,
            event_type,
            "payout_item",
            PayoutService._payout_item_to_dict(item),
            f"Payout item {item.id} status: {item.status}",
            universe_id,
            virtual_now,
        )

    @staticmethod
    async def return_unclaimed_item(
        session: AsyncSession,
        universe_id: uuid.UUID,
        item_id: str,
        virtual_now: datetime,
    ) -> None:
        """Return an unclaimed payout item (called by scheduled event handler).

        Args:
            session: Database session
            universe_id: Universe ID
            item_id: Item ID
            virtual_now: Current virtual time
        """
        item = await session.execute(
            select(PayoutItem).where(
                PayoutItem.id == item_id,
                PayoutItem.universe_id == universe_id,
            )
        )
        item_obj = item.scalars().first()
        if not item_obj or item_obj.status != "UNCLAIMED":
            return

        # Get batch for currency
        batch = await session.execute(
            select(PayoutBatch).where(PayoutBatch.id == item_obj.batch_id)
        )
        batch_obj = batch.scalars().first()

        # Mark as RETURNED
        item_obj.status = "RETURNED"
        item_obj.returned_virtual_at = virtual_now

        # Return funds to sender
        await PayoutService._update_account_balance(
            session, universe_id, batch_obj.currency_code, item_obj.amount + item_obj.fee
        )

        # Emit webhook
        await emit_event(
            session,
            "PAYMENT.PAYOUTS-ITEM.RETURNED",
            "payout_item",
            PayoutService._payout_item_to_dict(item_obj),
            f"Payout item {item_obj.id} returned after 30 days unclaimed",
            universe_id,
            virtual_now,
        )

        await session.flush()

    @staticmethod
    async def _update_account_balance(
        session: AsyncSession,
        universe_id: uuid.UUID,
        currency_code: str,
        amount_delta: Decimal,
    ) -> None:
        """Update merchant account balance.

        Args:
            session: Database session
            universe_id: Universe ID
            currency_code: Currency code
            amount_delta: Amount to add (positive) or subtract (negative)
        """
        balance = await session.execute(
            select(AccountBalance).where(
                AccountBalance.universe_id == universe_id,
                AccountBalance.currency_code == currency_code,
            )
        )
        balance_obj = balance.scalars().first()
        if not balance_obj:
            balance_obj = AccountBalance(
                universe_id=universe_id,
                currency_code=currency_code,
                available=Decimal("0.00"),
            )
            session.add(balance_obj)
            await session.flush()

        balance_obj.available += amount_delta

    @staticmethod
    def _payout_item_to_dict(item: PayoutItem) -> dict[str, Any]:
        """Convert PayoutItem to dict."""
        return {
            "payout_item_id": item.id,
            "transaction_id": item.id,
            "transaction_status": item.status,
            "payout_item_type": item.recipient_type,
            "amount_range": {"value": str(item.amount), "currency_code": "USD"},
            "amount": {"value": str(item.amount), "currency_code": "USD"},
            "fees": {"value": str(item.fee), "currency_code": "USD"},
            "payout_batch_id": item.batch_id,
            "sender_item_id": item.sender_item_id,
            "receiver_type": item.recipient_type,
            "receiver": item.receiver,
            "note": item.note,
            "time_processed": item.processed_virtual_at.isoformat() if item.processed_virtual_at else None,
            "links": [
                {"rel": "self", "href": f"/v1/payments/payouts-item/{item.id}"},
                {"rel": "up", "href": f"/v1/payments/payouts/{item.batch_id}"},
            ],
        }

    @staticmethod
    def _payout_batch_to_dict(batch: PayoutBatch) -> dict[str, Any]:
        """Convert PayoutBatch to dict."""
        return {
            "batch_header": {
                "payout_batch_id": batch.id,
                "batch_status": batch.status,
                "sender_batch_id": batch.sender_batch_id,
                "sender_batch_header": batch.sender_batch_header,
                "time_created": batch.created_virtual_at.isoformat(),
                "time_completed": batch.processed_virtual_at.isoformat() if batch.processed_virtual_at else None,
            },
            "payout_items": [],
            "links": [
                {"rel": "self", "href": f"/v1/payments/payouts/{batch.id}"},
            ],
        }
