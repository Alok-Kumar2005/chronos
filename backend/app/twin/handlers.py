"""Event handlers for scheduled events in Chronos."""

from app.twin.scheduler import SchedulerContext, handler
from app.twin.services.payouts import PayoutService
from app.twin.services.subscriptions import SubscriptionService


@handler("payout.process")
async def handle_payout_process(context: SchedulerContext) -> None:
    """Process a payout batch when it's time to transition from PENDING to PROCESSING.

    Payload: {"batch_id": "..."}
    """
    batch_id = context.event.payload.get("batch_id")
    if not batch_id:
        return

    await PayoutService.process_payout_batch(
        context.session,
        context.universe.id,
        batch_id,
        context,
        context.now,
    )


@handler("payout.return_unclaimed")
async def handle_payout_return_unclaimed(context: SchedulerContext) -> None:
    """Return an unclaimed payout item after 30 days.

    Payload: {"item_id": "...", "batch_id": "..."}
    """
    item_id = context.event.payload.get("item_id")
    if not item_id:
        return

    await PayoutService.return_unclaimed_item(
        context.session,
        context.universe.id,
        item_id,
        context.now,
    )


@handler("sub.bill")
async def handle_subscription_billing(context: SchedulerContext) -> None:
    """Process billing cycles for a subscription universe when virtual time advances.

    Payload: {} (empty - processes all due subscriptions)
    """
    await SubscriptionService.process_billing_cycles(
        context.session,
        context.universe.id,
    )
