"""Invariants oracle for Lumen Store - verifies correctness against twin's ground truth.

The oracle reads shop tables and the twin's ground truth through the database,
NOT through shop HTTP. This ensures the oracle is independent of the shop's code paths.
"""

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Capture,
    PayoutItem,
    Refund,
    ShopAppliedWebhookEvent,
    ShopCreator,
    ShopErrorLog,
    ShopLedgerEntry,
    ShopOrder,
    WebhookDelivery,
)

logger = logging.getLogger(__name__)


@dataclass
class InvariantResult:
    """Result of an invariant check."""

    invariant_id: str
    passed: bool
    evidence: dict[str, Any]  # Details for debugging failures


class InvariantsOracle:
    """Determines pass/fail for Lumen Store invariants."""

    def __init__(self, db: AsyncSession, universe_id: uuid.UUID):
        self.db = db
        self.universe_id = universe_id

    async def inv_1_no_shipped_without_completed_capture(self) -> InvariantResult:
        """INV-1: No shipped order without a COMPLETED capture in the twin.

        For each order with status='paid', there must be:
        1. A paypal_capture_id set
        2. A Capture record with that ID and status='COMPLETED'
        """
        # Find all paid/shipped shop orders
        result = await self.db.execute(
            select(ShopOrder).where(
                and_(
                    ShopOrder.universe_id == self.universe_id,
                    ShopOrder.status.in_(["shipped", "paid"]),
                )
            )
        )
        shop_orders = result.scalars().all()

        failures = []
        for order in shop_orders:
            if not order.paypal_capture_id:
                failures.append({
                    "shop_order_id": order.id,
                    "issue": "no paypal_capture_id set",
                })
                continue

            # Check if capture exists and is COMPLETED
            result = await self.db.execute(
                select(Capture).where(
                    and_(
                        Capture.universe_id == self.universe_id,
                        Capture.id == order.paypal_capture_id,
                    )
                )
            )
            capture = result.scalar_one_or_none()
            if not capture or capture.status != "COMPLETED":
                failures.append({
                    "shop_order_id": order.id,
                    "paypal_capture_id": order.paypal_capture_id,
                    "capture_status": capture.status if capture else None,
                    "issue": "capture not COMPLETED",
                })

        return InvariantResult(
            invariant_id="INV-1",
            passed=len(failures) == 0,
            evidence={
                "total_orders": len(shop_orders),
                "failures": failures,
            },
        )

    async def inv_2_shop_revenue_ledger_equals_twin_captures_minus_refunds(self) -> InvariantResult:
        """INV-2: Shop revenue ledger equals twin captures minus refunds.

        Sum of all shop ledger entries with account='shop_revenue'
        should equal sum of all COMPLETED captures minus refunds in the twin.
        """
        # Shop ledger revenue
        result = await self.db.execute(
            select(func.sum(ShopLedgerEntry.amount)).where(
                and_(
                    ShopLedgerEntry.universe_id == self.universe_id,
                    ShopLedgerEntry.account == "shop_revenue",
                )
            )
        )
        shop_revenue = result.scalar() or Decimal("0.00")

        # Twin captures (COMPLETED)
        result = await self.db.execute(
            select(func.sum(Capture.amount)).where(
                and_(
                    Capture.universe_id == self.universe_id,
                    Capture.status == "COMPLETED",
                )
            )
        )
        twin_captures = result.scalar() or Decimal("0.00")

        # Twin refunds (COMPLETED)
        result = await self.db.execute(
            select(func.sum(Refund.amount)).where(
                and_(
                    Refund.universe_id == self.universe_id,
                    Refund.status == "COMPLETED",
                )
            )
        )
        twin_refunds = result.scalar() or Decimal("0.00")

        expected_revenue = twin_captures - twin_refunds
        matches = shop_revenue == expected_revenue

        return InvariantResult(
            invariant_id="INV-2",
            passed=matches,
            evidence={
                "shop_revenue_ledger": str(shop_revenue),
                "twin_captures": str(twin_captures),
                "twin_refunds": str(twin_refunds),
                "expected": str(expected_revenue),
                "actual": str(shop_revenue),
                "match": matches,
            },
        )

    async def inv_3_creator_paid_only_if_payout_success(self) -> InvariantResult:
        """INV-3: A creator is 'paid' only if the twin payout item is SUCCESS.

        For each creator with payout_status='paid', there must be
        a PayoutItem with status='SUCCESS'.

        Also: no creator credit for RETURNED items (should go to platform).
        """
        # Find paid creators
        result = await self.db.execute(
            select(ShopCreator).where(
                and_(
                    ShopCreator.universe_id == self.universe_id,
                    ShopCreator.payout_status == "paid",
                )
            )
        )
        paid_creators = result.scalars().all()

        failures = []
        for creator in paid_creators:
            # Check if there's a SUCCESS payout item for this creator
            result = await self.db.execute(
                select(PayoutItem).where(
                    and_(
                        PayoutItem.universe_id == self.universe_id,
                        PayoutItem.receiver == creator.email,
                        PayoutItem.status == "SUCCESS",
                    )
                )
            )
            payout_item = result.scalar_one_or_none()
            if not payout_item:
                failures.append({
                    "creator_email": creator.email,
                    "issue": "marked paid but no SUCCESS payout item",
                })

        # Check for RETURNED items crediting creators (BUG B3)
        result = await self.db.execute(
            select(ShopLedgerEntry).where(
                and_(
                    ShopLedgerEntry.universe_id == self.universe_id,
                    ShopLedgerEntry.kind == "payout_returned",
                    ShopLedgerEntry.account.like("creator_%"),
                )
            )
        )
        returned_to_creator = result.scalars().all()

        if returned_to_creator:
            failures.append({
                "issue": "RETURNED payout items credited to creator instead of platform",
                "count": len(returned_to_creator),
            })

        return InvariantResult(
            invariant_id="INV-3",
            passed=len(failures) == 0,
            evidence={
                "total_paid_creators": len(paid_creators),
                "failures": failures,
            },
        )

    async def inv_4_every_webhook_event_applied_at_most_once(self) -> InvariantResult:
        """INV-4: Every webhook event ID is applied at most once.

        Check shop_applied_webhook_events table: each source_event_id should appear
        exactly once (or not at all).
        """
        # Count event applications (should be 0 or 1 for each event)
        result = await self.db.execute(
            select(ShopAppliedWebhookEvent.source_event_id, func.count()).where(
                ShopAppliedWebhookEvent.universe_id == self.universe_id
            ).group_by(ShopAppliedWebhookEvent.source_event_id)
        )
        event_counts = result.all()

        failures = []
        for event_id, count in event_counts:
            if count > 1:
                failures.append({
                    "source_event_id": event_id,
                    "application_count": count,
                    "issue": "webhook applied multiple times",
                })

        return InvariantResult(
            invariant_id="INV-4",
            passed=len(failures) == 0,
            evidence={
                "unique_webhook_events": len(event_counts),
                "failures": failures,
            },
        )

    async def inv_5_no_invalid_signature_webhook_accepted(self) -> InvariantResult:
        """INV-5: No webhook with an invalid signature was accepted.

        Check: if a webhook delivery has signature_tampered=true,
        the shop should NOT have applied that event.

        This reads from twin's WebhookDelivery records (source of truth for tampering)
        and shop's applied events.
        """
        # Find tampered deliveries for this universe
        result = await self.db.execute(
            select(WebhookDelivery).where(
                and_(
                    WebhookDelivery.universe_id == self.universe_id,
                    WebhookDelivery.signature_tampered,
                )
            )
        )
        tampered_deliveries = result.scalars().all()

        failures = []
        for delivery in tampered_deliveries:
            # Check if this event was applied
            result = await self.db.execute(
                select(ShopAppliedWebhookEvent).where(
                    and_(
                        ShopAppliedWebhookEvent.universe_id == self.universe_id,
                        ShopAppliedWebhookEvent.source_event_id == delivery.webhook_event_id,
                    )
                )
            )
            applied = result.scalar_one_or_none()

            if applied:
                failures.append({
                    "webhook_event_id": delivery.webhook_event_id,
                    "issue": "tampered webhook was accepted and applied",
                })

        return InvariantResult(
            invariant_id="INV-5",
            passed=len(failures) == 0,
            evidence={
                "tampered_deliveries": len(tampered_deliveries),
                "accepted_tampered": len(failures),
                "failures": failures,
            },
        )

    async def inv_6_fully_refunded_order_not_paid(self) -> InvariantResult:
        """INV-6: A fully refunded order is not in paid state.

        For each shop order, if the capture is fully refunded (refund amount >= capture amount),
        the order should NOT be in status='paid'.
        """
        # Find all paid shop orders
        result = await self.db.execute(
            select(ShopOrder).where(
                and_(
                    ShopOrder.universe_id == self.universe_id,
                    ShopOrder.status == "paid",
                )
            )
        )
        paid_orders = result.scalars().all()

        failures = []
        for order in paid_orders:
            if not order.paypal_capture_id:
                continue

            # Get the capture
            result = await self.db.execute(
                select(Capture).where(
                    and_(
                        Capture.universe_id == self.universe_id,
                        Capture.id == order.paypal_capture_id,
                    )
                )
            )
            capture = result.scalar_one_or_none()
            if not capture:
                continue

            # Sum refunds for this capture
            result = await self.db.execute(
                select(func.sum(Refund.amount)).where(
                    and_(
                        Refund.universe_id == self.universe_id,
                        Refund.capture_id == capture.id,
                        Refund.status == "COMPLETED",
                    )
                )
            )
            total_refunded = result.scalar() or Decimal("0.00")

            # If fully refunded, order should not be paid
            if total_refunded >= capture.amount:
                failures.append({
                    "shop_order_id": order.id,
                    "paypal_capture_id": capture.id,
                    "capture_amount": str(capture.amount),
                    "refunded_amount": str(total_refunded),
                    "issue": "fully refunded order still in paid state",
                })

        return InvariantResult(
            invariant_id="INV-6",
            passed=len(failures) == 0,
            evidence={
                "paid_orders": len(paid_orders),
                "failures": failures,
            },
        )

    async def inv_7_no_swallowed_paypal_errors(self) -> InvariantResult:
        """INV-7: No 401/4xx error from PayPal was swallowed silently.

        Check shop_error_logs: no error should have swallowed=true.
        The shop should log and propagate all PayPal errors.
        """
        result = await self.db.execute(
            select(ShopErrorLog).where(
                and_(
                    ShopErrorLog.universe_id == self.universe_id,
                    ShopErrorLog.swallowed,
                )
            )
        )
        swallowed_errors = result.scalars().all()

        failures = [
            {
                "operation": error.operation,
                "error_code": error.error_code,
                "error_message": error.error_message,
                "created_at": error.created_virtual_at.isoformat(),
            }
            for error in swallowed_errors
        ]

        return InvariantResult(
            invariant_id="INV-7",
            passed=len(failures) == 0,
            evidence={
                "swallowed_error_count": len(failures),
                "failures": failures,
            },
        )

    async def check_all(self) -> dict[str, InvariantResult]:
        """Run all invariants and return results."""
        results = {}
        results["INV-1"] = await self.inv_1_no_shipped_without_completed_capture()
        results["INV-2"] = await self.inv_2_shop_revenue_ledger_equals_twin_captures_minus_refunds()
        results["INV-3"] = await self.inv_3_creator_paid_only_if_payout_success()
        results["INV-4"] = await self.inv_4_every_webhook_event_applied_at_most_once()
        results["INV-5"] = await self.inv_5_no_invalid_signature_webhook_accepted()
        results["INV-6"] = await self.inv_6_fully_refunded_order_not_paid()
        results["INV-7"] = await self.inv_7_no_swallowed_paypal_errors()
        return results
